"""
EventProcessorService — orquestra o processamento assíncrono de eventos.

Design decision: O serviço de aplicação NÃO conhece detalhes de I/O.
Ele recebe as dependências (ports) via construtor — Injeção de Dependência
manual. Isso permite testar com mocks sem nenhum framework de DI.

Idempotência: antes de processar, verifica se event_id já existe no store.
Resiliência: tenacity com retry para erros transitórios.
Logging: structlog com context binding por evento.
"""
from __future__ import annotations

import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from finanalytics_ai.application.commands.process_event import ProcessMarketEventCommand
from finanalytics_ai.domain.entities.event import EventStatus, EventType, MarketEvent
from finanalytics_ai.domain.ports.event_store import EventStore
from finanalytics_ai.domain.ports.market_data import MarketDataProvider
from finanalytics_ai.exceptions import (
    DuplicateEventError,
    EventProcessingError,
    TransientError,
)
from finanalytics_ai.observability import events_processed_total, event_processing_duration_seconds

logger = structlog.get_logger(__name__)


class EventProcessorService:
    """
    Processa eventos de mercado com:
    - Idempotência via event_id
    - Retry com backoff exponencial para erros transitórios
    - Logging estruturado por evento
    - Métricas Prometheus por tipo e status
    """

    def __init__(
        self,
        event_store: EventStore,
        market_data: MarketDataProvider,
        max_retry_attempts: int = 3,
    ) -> None:
        self._store = event_store
        self._market_data = market_data
        self._max_retries = max_retry_attempts

    async def process(self, command: ProcessMarketEventCommand) -> MarketEvent:
        """
        Ponto de entrada principal. Garante idempotência e resiliência.
        
        Returns: MarketEvent com status final (PROCESSED | SKIPPED | FAILED)
        Raises: EventProcessingError se esgotar as tentativas
        """
        log = logger.bind(
            event_id=command.event_id,
            event_type=command.event_type,
            ticker=command.ticker,
        )

        # ── 1. Idempotência ──────────────────────────────────────────────────
        if await self._store.exists(command.event_id):
            log.info("event.skipped.duplicate")
            events_processed_total.labels(
                event_type=command.event_type, status="skipped"
            ).inc()
            existing = await self._store.find_by_id(command.event_id)
            if existing is None:
                raise EventProcessingError(
                    message="Evento duplicado mas não encontrado no store",
                    context={"event_id": command.event_id},
                )
            return existing

        # ── 2. Persiste como PENDING ─────────────────────────────────────────
        event = MarketEvent(
            event_id=command.event_id,
            event_type=EventType(command.event_type),
            ticker=command.ticker,
            payload=command.payload,
            source=command.source,
        )
        await self._store.save(event)
        log.info("event.received")

        # ── 3. Processa com retry ────────────────────────────────────────────
        timer = event_processing_duration_seconds.labels(event_type=command.event_type)
        with timer.time():
            try:
                processed = await self._process_with_retry(event, log)
            except Exception as exc:
                failed = event.mark_failed(str(exc))
                await self._store.update_status(
                    event.event_id, EventStatus.FAILED, str(exc)
                )
                events_processed_total.labels(
                    event_type=command.event_type, status="failed"
                ).inc()
                log.error("event.failed", error=str(exc))
                raise EventProcessingError(
                    message=f"Falha ao processar evento {command.event_id}",
                    context={"event_id": command.event_id, "error": str(exc)},
                ) from exc

        events_processed_total.labels(
            event_type=command.event_type, status="processed"
        ).inc()
        log.info("event.processed")
        return processed

    async def _process_with_retry(
        self, event: MarketEvent, log: structlog.BoundLogger
    ) -> MarketEvent:
        """Retry com backoff exponencial apenas para TransientError."""
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type(TransientError),
            reraise=True,
        ):
            with attempt:
                await self._store.update_status(event.event_id, EventStatus.PROCESSING)
                result = await self._dispatch(event)
                processed = result.mark_processed()
                await self._store.update_status(event.event_id, EventStatus.PROCESSED)
                return processed

        # nunca atingido — tenacity reraise=True
        raise EventProcessingError(message="Retry esgotado", context={"event_id": event.event_id})

    async def _dispatch(self, event: MarketEvent) -> MarketEvent:
        """Despacha o evento para o handler específico por tipo."""
        match event.event_type:
            case EventType.PRICE_UPDATE:
                await self._handle_price_update(event)
            case EventType.OHLC_BAR_CLOSED:
                await self._handle_ohlc_bar(event)
            case EventType.NEWS_PUBLISHED:
                await self._handle_news(event)
            case _:
                logger.warning("event.unhandled", event_type=event.event_type)
        return event

    async def _handle_price_update(self, event: MarketEvent) -> None:
        """Processa atualização de preço. Hook para alertas e stop loss."""
        price = event.payload.get("price")
        logger.debug("price.updated", ticker=event.ticker, price=price)
        # TODO: avaliar regras de stop loss e alertas

    async def _handle_ohlc_bar(self, event: MarketEvent) -> None:
        """Armazena barra OHLC para uso em backtesting e análise."""
        logger.debug("ohlc.bar.received", ticker=event.ticker)
        # TODO: persistir OHLCBar via repository

    async def _handle_news(self, event: MarketEvent) -> None:
        """Encaminha notícia para análise de impacto via LLM."""
        logger.debug("news.received", ticker=event.ticker)
        # TODO: enviar para análise LLM assíncrona
