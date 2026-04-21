"""
application/services/ohlc_updater.py
──────────────────────────────────────
Servico de atualizacao diaria de barras OHLC no TimescaleDB.

Responsabilidades:
  1. Detectar quais tickers estao desatualizados (ultima barra > 1 dia atras)
  2. Buscar barras ausentes via market_client (BRAPI/Yahoo)
  3. Persistir no TimescaleDB via OHLCTimescaleRepo
  4. Rodar em loop diario (run_daily_loop) como background task da API

Design — run_daily_loop como asyncio.Task:
  O app.py faz asyncio.create_task(updater.run_daily_loop()).
  O loop dorme ate o proximo horario de atualizacao (18:30 BRT = apos fechamento B3)
  e roda a atualizacao. Se o TimescaleDB ficar indisponivel entre ciclos,
  o proximo ciclo tenta novamente sem crash — resiliente a falhas transitorias.

Tickers default: universo IBOV reduzido para nao sobrecarregar a API.
  Configuravel via OHLC_UPDATER_TICKERS no .env — permite override sem
  alterar codigo.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Horario de atualizacao diaria (18:30 UTC-3 = 21:30 UTC)
_UPDATE_HOUR_UTC = 21
_UPDATE_MINUTE_UTC = 30

# Tickers padrao — subset do IBOV para atualizacao diaria
_DEFAULT_TICKERS = [
    "PETR4",
    "VALE3",
    "ITUB4",
    "BBDC4",
    "BBAS3",
    "WEGE3",
    "ABEV3",
    "RENT3",
    "MGLU3",
    "LREN3",
    "IBOV",
]


class OHLCUpdaterService:
    """
    Servico de atualizacao incremental de barras OHLC diarias.

    Args:
        repo:          OHLCTimescaleRepo — persistencia no TimescaleDB
        market_client: adaptador de dados de mercado (BRAPI/Yahoo)
        tickers:       lista de tickers a manter atualizados
    """

    def __init__(
        self,
        repo: Any,  # OHLCTimescaleRepo
        market_client: Any,  # MarketDataClient
        tickers: list[str] | None = None,
    ) -> None:
        self._repo = repo
        self._client = market_client
        self._tickers = tickers or _DEFAULT_TICKERS
        self._running = False
        self._last_run: datetime | None = None

    async def run_daily_loop(self) -> None:
        """
        Loop infinito que atualiza barras OHLC uma vez por dia.
        Executa imediatamente na primeira vez (ao subir a API),
        depois aguarda ate o proximo horario configurado.
        """
        self._running = True
        log.info("ohlc_updater.loop.started", tickers=len(self._tickers))

        # Primeira execucao imediata (atualiza dados ao subir)
        await self._run_update_cycle()

        while self._running:
            next_run = self._next_run_time()
            wait_seconds = (next_run - datetime.now(tz=UTC)).total_seconds()
            wait_seconds = max(wait_seconds, 0)

            log.info(
                "ohlc_updater.sleeping",
                next_run=next_run.isoformat(),
                wait_hours=round(wait_seconds / 3600, 1),
            )

            await asyncio.sleep(wait_seconds)

            if self._running:
                await self._run_update_cycle()

    async def _run_update_cycle(self) -> None:
        """Executa um ciclo de atualizacao para todos os tickers."""
        log.info("ohlc_updater.cycle.start", tickers=self._tickers)
        updated = 0
        errors = 0

        for ticker in self._tickers:
            try:
                n = await self._update_ticker(ticker)
                if n > 0:
                    updated += 1
                    log.debug("ohlc_updater.ticker.updated", ticker=ticker, bars=n)
            except Exception as exc:
                errors += 1
                log.warning("ohlc_updater.ticker.error", ticker=ticker, error=str(exc))

        self._last_run = datetime.now(tz=UTC)
        log.info(
            "ohlc_updater.cycle.done", updated=updated, errors=errors, total=len(self._tickers)
        )

    async def _update_ticker(self, ticker: str) -> int:
        """
        Busca e persiste barras ausentes para um ticker.
        Retorna numero de barras inseridas (0 se ja atualizado).
        """
        # Verifica ultima barra armazenada
        last_date = await self._repo.get_last_date(ticker, timeframe="1d")
        today = datetime.now(tz=UTC).date()

        # Se a ultima barra for de hoje, nao precisa atualizar
        if last_date and last_date.date() >= today:
            return 0

        # Determina periodo de busca
        if last_date:
            days_back = (today - last_date.date()).days + 2  # +2 buffer
        else:
            days_back = 365  # primeira carga: 1 ano de historico

        days_back = min(days_back, 365)  # cap em 1 ano por request

        try:
            # Tenta via market_client (BRAPI/Yahoo)
            bars = await self._fetch_bars(ticker, days_back)
            if not bars:
                return 0
            return await self._repo.save_bars(bars)
        except Exception as exc:
            log.debug("ohlc_updater.fetch.failed", ticker=ticker, days=days_back, error=str(exc))
            return 0

    async def _fetch_bars(self, ticker: str, days: int) -> list[dict[str, Any]]:
        """
        Busca barras OHLC via market_client.
        Retorna lista vazia se o cliente nao suportar o ticker.
        """
        try:
            # market_client.get_ohlc retorna list[dict] com keys:
            # time (datetime), open, high, low, close, volume
            result = await self._client.get_ohlc(
                ticker=ticker,
                interval="1d",
                range_period=f"{days}d",
            )
            if not result:
                return []

            # Normaliza para formato do repositorio
            bars = []
            for item in result:
                if not all(k in item for k in ("open", "high", "low", "close")):
                    continue
                ts = item.get("time") or item.get("timestamp")
                if isinstance(ts, (int, float)):
                    ts = datetime.fromtimestamp(ts, tz=UTC)
                elif ts is None:
                    ts = datetime.now(tz=UTC)

                bars.append(
                    {
                        "time": ts,
                        "ticker": ticker,
                        "timeframe": "1d",
                        "open": item["open"],
                        "high": item["high"],
                        "low": item["low"],
                        "close": item["close"],
                        "volume": item.get("volume", 0),
                        "source": "market_client",
                    }
                )
            return bars

        except AttributeError:
            # market_client nao tem get_ohlc — retorna vazio silenciosamente
            return []

    @staticmethod
    def _next_run_time() -> datetime:
        """Calcula o proximo horario de execucao (18:30 BRT diariamente)."""
        now = datetime.now(tz=UTC)
        target = now.replace(
            hour=_UPDATE_HOUR_UTC,
            minute=_UPDATE_MINUTE_UTC,
            second=0,
            microsecond=0,
        )
        if target <= now:
            target += timedelta(days=1)
        return target

    async def stop(self) -> None:
        """Para o loop graciosamente."""
        self._running = False
        log.info("ohlc_updater.stopped")

    @property
    def last_run(self) -> datetime | None:
        return self._last_run

    @property
    def is_running(self) -> bool:
        return self._running
