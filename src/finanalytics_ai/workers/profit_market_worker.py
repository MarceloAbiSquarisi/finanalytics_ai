"""
workers/profit_market_worker.py

Worker de Market Data em tempo real via ProfitDLL64 (Nelogica).

Fluxo:
  ProfitDLL (ConnectorThread) → asyncio.Queue → handlers:
    1. TimescaleTickWriter  → fintz_cotacoes_ts (COPY rápido)
    2. EventPublisher       → PRICE_TICK_RECEIVED (pipeline de eventos)
    3. AlertService hook    → avalia alertas de preço em tempo real

Configuração via .env:
  PROFIT_DLL_PATH=C:/Nelogica/ProfitDLL64.dll
  PROFIT_ACTIVATION_KEY=...
  PROFIT_USERNAME=...
  PROFIT_PASSWORD=...
  PROFIT_TICKERS=PETR4,VALE3,WINFUT,WDOFUT,ITUB4
  PROFIT_EXCHANGE=B   (padrão B3 Bovespa)

Execução:
  uv run python -m finanalytics_ai.workers.profit_market_worker

Restrições:
  - Somente Windows (ProfitDLL64.dll é WinDLL)
  - Callbacks não devem fazer I/O — despachados para asyncio.Queue
  - DLL não deve ser chamada dentro de callbacks
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any

from finanalytics_ai.config import get_settings
from finanalytics_ai.container import bootstrap, build_engine, build_session_factory
from finanalytics_ai.observability.logging import get_logger

log = get_logger(__name__)


# ── Factory de client ─────────────────────────────────────────────────────────

def build_profit_client(settings: Any) -> Any:
    """
    Constrói ProfitDLLClient (Windows) ou NoOpProfitClient (outros SOs).
    Permite rodar o worker em qualquer plataforma sem crashar.
    """
    if sys.platform != "win32":
        log.warning("profit_market_worker.noop_mode", reason="Não é Windows")
        from finanalytics_ai.infrastructure.market_data.profit_dll.noop_client import NoOpProfitClient
        return NoOpProfitClient()

    dll_path = getattr(settings, "profit_dll_path", "") or os.getenv("PROFIT_DLL_PATH", "")
    if not dll_path:
        log.warning("profit_market_worker.noop_mode", reason="PROFIT_DLL_PATH não configurado")
        from finanalytics_ai.infrastructure.market_data.profit_dll.noop_client import NoOpProfitClient
        return NoOpProfitClient()

    from finanalytics_ai.infrastructure.market_data.profit_dll.client import ProfitDLLClient
    return ProfitDLLClient(
        dll_path=dll_path,
        activation_key=getattr(settings, "profit_activation_key", "") or os.getenv("PROFIT_ACTIVATION_KEY", ""),
        username=getattr(settings, "profit_username", "") or os.getenv("PROFIT_USERNAME", ""),
        password=getattr(settings, "profit_password", "") or os.getenv("PROFIT_PASSWORD", ""),
    )


# ── Tick handlers ─────────────────────────────────────────────────────────────

class TickToTimescaleHandler:
    """
    Grava ticks de preço em tempo real no TimescaleDB.
    Usa COPY protocol via asyncpg para máximo throughput.

    Estratégia de buffer:
      Acumula ticks por até FLUSH_INTERVAL_S segundos ou MAX_BUFFER ticks,
      então faz COPY em lote — evita uma transação por tick.
    """

    FLUSH_INTERVAL_S = 0.5   # flush a cada 500ms
    MAX_BUFFER       = 500   # ou a cada 500 ticks

    def __init__(self, ts_pool: Any) -> None:
        self._pool = ts_pool
        self._buffer: list = []
        self._last_flush = time.monotonic()
        self._lock = asyncio.Lock()

    async def handle_tick(self, tick: Any) -> None:
        """Recebe um tick e o adiciona ao buffer."""
        from finanalytics_ai.infrastructure.market_data.profit_dll.client import PriceTick
        if not isinstance(tick, PriceTick):
            return

        async with self._lock:
            self._buffer.append(tick)
            now = time.monotonic()
            should_flush = (
                len(self._buffer) >= self.MAX_BUFFER or
                (now - self._last_flush) >= self.FLUSH_INTERVAL_S
            )
            if should_flush:
                await self._flush()

    async def _flush(self) -> None:
        """Faz COPY do buffer para o TimescaleDB."""
        if not self._buffer:
            return

        ticks = self._buffer[:]
        self._buffer.clear()
        self._last_flush = time.monotonic()

        records = [
            (
                datetime.now(tz=timezone.utc),  # time
                t.ticker,                        # ticker
                t.price,                         # preco_fechamento
                t.price,                         # preco_fechamento_ajustado
                t.price,                         # preco_abertura (melhor estimativa)
                t.price,                         # preco_minimo
                t.price,                         # preco_maximo
                float(t.volume),                 # volume_negociado
                t.quantity,                      # quantidade_negocios
            )
            for t in ticks
        ]

        try:
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    temp = "_profit_tick_import"
                    await conn.execute(f"""
                        CREATE TEMP TABLE {temp} (
                            time                    TIMESTAMPTZ,
                            ticker                  VARCHAR(20),
                            preco_fechamento        NUMERIC(24,4),
                            preco_fechamento_ajustado NUMERIC(24,4),
                            preco_abertura          NUMERIC(24,4),
                            preco_minimo            NUMERIC(24,4),
                            preco_maximo            NUMERIC(24,4),
                            volume_negociado        NUMERIC(24,2),
                            quantidade_negocios     INTEGER
                        ) ON COMMIT DROP
                    """)
                    await conn.copy_records_to_table(
                        temp, records=records,
                        columns=["time","ticker","preco_fechamento","preco_fechamento_ajustado",
                                 "preco_abertura","preco_minimo","preco_maximo",
                                 "volume_negociado","quantidade_negocios"],
                    )
                    result = await conn.execute(f"""
                        INSERT INTO fintz_cotacoes_ts
                            (time, ticker, preco_fechamento, preco_fechamento_ajustado,
                             preco_abertura, preco_minimo, preco_maximo,
                             volume_negociado, quantidade_negocios)
                        SELECT time, ticker, preco_fechamento, preco_fechamento_ajustado,
                               preco_abertura, preco_minimo, preco_maximo,
                               volume_negociado, quantidade_negocios
                        FROM {temp}
                        ON CONFLICT (time, ticker) DO NOTHING
                    """)
                    inserted = int(result.split()[-1])

            log.debug(
                "profit_tick.flushed",
                ticks=len(ticks),
                inserted=inserted,
            )
        except Exception as exc:
            log.error("profit_tick.flush_failed", error=str(exc), ticks=len(ticks))


class TickToAlertHandler:
    """
    Avalia alertas de preço em tempo real via AlertService.
    Usa debounce por ticker (1 avaliação por segundo por ticker).
    """

    DEBOUNCE_S = 1.0

    def __init__(self, alert_service: Any | None) -> None:
        self._service = alert_service
        self._last_eval: dict[str, float] = {}

    async def handle_tick(self, tick: Any) -> None:
        from finanalytics_ai.infrastructure.market_data.profit_dll.client import PriceTick
        if not isinstance(tick, PriceTick) or self._service is None:
            return

        now = time.monotonic()
        last = self._last_eval.get(tick.ticker, 0)
        if (now - last) < self.DEBOUNCE_S:
            return

        self._last_eval[tick.ticker] = now
        try:
            triggered = await self._service.evaluate_price(tick.ticker, tick.price)
            if triggered:
                log.info(
                    "profit_tick.alert_triggered",
                    ticker=tick.ticker,
                    price=tick.price,
                    count=triggered,
                )
        except Exception as exc:
            log.warning("profit_tick.alert_error", error=str(exc))


# ── Worker principal ──────────────────────────────────────────────────────────

async def run_market_worker(stop_event: asyncio.Event, settings: Any) -> None:
    """
    Loop principal do worker de market data.

    1. Conecta ao TimescaleDB
    2. Inicializa ProfitDLLClient
    3. Aguarda conexão
    4. Subscreve tickers
    5. Processa ticks até stop_event
    """
    # Tickers a assinar
    tickers_env = os.getenv("PROFIT_TICKERS", "PETR4,VALE3,ITUB4,BBDC4,WINFUT")
    tickers = [t.strip() for t in tickers_env.split(",") if t.strip()]
    exchange = os.getenv("PROFIT_EXCHANGE", "B")

    log.info("profit_market_worker.starting", tickers=tickers, exchange=exchange)

    # Conecta TimescaleDB
    ts_pool = None
    try:
        import asyncpg
        ts_dsn = (
            getattr(settings, "timescale_url", None) or
            os.getenv("TIMESCALE_URL", "")
        )
        if ts_dsn:
            ts_dsn = str(ts_dsn).replace("postgresql+asyncpg://", "postgresql://")
            ts_pool = await asyncpg.create_pool(
                ts_dsn, min_size=2, max_size=6, statement_cache_size=0
            )
            log.info("profit_market_worker.timescale_connected")
        else:
            log.warning("profit_market_worker.timescale_disabled", reason="TIMESCALE_URL não configurado")
    except Exception as exc:
        log.warning("profit_market_worker.timescale_failed", error=str(exc))

    # Handlers
    ts_handler = TickToTimescaleHandler(ts_pool) if ts_pool else None
    alert_handler = TickToAlertHandler(alert_service=None)  # AlertService injetado futuramente

    # Cliente Profit
    client = build_profit_client(settings)

    async def on_tick(tick: Any) -> None:
        if ts_handler:
            await ts_handler.handle_tick(tick)
        await alert_handler.handle_tick(tick)

    client.add_tick_handler(on_tick)

    # Inicia
    loop = asyncio.get_event_loop()
    await client.start(loop)

    # Aguarda conexão (somente se for cliente real)
    if sys.platform == "win32" and os.getenv("PROFIT_DLL_PATH"):
        connected = await client.wait_connected(timeout=30.0)
        if not connected:
            log.error("profit_market_worker.connect_failed")
            await client.stop()
            if ts_pool:
                await ts_pool.close()
            return

    # Subscreve tickers
    await client.subscribe_tickers(tickers, exchange=exchange)

    log.info("profit_market_worker.running", tickers=tickers)

    # Aguarda stop_event
    await stop_event.wait()

    # Shutdown
    log.info("profit_market_worker.stopping")
    await client.stop()
    if ts_pool:
        # Flush final
        if ts_handler:
            async with ts_handler._lock:
                await ts_handler._flush()
        await ts_pool.close()
    log.info("profit_market_worker.stopped")


def main() -> None:
    settings = get_settings()
    bootstrap(settings)

    stop_event = asyncio.Event()

    def _handle_signal(sig: int, _: Any) -> None:
        log.info("profit_market_worker.signal", signal=sig)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    asyncio.run(run_market_worker(stop_event, settings))


if __name__ == "__main__":
    main()
