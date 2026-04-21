"""
Testes de integracao do SqlEventRepository com PostgreSQL real.

Estes testes verificam:
1. Upsert cria e atualiza registros corretamente
2. find_by_id retorna None para IDs inexistentes
3. find_by_status filtra corretamente
4. Transicoes de status sao persistidas

Marcados como 'integration' -- so rodam com Docker disponivel.
"""

from __future__ import annotations

import uuid

import pytest

from finanalytics_ai.domain.events.models import (
    DomainEvent,
    EventPayload,
    EventStatus,
)
from finanalytics_ai.domain.events.value_objects import EventType
from finanalytics_ai.infrastructure.event_processor.repository import SqlEventRepository

pytestmark = pytest.mark.integration


def make_event(event_type: str = "price.update") -> DomainEvent:
    return DomainEvent.create(
        EventPayload(
            event_type=EventType(event_type),
            data={"ticker": "PETR4", "price": 38.5},
            source="integration-test",
        )
    )


@pytest.mark.asyncio
class TestSqlEventRepositoryIntegration:
    async def test_upsert_and_find_by_id(self, session_factory) -> None:
        repo = SqlEventRepository(session_factory)
        event = make_event()

        await repo.upsert(event)
        found = await repo.find_by_id(event.event_id)

        assert found is not None
        assert found.event_id == event.event_id
        assert found.status == EventStatus.PENDING
        assert found.payload.data["ticker"] == "PETR4"

    async def test_upsert_updates_existing(self, session_factory) -> None:
        repo = SqlEventRepository(session_factory)
        event = make_event()
        await repo.upsert(event)

        event.mark_processing()
        event.mark_completed({"result": "ok"})
        await repo.upsert(event)

        found = await repo.find_by_id(event.event_id)
        assert found is not None
        assert found.status == EventStatus.COMPLETED
        assert found.processed_at is not None

    async def test_find_by_id_returns_none_for_missing(self, session_factory) -> None:
        repo = SqlEventRepository(session_factory)
        result = await repo.find_by_id(uuid.uuid4())
        assert result is None

    async def test_find_by_status(self, session_factory) -> None:
        repo = SqlEventRepository(session_factory)
        event1 = make_event()
        event2 = make_event()
        await repo.upsert(event1)
        await repo.upsert(event2)

        pending = await repo.find_by_status(EventStatus.PENDING)
        ids = {e.event_id for e in pending}
        assert event1.event_id in ids
        assert event2.event_id in ids

    async def test_failed_event_persisted_with_error_message(self, session_factory) -> None:
        repo = SqlEventRepository(session_factory)
        event = make_event()
        event.mark_failed("timeout ao acessar servico externo")
        await repo.upsert(event)

        found = await repo.find_by_id(event.event_id)
        assert found is not None
        assert found.status == EventStatus.FAILED
        assert found.error_message == "timeout ao acessar servico externo"
        assert found.retry_count == 1

    async def test_dead_letter_persisted(self, session_factory) -> None:
        repo = SqlEventRepository(session_factory)
        event = make_event()
        event.mark_dead_letter("esgotou retries")
        await repo.upsert(event)

        found = await repo.find_by_id(event.event_id)
        assert found is not None
        assert found.status == EventStatus.DEAD_LETTER
