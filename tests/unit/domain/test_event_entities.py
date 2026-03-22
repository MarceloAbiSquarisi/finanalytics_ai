"""
Testes unitários — Domain entities.

Princípios:
- Zero IO: sem banco, sem rede, sem filesystem.
- Cada teste cobre uma transição de estado ou invariante de domínio.
- Nomes descritivos: `test_<quando>_<esperado>`.
"""

from __future__ import annotations

import uuid

import pytest

from finanalytics_ai.domain.events.entities import (
    Event,
    EventId,
    EventProcessingRecord,
    EventStatus,
    EventType,
    InvalidEventIdError,
    InvalidStatusTransitionError,
)


# ──────────────────────────────────────────────────────────────────────────────
# EventId
# ──────────────────────────────────────────────────────────────────────────────


class TestEventId:
    def test_new_generates_unique_ids(self) -> None:
        ids = {EventId.new() for _ in range(100)}
        assert len(ids) == 100

    def test_from_str_valid_uuid(self) -> None:
        raw = "123e4567-e89b-12d3-a456-426614174000"
        event_id = EventId.from_str(raw)
        assert str(event_id) == raw

    def test_from_str_invalid_raises(self) -> None:
        with pytest.raises(InvalidEventIdError):
            EventId.from_str("not-a-uuid")

    def test_frozen_immutable(self) -> None:
        event_id = EventId.new()
        with pytest.raises(AttributeError):
            event_id.value = uuid.uuid4()  # type: ignore[misc]


# ──────────────────────────────────────────────────────────────────────────────
# Event
# ──────────────────────────────────────────────────────────────────────────────


class TestEvent:
    def test_create_assigns_new_id(self) -> None:
        event = Event.create(
            event_type=EventType.FINTZ_SYNC_COMPLETED,
            payload={"dataset": "cotacoes"},
            source="test",
        )
        assert event.id is not None
        assert event.event_type == EventType.FINTZ_SYNC_COMPLETED

    def test_create_with_correlation_id(self) -> None:
        event = Event.create(
            event_type=EventType.FINTZ_SYNC_COMPLETED,
            payload={},
            source="test",
            correlation_id="req-123",
        )
        assert event.correlation_id == "req-123"

    def test_event_is_immutable(self) -> None:
        event = Event.create(
            event_type=EventType.FINTZ_SYNC_COMPLETED,
            payload={},
            source="test",
        )
        with pytest.raises(AttributeError):
            event.source = "outro"  # type: ignore[misc]


# ──────────────────────────────────────────────────────────────────────────────
# EventProcessingRecord — máquina de estados
# ──────────────────────────────────────────────────────────────────────────────


class TestEventProcessingRecord:
    def _make_record(self, status: EventStatus = EventStatus.PENDING) -> EventProcessingRecord:
        return EventProcessingRecord(
            event_id=EventId.new(),
            status=status,
        )

    def test_mark_processing_from_pending(self) -> None:
        record = self._make_record(EventStatus.PENDING)
        record.mark_processing()
        assert record.status == EventStatus.PROCESSING
        assert record.attempt == 1

    def test_mark_processing_from_failed(self) -> None:
        record = self._make_record(EventStatus.FAILED)
        record.mark_processing()
        assert record.status == EventStatus.PROCESSING

    def test_mark_processing_from_completed_raises(self) -> None:
        record = self._make_record(EventStatus.COMPLETED)
        with pytest.raises(InvalidStatusTransitionError):
            record.mark_processing()

    def test_mark_completed_sets_processed_at(self) -> None:
        record = self._make_record(EventStatus.PROCESSING)
        record.mark_completed({"rows": 100})
        assert record.status == EventStatus.COMPLETED
        assert record.processed_at is not None
        assert record.result_metadata == {"rows": 100}

    def test_mark_failed_below_max_retries_stays_failed(self) -> None:
        record = self._make_record(EventStatus.PROCESSING)
        record.attempt = 1
        record.mark_failed("timeout", max_retries=5)
        assert record.status == EventStatus.FAILED
        assert record.last_error == "timeout"

    def test_mark_failed_at_max_retries_goes_dead_letter(self) -> None:
        record = self._make_record(EventStatus.PROCESSING)
        record.attempt = 5
        record.mark_failed("timeout", max_retries=5)
        assert record.status == EventStatus.DEAD_LETTER

    def test_attempt_increments_on_each_processing(self) -> None:
        record = self._make_record(EventStatus.PENDING)
        for expected in range(1, 4):
            record.mark_processing()
            record.mark_failed("err", max_retries=10)
            assert record.attempt == expected
