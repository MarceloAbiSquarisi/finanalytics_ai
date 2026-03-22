"""Testes unitários — EventPublisher."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from finanalytics_ai.application.services.event_publisher import EventPublisher
from finanalytics_ai.domain.events.entities import EventStatus, EventType


def _fake_repo() -> MagicMock:
    repo = MagicMock()
    repo.save_event = AsyncMock()
    repo.upsert_processing_record = AsyncMock()
    return repo


class TestEventPublisher:
    async def test_publish_creates_event_with_correct_type(self) -> None:
        session = MagicMock()

        # Patch o repositório interno
        import finanalytics_ai.application.services.event_publisher as mod
        original = mod.PostgresEventRepository
        mod.PostgresEventRepository = lambda s: _fake_repo()  # type: ignore[assignment]

        try:
            publisher = EventPublisher(session)
            event = await publisher.publish(
                EventType.FINTZ_SYNC_COMPLETED,
                payload={"dataset": "cotacoes"},
                source="test",
            )
            assert event.event_type == EventType.FINTZ_SYNC_COMPLETED
            assert event.payload == {"dataset": "cotacoes"}
        finally:
            mod.PostgresEventRepository = original  # type: ignore[assignment]

    async def test_publish_fintz_sync_completed_shortcut(self) -> None:
        session = MagicMock()
        import finanalytics_ai.application.services.event_publisher as mod
        original = mod.PostgresEventRepository
        fake = _fake_repo()
        mod.PostgresEventRepository = lambda s: fake  # type: ignore[assignment]

        try:
            publisher = EventPublisher(session)
            event = await publisher.publish_fintz_sync_completed(
                dataset="indicadores",
                rows_synced=1000,
                errors=5,
                duration_s=3.0,
            )

            assert event.event_type == EventType.FINTZ_SYNC_COMPLETED
            assert event.payload["rows_synced"] == 1000
            assert event.payload["errors"] == 5
            # Verifica que save_event foi chamado
            fake.save_event.assert_called_once()
            # Verifica que upsert_processing_record foi chamado com status PENDING
            call_args = fake.upsert_processing_record.call_args[0][0]
            assert call_args.status == EventStatus.PENDING
        finally:
            mod.PostgresEventRepository = original  # type: ignore[assignment]

    async def test_publish_fintz_sync_failed_shortcut(self) -> None:
        session = MagicMock()
        import finanalytics_ai.application.services.event_publisher as mod
        original = mod.PostgresEventRepository
        mod.PostgresEventRepository = lambda s: _fake_repo()  # type: ignore[assignment]

        try:
            publisher = EventPublisher(session)
            event = await publisher.publish_fintz_sync_failed(
                dataset="cotacoes",
                error_type="ParseError",
                error_message="Unexpected column",
            )
            assert event.event_type == EventType.FINTZ_SYNC_FAILED
            assert event.payload["error_type"] == "ParseError"
        finally:
            mod.PostgresEventRepository = original  # type: ignore[assignment]
