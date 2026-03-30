"""
Testes unitarios dos endpoints de events_admin.

Usa FakeEventRepository para evitar dependencia de banco.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from finanalytics_ai.domain.events.models import DomainEvent, EventPayload, EventStatus
from finanalytics_ai.domain.events.value_objects import EventType


def make_dead_letter_event() -> DomainEvent:
    event = DomainEvent.create(
        EventPayload(
            event_type=EventType("price.update"),
            data={"ticker": "PETR4"},
            source="test",
        )
    )
    event.mark_dead_letter("max retries")
    return event


def make_failed_event() -> DomainEvent:
    event = DomainEvent.create(
        EventPayload(
            event_type=EventType("price.update"),
            data={"ticker": "VALE3"},
            source="test",
        )
    )
    event.mark_failed("timeout")
    return event


class TestReprocessEndpointLogic:
    """Testa a logica de negocio do endpoint sem HTTP."""

    def test_only_failed_and_dead_letter_are_reprocessable(self) -> None:
        from finanalytics_ai.interfaces.api.routes.events_admin import REPROCESSABLE_STATUSES
        assert EventStatus.FAILED in REPROCESSABLE_STATUSES
        assert EventStatus.DEAD_LETTER in REPROCESSABLE_STATUSES
        assert EventStatus.COMPLETED not in REPROCESSABLE_STATUSES
        assert EventStatus.PENDING not in REPROCESSABLE_STATUSES

    def test_invalid_uuid_raises_422(self) -> None:
        """UUID invalido deve retornar 422."""
        import asyncio

        from finanalytics_ai.interfaces.api.routes.events_admin import reprocess_event

        async def _run() -> None:
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                await reprocess_event(
                    event_id="not-a-uuid",
                    _=MagicMock(),
                    session=MagicMock(),
                )
            assert exc_info.value.status_code == 422

        asyncio.get_event_loop().run_until_complete(_run())

