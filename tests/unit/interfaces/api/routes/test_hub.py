"""
Testes unitários do hub router.

Usa FakeEventRepository e TestClient do FastAPI para teste HTTP completo
sem banco de dados real. Cada teste monta um app mínimo com dependency override.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from finanalytics_ai.domain.events.models import DomainEvent, EventPayload, EventStatus
from finanalytics_ai.domain.events.value_objects import EventType
from finanalytics_ai.interfaces.api.routes import hub


# ──────────────────────────────────────────────────────────────────────────────
# Fake session that wraps FakeRepo — simula AsyncSession para dependency
# ──────────────────────────────────────────────────────────────────────────────


class FakeRepo:
    """Repositório in-memory para testes do hub."""

    def __init__(self) -> None:
        self.store: dict[uuid.UUID, DomainEvent] = {}

    async def upsert(self, event: DomainEvent) -> None:
        self.store[event.event_id] = event

    async def find_by_id(self, event_id: uuid.UUID) -> DomainEvent | None:
        return self.store.get(event_id)

    async def find_by_status(
        self, status: EventStatus, *, limit: int = 100
    ) -> list[DomainEvent]:
        return [e for e in self.store.values() if e.status == status][:limit]

    async def find_filtered(
        self,
        *,
        status: EventStatus | None = None,
        event_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DomainEvent]:
        results = list(self.store.values())
        if status is not None:
            results = [e for e in results if e.status == status]
        if event_type is not None:
            results = [e for e in results if str(e.payload.event_type) == event_type]
        return results[offset : offset + limit]

    async def count_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.store.values():
            counts[e.status.value] = counts.get(e.status.value, 0) + 1
        return counts

    async def delete_completed_before(self, cutoff: datetime) -> int:
        to_delete = [
            eid
            for eid, e in self.store.items()
            if e.status == EventStatus.COMPLETED and e.created_at < cutoff
        ]
        for eid in to_delete:
            del self.store[eid]
        return len(to_delete)


# Singleton do fake repo — compartilhado entre hub._make_repo e os testes
_fake_repo = FakeRepo()


def _get_test_client(fake_repo: FakeRepo) -> TestClient:
    """Monta um app mínimo com hub router + fake session."""
    app = FastAPI()
    app.include_router(hub.router)

    # Override: hub.get_db retorna um sentinel que será ignorado
    # porque também fazemos monkey-patch em hub._make_repo
    async def _fake_db() -> Any:
        yield None

    app.dependency_overrides[hub.get_db] = _fake_db

    # Monkey-patch _make_repo para retornar o fake
    original = hub._make_repo

    def _patched_make_repo(session: Any) -> FakeRepo:  # type: ignore[override]
        return fake_repo

    hub._make_repo = _patched_make_repo  # type: ignore[assignment]

    client = TestClient(app)
    # Store original to restore later
    client._original_make_repo = original  # type: ignore[attr-defined]
    return client


def _cleanup(client: TestClient) -> None:
    hub._make_repo = client._original_make_repo  # type: ignore[attr-defined]


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _make_event(
    event_type: str = "price.update",
    status: EventStatus = EventStatus.PENDING,
    created_at: datetime | None = None,
) -> DomainEvent:
    event = DomainEvent.create(
        EventPayload(
            event_type=EventType(event_type),
            data={"ticker": "PETR4", "price": 38.5},
            source="test",
        )
    )
    if status == EventStatus.FAILED:
        event.mark_processing()
        event.mark_failed("some error")
    elif status == EventStatus.DEAD_LETTER:
        event.mark_processing()
        event.mark_dead_letter("max retries")
    elif status == EventStatus.COMPLETED:
        event.mark_processing()
        event.mark_completed({"ok": True})
    if created_at is not None:
        event.created_at = created_at
    return event


# ══════════════════════════════════════════════════════════════════════════════
# POST /hub/events
# ══════════════════════════════════════════════════════════════════════════════


class TestCreateEvent:
    def test_creates_event_successfully(self) -> None:
        repo = FakeRepo()
        client = _get_test_client(repo)
        try:
            resp = client.post(
                "/hub/events",
                json={
                    "event_type": "price.update",
                    "source": "test",
                    "data": {"ticker": "PETR4"},
                },
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body["status"] == "pending"
            assert body["event_type"] == "price.update"
            assert body["source"] == "test"

            # Verifica que o evento foi persistido
            eid = uuid.UUID(body["event_id"])
            assert eid in repo.store
        finally:
            _cleanup(client)

    def test_creates_event_with_correlation_id(self) -> None:
        repo = FakeRepo()
        client = _get_test_client(repo)
        try:
            resp = client.post(
                "/hub/events",
                json={
                    "event_type": "trade.executed",
                    "source": "broker",
                    "data": {},
                    "correlation_id": "corr-123",
                },
            )
            assert resp.status_code == 201
            assert resp.json()["correlation_id"] == "corr-123"
        finally:
            _cleanup(client)

    def test_rejects_empty_event_type(self) -> None:
        repo = FakeRepo()
        client = _get_test_client(repo)
        try:
            resp = client.post(
                "/hub/events",
                json={"event_type": "", "source": "test", "data": {}},
            )
            assert resp.status_code == 422
        finally:
            _cleanup(client)

    def test_rejects_empty_source(self) -> None:
        repo = FakeRepo()
        client = _get_test_client(repo)
        try:
            resp = client.post(
                "/hub/events",
                json={"event_type": "price.update", "source": "", "data": {}},
            )
            assert resp.status_code == 422
        finally:
            _cleanup(client)


# ══════════════════════════════════════════════════════════════════════════════
# GET /hub/events
# ══════════════════════════════════════════════════════════════════════════════


class TestListEvents:
    def test_returns_empty_list(self) -> None:
        repo = FakeRepo()
        client = _get_test_client(repo)
        try:
            resp = client.get("/hub/events")
            assert resp.status_code == 200
            body = resp.json()
            assert body["total"] == 0
            assert body["events"] == []
        finally:
            _cleanup(client)

    def test_returns_events(self) -> None:
        repo = FakeRepo()
        e1 = _make_event()
        e2 = _make_event(event_type="trade.executed")
        repo.store[e1.event_id] = e1
        repo.store[e2.event_id] = e2

        client = _get_test_client(repo)
        try:
            resp = client.get("/hub/events")
            assert resp.status_code == 200
            assert resp.json()["total"] == 2
        finally:
            _cleanup(client)

    def test_filters_by_status(self) -> None:
        repo = FakeRepo()
        pending = _make_event()
        completed = _make_event(status=EventStatus.COMPLETED)
        repo.store[pending.event_id] = pending
        repo.store[completed.event_id] = completed

        client = _get_test_client(repo)
        try:
            resp = client.get("/hub/events", params={"status": "completed"})
            assert resp.status_code == 200
            body = resp.json()
            assert body["total"] == 1
            assert body["events"][0]["status"] == "completed"
        finally:
            _cleanup(client)

    def test_filters_by_event_type(self) -> None:
        repo = FakeRepo()
        e1 = _make_event(event_type="price.update")
        e2 = _make_event(event_type="trade.executed")
        repo.store[e1.event_id] = e1
        repo.store[e2.event_id] = e2

        client = _get_test_client(repo)
        try:
            resp = client.get("/hub/events", params={"event_type": "trade.executed"})
            assert resp.status_code == 200
            body = resp.json()
            assert body["total"] == 1
            assert body["events"][0]["event_type"] == "trade.executed"
        finally:
            _cleanup(client)

    def test_invalid_status_returns_422(self) -> None:
        repo = FakeRepo()
        client = _get_test_client(repo)
        try:
            resp = client.get("/hub/events", params={"status": "invalid"})
            assert resp.status_code == 422
        finally:
            _cleanup(client)


# ══════════════════════════════════════════════════════════════════════════════
# GET /hub/stats
# ══════════════════════════════════════════════════════════════════════════════


class TestStats:
    def test_empty_stats(self) -> None:
        repo = FakeRepo()
        client = _get_test_client(repo)
        try:
            resp = client.get("/hub/stats")
            assert resp.status_code == 200
            body = resp.json()
            assert body["total"] == 0
            assert body["counts"] == {}
        finally:
            _cleanup(client)

    def test_counts_by_status(self) -> None:
        repo = FakeRepo()
        for _ in range(3):
            e = _make_event(status=EventStatus.PENDING)
            repo.store[e.event_id] = e
        for _ in range(2):
            e = _make_event(status=EventStatus.COMPLETED)
            repo.store[e.event_id] = e
        e = _make_event(status=EventStatus.FAILED)
        repo.store[e.event_id] = e

        client = _get_test_client(repo)
        try:
            resp = client.get("/hub/stats")
            assert resp.status_code == 200
            body = resp.json()
            assert body["counts"]["pending"] == 3
            assert body["counts"]["completed"] == 2
            assert body["counts"]["failed"] == 1
            assert body["total"] == 6
        finally:
            _cleanup(client)


# ══════════════════════════════════════════════════════════════════════════════
# POST /hub/events/{id}/reprocess
# ══════════════════════════════════════════════════════════════════════════════


class TestReprocess:
    def test_reprocess_failed_event(self) -> None:
        repo = FakeRepo()
        event = _make_event(status=EventStatus.FAILED)
        repo.store[event.event_id] = event

        client = _get_test_client(repo)
        try:
            resp = client.post(f"/hub/events/{event.event_id}/reprocess")
            assert resp.status_code == 202
            body = resp.json()
            assert body["previous_status"] == "failed"
            assert body["new_status"] == "pending"

            # Verifica que o status foi atualizado no repo
            assert repo.store[event.event_id].status == EventStatus.PENDING
        finally:
            _cleanup(client)

    def test_reprocess_dead_letter_event(self) -> None:
        repo = FakeRepo()
        event = _make_event(status=EventStatus.DEAD_LETTER)
        repo.store[event.event_id] = event

        client = _get_test_client(repo)
        try:
            resp = client.post(f"/hub/events/{event.event_id}/reprocess")
            assert resp.status_code == 202
            assert resp.json()["previous_status"] == "dead_letter"
        finally:
            _cleanup(client)

    def test_reprocess_completed_event_returns_409(self) -> None:
        repo = FakeRepo()
        event = _make_event(status=EventStatus.COMPLETED)
        repo.store[event.event_id] = event

        client = _get_test_client(repo)
        try:
            resp = client.post(f"/hub/events/{event.event_id}/reprocess")
            assert resp.status_code == 409
        finally:
            _cleanup(client)

    def test_reprocess_nonexistent_event_returns_404(self) -> None:
        repo = FakeRepo()
        client = _get_test_client(repo)
        try:
            fake_id = str(uuid.uuid4())
            resp = client.post(f"/hub/events/{fake_id}/reprocess")
            assert resp.status_code == 404
        finally:
            _cleanup(client)

    def test_reprocess_invalid_uuid_returns_422(self) -> None:
        repo = FakeRepo()
        client = _get_test_client(repo)
        try:
            resp = client.post("/hub/events/not-a-uuid/reprocess")
            assert resp.status_code == 422
        finally:
            _cleanup(client)


# ══════════════════════════════════════════════════════════════════════════════
# POST /hub/cleanup
# ══════════════════════════════════════════════════════════════════════════════


class TestCleanup:
    def test_cleanup_deletes_old_completed(self) -> None:
        repo = FakeRepo()
        old_event = _make_event(
            status=EventStatus.COMPLETED,
            created_at=datetime.now(timezone.utc) - timedelta(days=60),
        )
        repo.store[old_event.event_id] = old_event

        client = _get_test_client(repo)
        try:
            resp = client.post("/hub/cleanup", params={"retention_days": 30})
            assert resp.status_code == 200
            body = resp.json()
            assert body["deleted"] == 1
            assert body["retention_days"] == 30
            assert old_event.event_id not in repo.store
        finally:
            _cleanup(client)

    def test_cleanup_respects_retention_days(self) -> None:
        """Evento completed de 60 dias atrás não é deletado com retention=90."""
        repo = FakeRepo()
        recent_event = _make_event(
            status=EventStatus.COMPLETED,
            created_at=datetime.now(timezone.utc) - timedelta(days=60),
        )
        repo.store[recent_event.event_id] = recent_event

        client = _get_test_client(repo)
        try:
            resp = client.post("/hub/cleanup", params={"retention_days": 90})
            assert resp.status_code == 200
            body = resp.json()
            assert body["deleted"] == 0
            assert recent_event.event_id in repo.store
        finally:
            _cleanup(client)

    def test_cleanup_keeps_non_completed(self) -> None:
        """FAILED, PENDING e DEAD_LETTER antigos NÃO são deletados."""
        repo = FakeRepo()
        old_failed = _make_event(
            status=EventStatus.FAILED,
            created_at=datetime.now(timezone.utc) - timedelta(days=60),
        )
        old_pending = _make_event(
            status=EventStatus.PENDING,
            created_at=datetime.now(timezone.utc) - timedelta(days=60),
        )
        old_dead = _make_event(
            status=EventStatus.DEAD_LETTER,
            created_at=datetime.now(timezone.utc) - timedelta(days=60),
        )
        repo.store[old_failed.event_id] = old_failed
        repo.store[old_pending.event_id] = old_pending
        repo.store[old_dead.event_id] = old_dead

        client = _get_test_client(repo)
        try:
            resp = client.post("/hub/cleanup", params={"retention_days": 30})
            assert resp.status_code == 200
            body = resp.json()
            assert body["deleted"] == 0
            assert old_failed.event_id in repo.store
            assert old_pending.event_id in repo.store
            assert old_dead.event_id in repo.store
        finally:
            _cleanup(client)
