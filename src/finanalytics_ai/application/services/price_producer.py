"""
BrapiPriceProducer — coleta preços reais da BRAPI e publica no Kafka.

Fluxo por ciclo:
  1. Para cada ticker na lista configurada:
     a. GET /quote/{ticker} via BrapiClient (com retry automático)
     b. Monta MarketEvent do tipo PRICE_UPDATE
     c. Publica no tópico price-updates via KafkaMarketEventProducer
  2. Aguarda producer_poll_interval_seconds
  3. Repete indefinidamente até receber sinal de parada

Design decisions:

  Intervalo vs. streaming:
    BRAPI é uma API REST — não há WebSocket. O polling é o único modo.
    30s é o padrão; tokens pagos do BRAPI têm rate limit maior e
    permitem intervalos menores (ex: 5s).

  Sequencial vs. paralelo:
    Tickers são consultados com asyncio.gather() — paralelo mas
    com semáforo (MAX_CONCURRENT=3) para respeitar rate limit da BRAPI.
    Token gratuito: ~5 req/s. Semáforo protege contra ban.

  Graceful shutdown:
    asyncio.Event _stop_event permite parada limpa sem CancelledError.
    O producer Kafka é fechado no finally — garante flush de mensagens
    pendentes antes de sair.

  Partial failure:
    Erros em tickers individuais são logados e ignorados — o loop
    continua para os demais. Evita que uma ação com problema derrube
    toda a coleta.

  Métricas:
    Contadores de sucesso/erro por ticker para observabilidade.
    Visíveis em /api/v1/producer/status.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog

from finanalytics_ai.domain.entities.event import EventStatus, EventType, MarketEvent
from finanalytics_ai.exceptions import MarketDataUnavailableError, TransientError

if TYPE_CHECKING:
    from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient
    from finanalytics_ai.infrastructure.queue.kafka_adapter import KafkaMarketEventProducer

logger = structlog.get_logger(__name__)

MAX_CONCURRENT = 3  # semáforo: máx de requests BRAPI simultâneos


class TickerStats:
    """Estatísticas por ticker para observabilidade."""

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        self.success_count = 0
        self.error_count = 0
        self.last_price: float | None = None
        self.last_change_pct: float | None = None
        self.last_updated: datetime | None = None
        self.last_error: str | None = None

    def record_success(self, price: float, change_pct: float | None) -> None:
        self.success_count += 1
        self.last_price = price
        self.last_change_pct = change_pct
        self.last_updated = datetime.utcnow()
        self.last_error = None

    def record_error(self, error: str) -> None:
        self.error_count += 1
        self.last_error = error
        self.last_updated = datetime.utcnow()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "last_price": self.last_price,
            "change_pct": self.last_change_pct,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "healthy": self.error_count == 0 or self.success_count > self.error_count,
        }


class BrapiPriceProducer:
    """
    Serviço de produção de preços reais: BRAPI → Kafka.

    Ciclo de vida gerenciado pelo lifespan da app FastAPI.
    Injetado como singleton — não deve ser instanciado mais de uma vez.
    """

    def __init__(
        self,
        tickers: list[str],
        poll_interval: float,
        brapi_client: BrapiClient,
        kafka_producer: KafkaMarketEventProducer,
    ) -> None:
        self._tickers = [t.upper().strip() for t in tickers if t.strip()]
        self._interval = poll_interval
        self._brapi = brapi_client
        self._kafka = kafka_producer
        self._stop_event = asyncio.Event()
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        self._cycle_count = 0
        self._running = False
        self._stats: dict[str, TickerStats] = {t: TickerStats(t) for t in self._tickers}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Inicia o producer Kafka (não inicia o loop — use run())."""
        await self._kafka.start()
        self._running = True
        logger.info(
            "price_producer.started",
            tickers=self._tickers,
            interval=self._interval,
        )

    async def stop(self) -> None:
        """Para o loop de polling graciosamente."""
        self._stop_event.set()
        self._running = False
        await self._kafka.stop()
        logger.info("price_producer.stopped", cycles=self._cycle_count)

    async def run(self) -> None:
        """
        Loop principal — roda até stop() ser chamado.
        Deve ser executado como asyncio.Task no lifespan.
        """
        # Primeira coleta imediata (não espera o intervalo inicial)
        await self._collect_all()

        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._interval,
                )
                break  # stop_event foi setado
            except TimeoutError:
                pass  # timeout normal — faz a próxima coleta

            await self._collect_all()

    # ── Coleta ────────────────────────────────────────────────────────────────

    async def _collect_all(self) -> None:
        """Coleta todos os tickers em paralelo (com semáforo)."""
        self._cycle_count += 1
        log = logger.bind(cycle=self._cycle_count)
        log.info("price_producer.cycle.start", tickers=len(self._tickers))

        tasks = [self._collect_ticker(ticker) for ticker in self._tickers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success = sum(1 for r in results if r is True)
        errors = len(results) - success
        log.info(
            "price_producer.cycle.done",
            success=success,
            errors=errors,
            next_in_seconds=self._interval,
        )

    async def _collect_ticker(self, ticker: str) -> bool:
        """
        Coleta preço de um ticker e publica no Kafka.
        Retorna True em sucesso, False em falha.
        """
        async with self._semaphore:
            try:
                data = await self._brapi.get_quote_full(ticker)  # type: ignore[arg-type]

                price = data.get("price")
                change_pct = data.get("change_pct")
                volume = data.get("volume")
                name = data.get("name", "")

                if price is None:
                    raise MarketDataUnavailableError(
                        message=f"Preço nulo para {ticker}",
                        context={"ticker": ticker},
                    )

                event = MarketEvent(
                    event_id=str(uuid.uuid4()),
                    event_type=EventType.PRICE_UPDATE,
                    ticker=ticker,
                    payload={
                        "price": float(price),
                        "change_pct": float(change_pct) if change_pct is not None else None,
                        "volume": int(volume) if volume else None,
                        "name": name,
                        "source": "brapi",
                    },
                    source="brapi-producer",
                    occurred_at=datetime.utcnow(),
                    status=EventStatus.PENDING,
                )

                await self._kafka.publish(event)
                self._stats[ticker].record_success(
                    float(price), float(change_pct) if change_pct else None
                )

                logger.debug(
                    "price_producer.published",
                    ticker=ticker,
                    price=price,
                    change_pct=change_pct,
                )
                return True

            except (MarketDataUnavailableError, TransientError) as exc:
                self._stats[ticker].record_error(str(exc))
                logger.warning(
                    "price_producer.fetch_failed",
                    ticker=ticker,
                    error=str(exc),
                )
                return False

            except Exception as exc:
                self._stats[ticker].record_error(str(exc))
                logger.error(
                    "price_producer.unexpected_error",
                    ticker=ticker,
                    error=str(exc),
                )
                return False

    # ── Status / Observabilidade ──────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running and not self._stop_event.is_set()

    @property
    def tickers(self) -> list[str]:
        return list(self._tickers)

    def get_status(self) -> dict[str, Any]:
        return {
            "running": self.is_running,
            "cycle_count": self._cycle_count,
            "interval": self._interval,
            "tickers": [s.to_dict() for s in self._stats.values()],
            "ticker_count": len(self._tickers),
            "healthy_count": sum(1 for s in self._stats.values() if s.last_price is not None),
        }

    def add_ticker(self, ticker: str) -> bool:
        """Adiciona ticker ao pool sem reiniciar o producer."""
        t = ticker.upper().strip()
        if t in self._tickers:
            return False
        self._tickers.append(t)
        self._stats[t] = TickerStats(t)
        logger.info("price_producer.ticker_added", ticker=t)
        return True

    def remove_ticker(self, ticker: str) -> bool:
        """Remove ticker do pool sem reiniciar o producer."""
        t = ticker.upper().strip()
        if t not in self._tickers:
            return False
        self._tickers.remove(t)
        self._stats.pop(t, None)
        logger.info("price_producer.ticker_removed", ticker=t)
        return True
