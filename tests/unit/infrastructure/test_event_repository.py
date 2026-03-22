"""
Testes unitários — PostgresEventRepository.

Usa um fake de AsyncSession para verificar que o repositório:
1. Monta as queries SQL corretamente.
2. Traduz erros asyncpg em TransientDatabaseError.
3. Deserializa rows em entidades de domínio sem perda de dados.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from finanalytics_ai.domain.events.entities import (
    Event,
    EventId,
    EventProcessingRecord,
    EventStatus,
    EventType,
)
from finanalytics_ai.infrastructure.database.repositories.event_repository import (
    PostgresEventRepository,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _fake_session(fetchone_return: Any = None, fetchall_return: Any = None) -> Any:
    """Cria um mock de AsyncSession que retorna dados predefinidos."""
    result = MagicMock()
    result.fetchone.return_value = fetchone_return
    result.fetchall.return_value = fetchall_return or []

    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return session


def _make_event() -> Event:
    return Event.create(
        event_type=EventType.FINTZ_SYNC_COMPLETED,
        payload={"dataset": "cotacoes", "rows": 1000},
        source="test_source",
        correlation_id="corr-123",
    )


def _make_record_row(
    event_id: str,
    status: str = "completed",
    attempt: int = 1,
) -> MagicMock:
    row = MagicMock()
    row.event_id = event_id
    row.status = status
    row.attempt = attempt
    row.last_error = None
    row.processed_at = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    row.result_metadata = json.dumps({"rows": 100})
    return row


def _make_event_row(event: Event) -> MagicMock:
    row = MagicMock()
    row.id = str(event.id)
    row.event_type = event.event_type.value
    row.payload = event.payload
    row.source = event.source
    row.correlation_id = event.correlation_id
    row.created_at = event.created_at.replace(tzinfo=None)  # simula Postgres sem tz
    return row


# ──────────────────────────────────────────────────────────────────────────────
# Testes
# ──────────────────────────────────────────────────────────────────────────────


class TestSaveEvent:
    async def test_save_event_calls_execute(self) -> None:
        session = _fake_session()
        repo = PostgresEventRepository(session)
        event = _make_event()

        await repo.save_event(event)

        session.execute.assert_called_once()
        call_params = session.execute.call_args[0][1]
        assert call_params["id"] == str(event.id)
        assert call_params["event_type"] == "fintz.sync.completed"
        assert call_params["source"] == "test_source"

    async def test_save_event_payload_serialized_as_json(self) -> None:
        session = _fake_session()
        repo = PostgresEventRepository(session)
        event = _make_event()

        await repo.save_event(event)

        call_params = session.execute.call_args[0][1]
        assert json.loads(call_params["payload"]) == event.payload


class TestGetProcessingRecord:
    async def test_returns_none_when_not_found(self) -> None:
        session = _fake_session(fetchone_return=None)
        repo = PostgresEventRepository(session)

        result = await repo.get_processing_record(EventId.new())

        assert result is None

    async def test_returns_record_when_found(self) -> None:
        event_id = str(uuid.uuid4())
        row = _make_record_row(event_id, status="completed", attempt=2)
        session = _fake_session(fetchone_return=row)
        repo = PostgresEventRepository(session)

        result = await repo.get_processing_record(EventId.from_str(event_id))

        assert result is not None
        assert result.status == EventStatus.COMPLETED
        assert result.attempt == 2
        assert result.result_metadata == {"rows": 100}


class TestUpsertProcessingRecord:
    async def test_upsert_calls_execute_with_correct_params(self) -> None:
        session = _fake_session()
        repo = PostgresEventRepository(session)
        record = EventProcessingRecord(
            event_id=EventId.new(),
            status=EventStatus.COMPLETED,
            attempt=1,
            result_metadata={"ok": True},
        )

        await repo.upsert_processing_record(record)

        params = session.execute.call_args[0][1]
        assert params["status"] == "completed"
        assert params["attempt"] == 1
        assert json.loads(params["result_metadata"]) == {"ok": True}


class TestGetPendingEvents:
    async def test_returns_empty_list_when_no_pending(self) -> None:
        session = _fake_session(fetchall_return=[])
        repo = PostgresEventRepository(session)

        result = await repo.get_pending_events()

        assert result == []

    async def test_returns_deserialized_events(self) -> None:
        event = _make_event()
        row = _make_event_row(event)
        session = _fake_session(fetchall_return=[row])
        repo = PostgresEventRepository(session)

        result = await repo.get_pending_events()

        assert len(result) == 1
        assert result[0].event_type == EventType.FINTZ_SYNC_COMPLETED
        assert result[0].source == "test_source"
        assert result[0].created_at.tzinfo is not None  # deve ter tzinfo

    async def test_filter_by_event_type_passes_correct_param(self) -> None:
        session = _fake_session(fetchall_return=[])
        repo = PostgresEventRepository(session)

        await repo.get_pending_events(event_type=EventType.PORTFOLIO_REBALANCE)

        params = session.execute.call_args[0][1]
        assert params["event_type"] == "portfolio.rebalance"


class TestErrorTranslation:
    async def test_connection_error_becomes_transient(self) -> None:
        import asyncpg
        from finanalytics_ai.exceptions import TransientDatabaseError

        session = MagicMock()
        session.execute = AsyncMock(
            side_effect=asyncpg.PostgresConnectionError("connection refused")
        )
        repo = PostgresEventRepository(session)

        with pytest.raises(TransientDatabaseError, match="Falha de conexão"):
            await repo.save_event(_make_event())

    async def test_deadlock_becomes_transient(self) -> None:
        import asyncpg
        from finanalytics_ai.exceptions import TransientDatabaseError

        session = MagicMock()
        session.execute = AsyncMock(
            side_effect=asyncpg.DeadlockDetectedError()
        )
        repo = PostgresEventRepository(session)

        with pytest.raises(TransientDatabaseError, match="Deadlock"):
            await repo.save_event(_make_event())


# ──────────────────────────────────────────────────────────────────────────────
# Novos métodos adicionados na Sprint 6
# ──────────────────────────────────────────────────────────────────────────────


class TestGetDeadLetterEvents:
    async def test_returns_empty_when_no_dead_letter(self) -> None:
        # fetchall retorna lista vazia
        session = _fake_session(fetchall_return=[])
        repo = PostgresEventRepository(session)

        result = await repo.get_dead_letter_events(limit=10, offset=0)

        assert result == []

    async def test_query_passes_limit_and_offset(self) -> None:
        session = _fake_session(fetchall_return=[])
        repo = PostgresEventRepository(session)

        await repo.get_dead_letter_events(limit=25, offset=50)

        params = session.execute.call_args[0][1]
        assert params["limit"] == 25
        assert params["offset"] == 50

    async def test_returns_event_record_pairs(self) -> None:
        event = _make_event()
        row = _make_event_row(event)
        # Dead-letter row precisa também dos campos de record
        row.status = "dead_letter"
        row.attempt = 5
        row.last_error = "max retries exhausted"
        row.processed_at = None
        row.result_metadata = "{}"
        session = _fake_session(fetchall_return=[row])
        repo = PostgresEventRepository(session)

        result = await repo.get_dead_letter_events()

        assert len(result) == 1
        ev, rec = result[0]
        assert ev.event_type == EventType.FINTZ_SYNC_COMPLETED
        assert rec.attempt == 5
        assert rec.last_error == "max retries exhausted"


class TestRequeueDeadLetter:
    async def test_requeue_returns_true_when_row_updated(self) -> None:
        from finanalytics_ai.domain.events.entities import EventId

        result_mock = MagicMock()
        result_mock.rowcount = 1
        session = MagicMock()
        session.execute = AsyncMock(return_value=result_mock)
        repo = PostgresEventRepository(session)

        requeued = await repo.requeue_dead_letter(EventId.new())

        assert requeued is True

    async def test_requeue_returns_false_when_event_not_found(self) -> None:
        from finanalytics_ai.domain.events.entities import EventId

        result_mock = MagicMock()
        result_mock.rowcount = 0
        session = MagicMock()
        session.execute = AsyncMock(return_value=result_mock)
        repo = PostgresEventRepository(session)

        requeued = await repo.requeue_dead_letter(EventId.new())

        assert requeued is False

    async def test_requeue_passes_event_id_in_params(self) -> None:
        from finanalytics_ai.domain.events.entities import EventId

        result_mock = MagicMock()
        result_mock.rowcount = 1
        session = MagicMock()
        session.execute = AsyncMock(return_value=result_mock)
        repo = PostgresEventRepository(session)
        eid = EventId.new()

        await repo.requeue_dead_letter(eid)

        params = session.execute.call_args[0][1]
        assert params["event_id"] == str(eid)


class TestForUpdateSkipLocked:
    async def test_lock_clause_present_when_requested(self) -> None:
        session = _fake_session(fetchall_return=[])
        repo = PostgresEventRepository(session)

        await repo.get_pending_events(for_update_skip_locked=True)

        sql = str(session.execute.call_args[0][0])
        assert "SKIP LOCKED" in sql

    async def test_no_lock_clause_by_default(self) -> None:
        session = _fake_session(fetchall_return=[])
        repo = PostgresEventRepository(session)

        await repo.get_pending_events()

        sql = str(session.execute.call_args[0][0])
        assert "SKIP LOCKED" not in sql
