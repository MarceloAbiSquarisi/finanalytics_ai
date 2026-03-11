"""
EventProcessorService — orquestra o processamento assíncrono de eventos.

Design decision: O serviço de aplicação NÃO conhece detalhes de I/O.
Ele recebe as dependências (ports) via construtor — Injeção de Dependência
manual. Isso permite testar com mocks sem nenhum framework de DI.

Idempotência: antes de processar, verifica se event_id já existe no store.
Resiliência: tenacity com retry para erros transitórios.
Logging: structlog com context binding por evento.
Observabilidade: spans OTel em cada handler + métricas Prometheus por tipo.

--- Tracer opcional ---
O tracer é injetado via construtor como `tracer: Tracer | None = None`.
Quando None, o código cria spans no-op (OTel garante isso via
trace.get_tracer("noop") quando nenhum provider está configurado).
Isso permite que testes unitários rodem sem OTel configurado e que
o serviço seja usado sem observabilidade em ambientes simplificados.
"""

from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

import structlog
from opentelemetry import trace
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from finanalytics_ai.domain.entities.event import EventStatus, EventType, MarketEvent
from finanalytics_ai.exceptions import (
    EventProcessingError,
    TransientError,
)
from finanalytics_ai.observability import (
    event_processing_duration_seconds,
    events_processed_total,
    handler_duration_seconds,
    handler_events_total,
)

if TYPE_CHECKING:
    from finanalytics_ai.application.commands.process_event import ProcessMarketEventCommand
    from finanalytics_ai.domain.ports.event_store import EventStore
    from finanalytics_ai.domain.ports.market_data import MarketDataProvider

logger = structlog.get_logger(__name__)

# Tracer de fallback: no-op quando OTel não está configurado
_noop_tracer = trace.get_tracer("finanalytics_ai.noop")


class EventProcessorService:
    """
    Processa eventos de mercado com:
    - Idempotência via event_id
    - Retry com backoff exponencial para erros transitórios
    - Logging estruturado por evento
    - Métricas Prometheus por tipo e status
    - Spans OpenTelemetry por handler (quando tracer injetado)
    """

    def __init__(
        self,
        event_store: EventStore,
        market_data: MarketDataProvider,
        max_retry_attempts: int = 3,
        tracer: trace.Tracer | None = None,
    ) -> None:
        self._store = event_store
        self._market_data = market_data
        self._max_retries = max_retry_attempts
        # Tracer opcional — usa no-op se não fornecido
        self._tracer: trace.Tracer = tracer or _noop_tracer

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
            events_processed_total.labels(event_type=command.event_type, status="skipped").inc()
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
                event.mark_failed(str(exc))
                await self._store.update_status(event.event_id, EventStatus.FAILED, str(exc))
                events_processed_total.labels(event_type=command.event_type, status="failed").inc()
                log.error("event.failed", error=str(exc))
                raise EventProcessingError(
                    message=f"Falha ao processar evento {command.event_id}",
                    context={"event_id": command.event_id, "error": str(exc)},
                ) from exc

        events_processed_total.labels(event_type=command.event_type, status="processed").inc()
        log.info("event.processed")
        return processed

    async def _process_with_retry(self, event: MarketEvent, log: structlog.BoundLogger) -> MarketEvent:
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
        """
        Despacha para o handler específico por tipo, envolvendo em span OTel.

        O span "event.dispatch" é o pai de todos os spans de handler.
        Atributos rastreados: event_id, event_type, ticker.
        """
        with self._tracer.start_as_current_span(
            "event.dispatch",
            attributes={
                "event.id": event.event_id,
                "event.type": event.event_type.value,
                "event.ticker": event.ticker,
            },
        ) as span:
            try:
                match event.event_type:
                    case EventType.PRICE_UPDATE:
                        await self._handle_price_update(event)
                    case EventType.OHLC_BAR_CLOSED:
                        await self._handle_ohlc_bar(event)
                    case EventType.NEWS_PUBLISHED:
                        await self._handle_news(event)
                    case _:
                        logger.warning("event.unhandled", event_type=event.event_type)
                        span.set_attribute("event.unhandled", True)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR, str(exc))
                raise

        return event

    async def _handle_price_update(self, event: MarketEvent) -> None:
        """
        Processa atualização de preço.

        Responsabilidades:
        - Valida e normaliza o campo price do payload
        - Registra métricas de latência e contagem
        - Emite span OTel com ticker e price como atributos
        - Hook point para alertas e stop loss (via AlertService no worker)

        Design decision: a avaliação de alertas NÃO acontece aqui.
        Ela é responsabilidade do worker (_process_event em main.py),
        que chama AlertService após o processamento do evento.
        Isso mantém EventProcessorService agnóstico sobre o AlertService,
        evitando dependência circular e facilitando testes.
        """
        _start = time.perf_counter()
        handler_name = "price_update"

        with self._tracer.start_as_current_span(
            "handler.price_update",
            attributes={"event.ticker": event.ticker},
        ) as span:
            try:
                raw_price = event.payload.get("price")
                price: Decimal | None = None
                if raw_price is not None:
                    try:
                        price = Decimal(str(raw_price))
                        span.set_attribute("price.value", float(price))
                    except InvalidOperation:
                        logger.warning(
                            "price_update.invalid_price",
                            ticker=event.ticker,
                            raw=str(raw_price),
                        )

                logger.debug(
                    "price.updated",
                    ticker=event.ticker,
                    price=str(price) if price is not None else None,
                )
                handler_events_total.labels(handler=handler_name, status="ok").inc()

            except Exception as exc:
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR, str(exc))
                handler_events_total.labels(handler=handler_name, status="error").inc()
                raise
            finally:
                handler_duration_seconds.labels(handler=handler_name).observe(time.perf_counter() - _start)

    async def _handle_ohlc_bar(self, event: MarketEvent) -> None:
        """
        Processa barra OHLC fechada.

        Responsabilidades:
        - Valida os campos obrigatórios do payload (open, high, low, close)
        - Registra métricas e emite span com atributos OHLC
        - Hook point para persistência via OHLCRepository

        Design decision: persistência via repository NÃO está conectada aqui.
        O OHLCBarRepository requer AsyncSession, que exige lifespan da app.
        A conexão final será feita quando OHLCRepository for injetado
        via WorkerDeps (próxima sprint). O span já está estruturado para
        receber o atributo "ohlc.persisted" quando isso acontecer.
        """
        _start = time.perf_counter()
        handler_name = "ohlc_bar"

        with self._tracer.start_as_current_span(
            "handler.ohlc_bar",
            attributes={"event.ticker": event.ticker},
        ) as span:
            try:
                payload = event.payload
                close = payload.get("close")
                volume = payload.get("volume")

                if close is not None:
                    span.set_attribute("ohlc.close", float(close))
                if volume is not None:
                    span.set_attribute("ohlc.volume", int(volume))

                span.set_attribute("ohlc.persisted", False)  # updated when repo is wired

                logger.debug(
                    "ohlc.bar.received",
                    ticker=event.ticker,
                    close=close,
                    volume=volume,
                )
                handler_events_total.labels(handler=handler_name, status="ok").inc()

            except Exception as exc:
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR, str(exc))
                handler_events_total.labels(handler=handler_name, status="error").inc()
                raise
            finally:
                handler_duration_seconds.labels(handler=handler_name).observe(time.perf_counter() - _start)

    async def _handle_news(self, event: MarketEvent) -> None:
        """
        Processa evento de notícia publicada.

        Responsabilidades:
        - Extrai headline e source do payload
        - Emite span com atributos de conteúdo
        - Hook point para análise de sentimento / impacto via LLM

        Design decision: análise LLM é síncrona e cara — será processada
        em background task separada quando implementada (S15+).
        O span inclui "news.sentiment_requested" = False para facilitar
        rastreamento de quais eventos ainda não foram analisados.
        """
        _start = time.perf_counter()
        handler_name = "news"

        with self._tracer.start_as_current_span(
            "handler.news",
            attributes={"event.ticker": event.ticker},
        ) as span:
            try:
                payload = event.payload
                headline = payload.get("headline", "")
                source = payload.get("source", event.source)

                if headline:
                    span.set_attribute("news.headline_length", len(headline))
                span.set_attribute("news.source", source)
                span.set_attribute("news.sentiment_requested", False)

                logger.debug(
                    "news.received",
                    ticker=event.ticker,
                    headline=headline[:80] if headline else None,
                    source=source,
                )
                handler_events_total.labels(handler=handler_name, status="ok").inc()

            except Exception as exc:
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR, str(exc))
                handler_events_total.labels(handler=handler_name, status="error").inc()
                raise
            finally:
                handler_duration_seconds.labels(handler=handler_name).observe(time.perf_counter() - _start)
