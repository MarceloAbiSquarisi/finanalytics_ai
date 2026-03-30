"""
Testes unitarios para os modelos de dominio.

Principio: testes de dominio NUNCA tocam IO. Sem banco, sem Redis, sem HTTP.
Rapidos o suficiente para rodar em pre-commit hook.
"""
from __future__ import annotations

import pytest

from finanalytics_ai.domain.events.models import (
    DomainEvent,
    EventPayload,
    EventStatus,
    ProcessingResult,
)
from finanalytics_ai.domain.events.value_objects import EventType


def make_payload(**kwargs) -> EventPayload:  # type: ignore[no-untyped-def]
    defaults = {
        "event_type": EventType("price.update"),
        "data": {"ticker": "PETR4", "price": 38.5},
        "source": "brapi",
    }
    return EventPayload(**{**defaults, **kwargs})


class TestDomainEvent:
    def test_create_sets_pending_status(self) -> None:
        event = DomainEvent.create(make_payload())
        assert event.status == EventStatus.PENDING

    def test_create_generates_unique_ids(self) -> None:
        e1 = DomainEvent.create(make_payload())
        e2 = DomainEvent.create(make_payload())
        assert e1.event_id != e2.event_id

    def test_mark_processing_from_pending(self) -> None:
        event = DomainEvent.create(make_payload())
        event.mark_processing()
        assert event.status == EventStatus.PROCESSING

    def test_mark_processing_from_failed_allowed(self) -> None:
        event = DomainEvent.create(make_payload())
        event.mark_failed("erro", increment_retry=True)
        event.mark_processing()  # deve funcionar para retry
        assert event.status == EventStatus.PROCESSING

    def test_mark_processing_from_completed_raises(self) -> None:
        event = DomainEvent.create(make_payload())
        event.mark_processing()
        event.mark_completed()
        with pytest.raises(ValueError, match="Transicao invalida"):
            event.mark_processing()

    def test_mark_failed_increments_retry(self) -> None:
        event = DomainEvent.create(make_payload())
        event.mark_failed("erro 1")
        assert event.retry_count == 1
        event.mark_failed("erro 2")
        assert event.retry_count == 2

    def test_mark_failed_no_increment(self) -> None:
        event = DomainEvent.create(make_payload())
        event.mark_failed("erro", increment_retry=False)
        assert event.retry_count == 0

    def test_mark_completed_sets_processed_at(self) -> None:
        event = DomainEvent.create(make_payload())
        event.mark_processing()
        event.mark_completed()
        assert event.processed_at is not None
        assert event.status == EventStatus.COMPLETED

    def test_is_retriable_only_when_failed(self) -> None:
        event = DomainEvent.create(make_payload())
        assert not event.is_retriable
        event.mark_failed("erro")
        assert event.is_retriable
        event.mark_processing()
        assert not event.is_retriable

    def test_idempotency_key_is_event_id_string(self) -> None:
        event = DomainEvent.create(make_payload())
        assert event.idempotency_key == str(event.event_id)


class TestEventPayload:
    def test_empty_source_raises(self) -> None:
        with pytest.raises(ValueError):
            EventPayload(event_type=EventType("x"), data={}, source="")

    def test_frozen(self) -> None:
        payload = make_payload()
        with pytest.raises((AttributeError, TypeError)):  # FrozenInstanceError
            payload.source = "outro"  # type: ignore[misc]


class TestProcessingResult:
    def test_success_factory(self) -> None:
        import uuid
        event_id = uuid.uuid4()
        r = ProcessingResult.success(event_id, {"x": 1})
        assert r.status == EventStatus.COMPLETED
        assert r.output == {"x": 1}
        assert r.error is None

    def test_failure_factory(self) -> None:
        import uuid
        r = ProcessingResult.failure(uuid.uuid4(), "bad input")
        assert r.status == EventStatus.FAILED
        assert r.error == "bad input"

    def test_skipped_factory(self) -> None:
        import uuid
        r = ProcessingResult.skipped(uuid.uuid4())
        assert r.status == EventStatus.SKIPPED

