"""
Application Service — EventProcessor.

Este é o coração do sistema. Orquestra:
  1. Verificação de idempotência
  2. Dispatch para a BusinessRule correta
  3. Persistência do resultado
  4. Retry com backoff exponencial para erros transitórios
  5. Dead-letter para erros permanentes

Injeção de dependência manual:
    Todas as dependências chegam pelo __init__. Sem globals, sem imports circulares.
    Testável sem banco de dados real (use repositório fake).

Por que não usar Celery/ARQ/Dramatiq?
    Para este domínio, o controle explícito da máquina de estados e retry logic
    justifica a implementação própria. Frameworks de filas adicionam complexidade
    operacional (broker, serialização, versionamento de tasks) que não é necessária
    quando o volume de eventos cabe em um asyncio.Semaphore de concorrência controlada.
    Se o volume crescer para >10k eventos/s, migrar para ARQ (Redis-backed) é trivial
    porque o contrato de BusinessRule não muda.
"""

from __future__ import annotations

import asyncio
import time
from typing import Sequence

import structlog

from finanalytics_ai.config import Settings
from finanalytics_ai.domain.events.entities import (
    Event,
    EventProcessingRecord,
    EventStatus,
    EventType,
)
from finanalytics_ai.domain.events.ports import (
    BusinessRule,
    EventRepository,
    ObservabilityPort,
)
from finanalytics_ai.exceptions import (
    ApplicationError,
    BusinessRuleError,
    EventAlreadyProcessedError,
    InfrastructureError,
    NoHandlerFoundError,
    TransientDatabaseError,
    TransientExternalServiceError,
)
from finanalytics_ai.observability.logging import get_logger
from finanalytics_ai.observability.metrics import trace_span

log: structlog.stdlib.BoundLogger = get_logger(__name__)


class EventProcessor:
    """Serviço de processamento assíncrono de eventos.

    Parâmetros injetados:
        repository: EventRepository — persistência (Postgres, in-memory para testes)
        rules: Sequence[BusinessRule] — regras de negócio registradas
        observability: ObservabilityPort — métricas e tracing
        settings: Settings — configurações do sistema
    """

    def __init__(
        self,
        repository: EventRepository,
        rules: Sequence[BusinessRule],
        observability: ObservabilityPort,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._observability = observability
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.event_processor_concurrency)

        # Constrói índice EventType → Rule para dispatch O(1)
        # Trade-off: múltiplas regras para o mesmo tipo causam ValueError no startup,
        # forçando o dev a ser explícito. Alternativa: lista de regras por tipo.
        self._rule_index: dict[EventType, BusinessRule] = {}
        for rule in rules:
            for event_type in rule.handles:
                if event_type in self._rule_index:
                    raise ValueError(
                        f"Conflito: duas regras registradas para {event_type}. "
                        f"Registre apenas uma BusinessRule por EventType."
                    )
                self._rule_index[event_type] = rule

        log.info(
            "event_processor_initialized",
            registered_types=[t.value for t in self._rule_index],
            concurrency=settings.event_processor_concurrency,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    async def process(self, event: Event) -> EventProcessingRecord:
        """Processa um único evento com idempotência e retry.

        Garante que:
        - Eventos COMPLETED não são reprocessados (idempotência).
        - Erros transitórios são retentados com backoff exponencial.
        - Erros permanentes movem o evento para dead-letter imediatamente.

        Returns:
            EventProcessingRecord com o estado final do processamento.

        Raises:
            EventAlreadyProcessedError: evento já foi completado com sucesso.
        """
        async with self._semaphore:
            return await self._process_with_retry(event)

    async def process_batch(self, events: list[Event]) -> list[EventProcessingRecord]:
        """Processa múltiplos eventos concorrentemente respeitando o semaphore."""
        tasks = [self.process(event) for event in events]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        records: list[EventProcessingRecord] = []
        for event, result in zip(events, results, strict=True):
            if isinstance(result, EventAlreadyProcessedError):
                log.info("event_skipped_already_processed", event_id=str(event.id))
            elif isinstance(result, Exception):
                log.error(
                    "event_batch_item_failed",
                    event_id=str(event.id),
                    error=str(result),
                )
            else:
                records.append(result)

        return records

    # ──────────────────────────────────────────────────────────────────────────
    # Internal machinery
    # ──────────────────────────────────────────────────────────────────────────

    async def _process_with_retry(self, event: Event) -> EventProcessingRecord:
        record = await self._get_or_create_record(event)

        if record.status == EventStatus.COMPLETED:
            raise EventAlreadyProcessedError(
                f"Evento {event.id} já foi processado com sucesso."
            )

        if record.status == EventStatus.DEAD_LETTER:
            log.warning(
                "event_in_dead_letter_skipped",
                event_id=str(event.id),
                last_error=record.last_error,
            )
            return record

        max_retries = self._settings.event_max_retries
        base_delay = self._settings.event_retry_base_delay

        while record.attempt < max_retries:
            record.mark_processing()
            await self._repository.upsert_processing_record(record)

            start = time.perf_counter()
            try:
                async with trace_span(
                    "process_event",
                    event_id=str(event.id),
                    event_type=event.event_type.value,
                ):
                    metadata = await self._dispatch(event)

                duration = time.perf_counter() - start
                record.mark_completed(metadata)
                await self._repository.upsert_processing_record(record)

                self._observability.record_event_processed(
                    event.event_type.value, "completed"
                )
                self._observability.record_processing_duration(
                    event.event_type.value, duration
                )

                log.info(
                    "event_processed_successfully",
                    event_id=str(event.id),
                    event_type=event.event_type.value,
                    attempt=record.attempt,
                    duration_s=round(duration, 3),
                )
                return record

            except BusinessRuleError as exc:
                # Erro permanente — não retry
                record.mark_failed(str(exc), max_retries=0)
                await self._repository.upsert_processing_record(record)
                self._observability.record_event_processed(
                    event.event_type.value, "dead_letter_business_rule"
                )
                log.error(
                    "event_business_rule_error",
                    event_id=str(event.id),
                    error=str(exc),
                )
                return record

            except (TransientDatabaseError, TransientExternalServiceError) as exc:
                # Erro transitório — retry com backoff exponencial
                self._observability.record_retry(
                    event.event_type.value, record.attempt
                )
                delay = base_delay * (2 ** (record.attempt - 1))
                log.warning(
                    "event_transient_error_retrying",
                    event_id=str(event.id),
                    attempt=record.attempt,
                    delay_s=delay,
                    error=str(exc),
                )
                record.mark_failed(str(exc), max_retries=max_retries)
                await self._repository.upsert_processing_record(record)

                if record.status != EventStatus.DEAD_LETTER:
                    await asyncio.sleep(delay)
                else:
                    break

            except (ApplicationError, InfrastructureError) as exc:
                # Outros erros não transitórios
                record.mark_failed(str(exc), max_retries=0)
                await self._repository.upsert_processing_record(record)
                self._observability.record_event_processed(
                    event.event_type.value, "dead_letter"
                )
                log.error(
                    "event_permanent_error",
                    event_id=str(event.id),
                    error=str(exc),
                    exc_info=True,
                )
                return record

        # Esgotou retries
        if record.status != EventStatus.DEAD_LETTER:
            record.mark_failed("Max retries exhausted", max_retries=0)
            await self._repository.upsert_processing_record(record)

        self._observability.record_event_processed(
            event.event_type.value, "dead_letter_max_retries"
        )
        return record

    async def _dispatch(self, event: Event) -> dict:
        """Rota o evento para a BusinessRule correta."""
        rule = self._rule_index.get(event.event_type)
        if rule is None:
            raise NoHandlerFoundError(
                f"Nenhuma BusinessRule registrada para {event.event_type!r}. "
                f"Registradas: {list(self._rule_index)}"
            )
        return await rule.apply(event)

    async def _get_or_create_record(self, event: Event) -> EventProcessingRecord:
        """Busca ou cria o registro de processamento (checkpoint de idempotência)."""
        existing = await self._repository.get_processing_record(event.id)
        if existing is not None:
            return existing

        record = EventProcessingRecord(
            event_id=event.id,
            status=EventStatus.PENDING,
        )
        await self._repository.save_event(event)
        await self._repository.upsert_processing_record(record)
        return record


# EventProcessorService: shim com API legada (handlers individuais + metricas + OTel)
# Importado de event_processor_service.py — NÃO é alias de EventProcessor

# ── EventProcessorService (compatibility shim) ──────────────────────────────
# API legada com handlers individuais por tipo de evento + metricas + OTel.
# Usada pelos testes test_observability_hooks.py e test_ohlc_handler.py.
# Coexiste com EventProcessor ate os testes serem migrados para a nova API.
"""
EventProcessorService — compatibility shim para a API legada.

Os testes em test_observability_hooks.py e test_ohlc_handler.py foram escritos
para uma versão anterior que tinha handlers específicos por tipo de evento com
métricas Prometheus e spans OTel individuais.

Esta classe reimplementa essa API sem alterar o EventProcessor atual.

Injeção:
    event_store   — repositório de eventos (mock em testes)
    market_data   — cliente de market data (mock em testes)
    tracer        — opentelemetry.trace.Tracer (None = no-op)
    ohlc_repo     — OHLCBarRepository (opcional)

Métodos:
    _handle_price_update(event) — handler de PRICE_UPDATE
    _handle_ohlc_bar(event)     — handler de OHLC_BAR_CLOSED
    _dispatch(event)            — span pai + despacha para handler correto
"""


import time
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from finanalytics_ai.domain.entities.event import EventType, MarketEvent
from finanalytics_ai.observability.logging import get_logger

log = get_logger(__name__)


def _try_decimal(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError):
        return None


class EventProcessorService:
    """
    Compatibility shim — API legada de EventProcessor com handlers individuais.

    Trade-off documentado: esta classe coexiste com o EventProcessor atual.
    Quando os testes forem migrados para a nova API, ela pode ser removida.
    """

    def __init__(
        self,
        event_store: Any,
        market_data: Any,
        tracer: Any = None,
        ohlc_repo: Any = None,
    ) -> None:
        self._event_store = event_store
        self._market_data = market_data
        self._tracer = tracer
        self._ohlc_repo = ohlc_repo

        # Métricas Prometheus (lazy — None se desabilitadas)
        self._handler_counter = None
        self._handler_duration = None
        self._init_metrics()

    def _init_metrics(self) -> None:
        try:
            from finanalytics_ai.observability import handler_events_total, handler_duration_seconds
            self._handler_counter = handler_events_total
            self._handler_duration = handler_duration_seconds
        except (ImportError, AttributeError):
            pass

    def _start_span(self, name: str, attributes: dict | None = None):
        """Cria span OTel ou contextmanager no-op."""
        import contextlib

        if self._tracer is None:
            return contextlib.nullcontext()

        span = self._tracer.start_span(name)
        if attributes:
            for k, v in attributes.items():
                span.set_attribute(k, v)
        return self._tracer.use_span(span, end_on_exit=True)

    def _record_metric(self, handler: str, status: str, duration: float) -> None:
        if self._handler_counter is not None:
            try:
                self._handler_counter.labels(handler=handler, status=status).inc()
            except Exception:
                pass
        if self._handler_duration is not None:
            try:
                self._handler_duration.labels(handler=handler).observe(duration)
            except Exception:
                pass

    # ── Handlers ──────────────────────────────────────────────────────────────

    async def _handle_price_update(self, event: MarketEvent) -> None:
        """Handler de PRICE_UPDATE com métricas Prometheus e span OTel."""
        t0 = time.perf_counter()
        ticker = event.ticker
        price = _try_decimal(event.payload.get("price"))

        if price is None:
            log.warning("event_processor_service.price_update.invalid_price",
                        ticker=ticker, payload=event.payload)

        # Span OTel
        if self._tracer is not None:
            try:
                with self._tracer.start_as_current_span("handler.price_update") as span:
                    span.set_attribute("event.ticker", ticker)
                    if price is not None:
                        span.set_attribute("price.value", float(price))
            except Exception:
                pass

        duration = time.perf_counter() - t0
        self._record_metric("price_update", "ok", duration)

    async def _handle_ohlc_bar(self, event: MarketEvent) -> None:
        """Handler de OHLC_BAR_CLOSED com métricas, span OTel e persistência."""
        from datetime import datetime, timezone, UTC
        t0 = time.perf_counter()
        p = event.payload
        persisted = False

        close = _try_decimal(p.get("close"))
        if close is None:
            log.warning("event_processor_service.ohlc_bar.missing_close",
                        ticker=event.ticker)
            self._record_metric("ohlc_bar", "ok", time.perf_counter() - t0)
            return

        # Persiste se repo injetado
        if self._ohlc_repo is not None:
            try:
                from finanalytics_ai.domain.entities.event import OHLCBar
                # Parseia timestamp
                ts_raw = p.get("timestamp")
                if ts_raw is None:
                    ts = event.occurred_at
                elif isinstance(ts_raw, (int, float)):
                    ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
                else:
                    ts = datetime.fromisoformat(str(ts_raw))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)

                bar = OHLCBar(
                    ticker=event.ticker,
                    timestamp=ts,
                    open=_try_decimal(p.get("open")) or close,
                    high=_try_decimal(p.get("high")) or close,
                    low=_try_decimal(p.get("low")) or close,
                    close=close,
                    volume=int(p.get("volume", 0) or 0),
                    timeframe=p.get("timeframe", "1m"),
                    source=event.source,
                )
                result = await self._ohlc_repo.upsert_bar(bar)
                persisted = result is not False
            except Exception as exc:
                log.warning("event_processor_service.ohlc_bar.persist_failed",
                            error=str(exc))

        # Span OTel
        if self._tracer is not None:
            try:
                with self._tracer.start_as_current_span("handler.ohlc_bar") as span:
                    span.set_attribute("event.ticker", event.ticker)
                    span.set_attribute("ohlc.close", float(close))
                    span.set_attribute("ohlc.volume", int(p.get("volume", 0) or 0))
                    span.set_attribute("ohlc.persisted", persisted)
            except Exception:
                pass

        duration = time.perf_counter() - t0
        self._record_metric("ohlc_bar", "ok", duration)


    async def process(self, command) -> dict:
        """Compatibilidade com ProcessMarketEventCommand — converte para MarketEvent e despacha."""
        from finanalytics_ai.domain.entities.event import MarketEvent, EventType
        event = MarketEvent(
            event_id=command.event_id,
            event_type=EventType(command.event_type),
            ticker=command.ticker,
            payload=command.payload,
            source=command.source,
        )
        await self._dispatch(event)
        from types import SimpleNamespace
        from finanalytics_ai.domain.entities.event import EventStatus
        return SimpleNamespace(status=EventStatus.PROCESSED, event_id=command.event_id)
    async def _dispatch(self, event: MarketEvent) -> None:
        """Span pai 'event.dispatch' + despacha para handler correto."""
        dispatch_span = None
        if self._tracer is not None:
            try:
                dispatch_span = self._tracer.start_span("event.dispatch")
                dispatch_span.set_attribute("event.type", str(event.event_type))
                dispatch_span.set_attribute("event.ticker", event.ticker)
            except Exception:
                dispatch_span = None

        try:
            if event.event_type == EventType.PRICE_UPDATE:
                await self._handle_price_update(event)
            elif event.event_type == EventType.OHLC_BAR_CLOSED:
                await self._handle_ohlc_bar(event)
            else:
                log.debug("event_processor_service.dispatch.no_handler",
                          event_type=event.event_type)
        except Exception as exc:
            if dispatch_span is not None:
                try:
                    from opentelemetry.trace import StatusCode
                    dispatch_span.set_status(StatusCode.ERROR, str(exc))
                    dispatch_span.record_exception(exc)
                except Exception:
                    pass
            raise
        finally:
            if dispatch_span is not None:
                try:
                    dispatch_span.end()
                except Exception:
                    pass
