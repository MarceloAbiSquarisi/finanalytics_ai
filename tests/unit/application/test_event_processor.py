"""
Testes unitários — EventProcessor.

Estratégia: Fakes (não Mocks).
Por que fakes em vez de unittest.mock.MagicMock?
- Fakes implementam o contrato real (Protocol) → mypy valida.
- Comportamento mais próximo do real: InMemoryEventRepository tem estado real.
- Erros de integração aparecem nos testes de unidade, não só em integração.

Os fakes vivem neste arquivo (sem conftest.py complexo) para manter
a leitura autocontida — o revisor vê tudo no mesmo arquivo.
"""

from __future__ import annotations

from typing import Any, Sequence

import pytest

from finanalytics_ai.application.services.event_processor import EventProcessor
from finanalytics_ai.config import Settings
from finanalytics_ai.domain.events.entities import (
    Event,
    EventId,
    EventProcessingRecord,
    EventStatus,
    EventType,
)
from finanalytics_ai.exceptions import (
    BusinessRuleError,
    EventAlreadyProcessedError,
    TransientDatabaseError,
)
from finanalytics_ai.observability.metrics import NoOpObservability


# ──────────────────────────────────────────────────────────────────────────────
# Fakes
# ──────────────────────────────────────────────────────────────────────────────


class InMemoryEventRepository:
    """Fake repository com estado real em memória."""

    def __init__(self) -> None:
        self.events: dict[str, Event] = {}
        self.records: dict[str, EventProcessingRecord] = {}

    async def save_event(self, event: Event) -> None:
        self.events.setdefault(str(event.id), event)

    async def get_processing_record(self, event_id: EventId) -> EventProcessingRecord | None:
        return self.records.get(str(event_id))

    async def upsert_processing_record(self, record: EventProcessingRecord) -> None:
        self.records[str(record.event_id)] = record

    async def get_pending_events(
        self, event_type: EventType | None = None, limit: int = 100
    ) -> list[Event]:
        return list(self.events.values())[:limit]


class SuccessRule:
    """Regra que sempre tem sucesso."""

    handles = frozenset({EventType.FINTZ_SYNC_COMPLETED})

    async def apply(self, event: Event) -> dict[str, Any]:
        return {"result": "ok"}


class FailingBusinessRule:
    """Regra que sempre lança BusinessRuleError (erro permanente)."""

    handles = frozenset({EventType.FINTZ_SYNC_FAILED})

    async def apply(self, event: Event) -> dict[str, Any]:
        raise BusinessRuleError("Dados inválidos no payload")


class TransientFailRule:
    """Regra que falha com erro transitório N vezes antes de ter sucesso."""

    handles = frozenset({EventType.PORTFOLIO_REBALANCE})

    def __init__(self, fail_times: int = 2) -> None:
        self._fail_times = fail_times
        self._call_count = 0

    async def apply(self, event: Event) -> dict[str, Any]:
        self._call_count += 1
        if self._call_count <= self._fail_times:
            raise TransientDatabaseError(f"Falha transitória #{self._call_count}")
        return {"recovered": True}


def _make_settings(**overrides: Any) -> Settings:
    """Cria Settings mínimo para testes sem arquivo .env."""
    defaults: dict[str, Any] = {
        "database_url": "postgresql+asyncpg://user:pass@localhost/test",
        "app_secret_key": "test-secret-key-16chars",
        "event_max_retries": 5,
        "event_retry_base_delay": 0.001,  # acelera testes
        "event_processor_concurrency": 10,
        "metrics_enabled": False,
        "log_level": "ERROR",
        "log_format": "text",
    }
    defaults.update(overrides)
    return Settings.model_validate(defaults)


def _make_processor(
    rules: Sequence[Any],
    repo: InMemoryEventRepository | None = None,
    settings: Settings | None = None,
) -> tuple[EventProcessor, InMemoryEventRepository]:
    if repo is None:
        repo = InMemoryEventRepository()
    if settings is None:
        settings = _make_settings()
    processor = EventProcessor(
        repository=repo,
        rules=rules,
        observability=NoOpObservability(),
        settings=settings,
    )
    return processor, repo


def _make_event(event_type: EventType = EventType.FINTZ_SYNC_COMPLETED) -> Event:
    return Event.create(event_type=event_type, payload={"test": True}, source="test")


# ──────────────────────────────────────────────────────────────────────────────
# Testes
# ──────────────────────────────────────────────────────────────────────────────


class TestEventProcessorHappyPath:
    async def test_process_returns_completed_record(self) -> None:
        processor, repo = _make_processor([SuccessRule()])
        event = _make_event()

        record = await processor.process(event)

        assert record.status == EventStatus.COMPLETED
        assert record.result_metadata == {"result": "ok"}

    async def test_event_saved_to_repository(self) -> None:
        processor, repo = _make_processor([SuccessRule()])
        event = _make_event()

        await processor.process(event)

        assert str(event.id) in repo.events

    async def test_processing_record_persisted(self) -> None:
        processor, repo = _make_processor([SuccessRule()])
        event = _make_event()

        await processor.process(event)

        record = repo.records[str(event.id)]
        assert record.status == EventStatus.COMPLETED


class TestIdempotency:
    async def test_reprocessing_completed_event_raises(self) -> None:
        processor, repo = _make_processor([SuccessRule()])
        event = _make_event()

        await processor.process(event)

        with pytest.raises(EventAlreadyProcessedError):
            await processor.process(event)

    async def test_batch_skips_already_processed_silently(self) -> None:
        processor, repo = _make_processor([SuccessRule()])
        event = _make_event()

        await processor.process(event)
        records = await processor.process_batch([event])

        # Evento já processado é silenciosamente ignorado no batch
        assert len(records) == 0


class TestBusinessRuleError:
    async def test_business_rule_error_goes_to_dead_letter(self) -> None:
        processor, repo = _make_processor([FailingBusinessRule()])
        event = _make_event(EventType.FINTZ_SYNC_FAILED)

        record = await processor.process(event)

        assert record.status == EventStatus.DEAD_LETTER
        assert "Dados inválidos" in (record.last_error or "")

    async def test_business_rule_error_no_retry(self) -> None:
        processor, repo = _make_processor([FailingBusinessRule()])
        event = _make_event(EventType.FINTZ_SYNC_FAILED)

        record = await processor.process(event)

        # Deve ter tentado apenas 1 vez (sem retry para erros de negócio)
        assert record.attempt == 1


class TestRetryLogic:
    async def test_transient_error_retries_and_recovers(self) -> None:
        rule = TransientFailRule(fail_times=2)
        processor, repo = _make_processor([rule])
        event = _make_event(EventType.PORTFOLIO_REBALANCE)

        record = await processor.process(event)

        assert record.status == EventStatus.COMPLETED
        assert record.attempt == 3  # 2 falhas + 1 sucesso

    async def test_transient_error_max_retries_dead_letter(self) -> None:
        rule = TransientFailRule(fail_times=99)
        settings = _make_settings(event_max_retries=3)
        processor, repo = _make_processor([rule], settings=settings)
        event = _make_event(EventType.PORTFOLIO_REBALANCE)

        record = await processor.process(event)

        assert record.status == EventStatus.DEAD_LETTER
        assert record.attempt == 3


class TestNoHandler:
    async def test_unknown_event_type_goes_dead_letter(self) -> None:
        """Evento sem regra registrada → dead-letter imediato (não retry)."""
        processor, repo = _make_processor([SuccessRule()])
        event = _make_event(EventType.ALERT_TRIGGERED)  # sem regra para este tipo

        record = await processor.process(event)

        assert record.status == EventStatus.DEAD_LETTER


class TestConcurrentBatch:
    async def test_batch_processes_multiple_events(self) -> None:
        processor, repo = _make_processor([SuccessRule()])
        events = [_make_event() for _ in range(5)]

        records = await processor.process_batch(events)

        assert len(records) == 5
        assert all(r.status == EventStatus.COMPLETED for r in records)

    async def test_batch_partial_failure_does_not_abort_others(self) -> None:
        processor, repo = _make_processor([SuccessRule(), FailingBusinessRule()])
        events = [
            _make_event(EventType.FINTZ_SYNC_COMPLETED),
            _make_event(EventType.FINTZ_SYNC_FAILED),
            _make_event(EventType.FINTZ_SYNC_COMPLETED),
        ]

        records = await processor.process_batch(events)

        # Os 2 COMPLETED devem processar mesmo que o FAILED vá para dead-letter
        completed = [r for r in records if r.status == EventStatus.COMPLETED]
        assert len(completed) == 2
