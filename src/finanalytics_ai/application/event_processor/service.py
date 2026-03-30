"""
EventProcessorService — orquestrador com OTEL tracing.

Mudancas da U6:
- TracingPort injetado (default: NullTracing — nao quebra testes existentes)
- Cada chamada a process() gera um span com atributos do evento
- Erros sao registrados no span antes de ser propagados

Compatibilidade: assinatura do __init__ mantem todos os parametros anteriores.
Testes existentes continuam passando sem modificacao (NullTracing como default).
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

import structlog

from finanalytics_ai.application.event_processor.tracing import NullTracing, TracingPort
from finanalytics_ai.domain.events.exceptions import (
    MaxRetriesExceededError,
    PermanentError,
    TransientError,
)
from finanalytics_ai.domain.events.models import (
    DomainEvent,
    EventStatus,
    ProcessingResult,
)

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from finanalytics_ai.application.event_processor.ports import (
        EventRepository,
        IdempotencyStore,
        ObservabilityPort,
    )
    from finanalytics_ai.domain.events.rules import BusinessRule

logger = structlog.get_logger(__name__)


class EventProcessorService:
    """
    Servico principal de processamento de eventos com OTEL tracing.

    O TracingPort e injetado opcionalmente (default NullTracing) para manter
    compatibilidade retroativa com todos os testes existentes.
    """

    def __init__(
        self,
        *,
        repository: EventRepository,
        idempotency_store: IdempotencyStore,
        rules: Sequence[BusinessRule],
        observability: ObservabilityPort,
        tracing: TracingPort | None = None,
        idempotency_ttl: int = 86400,
        idempotency_prefix: str = "evt_idem",
        max_retries: int = 3,
    ) -> None:
        self._repo = repository
        self._idempotency = idempotency_store
        self._rules = list(rules)
        self._obs = observability
        self._tracing: TracingPort = tracing or NullTracing()
        self._idempotency_ttl = idempotency_ttl
        self._idempotency_prefix = idempotency_prefix
        self._max_retries = max_retries

    def _idempotency_key(self, event_id: uuid.UUID) -> str:
        return f"{self._idempotency_prefix}:{event_id}"

    async def process(self, event: DomainEvent) -> ProcessingResult:
        """
        Processa um evento. Cada chamada gera um span OTEL com:
        - event.id, event.type, event.source como atributos
        - status final do processamento
        - excecao registrada em caso de falha
        """
        log = logger.bind(
            event_id=str(event.event_id),
            event_type=str(event.payload.event_type),
            source=event.payload.source,
        )
        start = time.monotonic()

        async with self._tracing.start_span(
            "event.process",
            attributes={
                "event.id": str(event.event_id),
                "event.type": str(event.payload.event_type),
                "event.source": event.payload.source,
            },
        ) as span:
            # 1. Idempotencia
            idem_key = self._idempotency_key(event.event_id)
            already_done = await self._idempotency.check_and_set(
                idem_key, self._idempotency_ttl
            )
            if already_done:
                log.info("event.skipped.idempotent")
                span.set_attribute("event.result", "skipped")
                self._obs.record_event_status(str(event.payload.event_type), "skipped")
                return ProcessingResult.skipped(event.event_id)

            try:
                # 2. Marca como em processamento e persiste
                event.mark_processing()
                await self._repo.upsert(event)
                log.debug("event.processing_started")

                # 3. Aplica regras
                result = await self._apply_rules(event, log)

                # 4. Persiste resultado final
                if result.status == EventStatus.COMPLETED:
                    event.mark_completed(result.output)
                else:
                    error_msg = result.error or "Regra retornou FAILED sem mensagem"
                    self._handle_failure(event, error_msg, log)

                await self._repo.upsert(event)

                duration_ms = (time.monotonic() - start) * 1000
                self._obs.record_processing_time(
                    str(event.payload.event_type), duration_ms
                )
                self._obs.record_event_status(
                    str(event.payload.event_type), event.status.value
                )
                span.set_attribute("event.result", event.status.value)
                span.set_attribute("event.duration_ms", round(duration_ms, 2))
                log.info(
                    "event.processed",
                    status=event.status,
                    duration_ms=round(duration_ms, 2),
                )
                return result

            except TransientError as exc:
                await self._idempotency.release(idem_key)
                event.mark_failed(str(exc))
                await self._repo.upsert(event)
                self._obs.record_retry(
                    str(event.payload.event_type), event.retry_count
                )
                span.record_exception(exc)
                span.set_attribute("event.result", "transient_error")
                log.warning(
                    "event.failed.transient",
                    error=str(exc),
                    retry_count=event.retry_count,
                )
                raise

            except PermanentError as exc:
                event.mark_dead_letter(str(exc))
                await self._repo.upsert(event)
                self._obs.record_event_status(
                    str(event.payload.event_type), "dead_letter"
                )
                span.record_exception(exc)
                span.set_attribute("event.result", "dead_letter")
                span.set_error()
                log.error("event.dead_letter", error=str(exc))
                raise

            except Exception as exc:
                await self._idempotency.release(idem_key)
                event.mark_failed(f"Unexpected: {exc}")
                await self._repo.upsert(event)
                span.record_exception(exc)
                span.set_attribute("event.result", "unexpected_error")
                span.set_error()
                log.exception("event.failed.unexpected", error=str(exc))
                raise

    async def _apply_rules(
        self, event: DomainEvent, log: structlog.BoundLogger
    ) -> ProcessingResult:
        """Aplica regras em sequencia, fail-fast no primeiro FAILED."""
        applicable = [r for r in self._rules if r.applies_to(event)]

        if not applicable:
            log.debug("event.no_rules_applicable")
            return ProcessingResult.success(event.event_id)

        for rule in applicable:
            rule_log = log.bind(rule=rule.name)
            rule_log.debug("rule.applying")

            async with self._tracing.start_span(
                f"rule.{rule.name}",
                attributes={"rule.name": rule.name},
            ) as rule_span:
                result = await rule.apply(event)
                rule_span.set_attribute("rule.result", result.status.value)
                rule_log.debug("rule.applied", status=result.status)

            if result.status != EventStatus.COMPLETED:
                return result

        return ProcessingResult.success(event.event_id)

    def _handle_failure(
        self, event: DomainEvent, error: str, log: structlog.BoundLogger
    ) -> None:
        if event.retry_count >= self._max_retries:
            event.mark_dead_letter(error)
            exc = MaxRetriesExceededError(event.event_id, self._max_retries)
            log.error("event.dead_letter.max_retries", max_retries=self._max_retries)
            raise exc
        event.mark_failed(error, increment_retry=False)
