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
