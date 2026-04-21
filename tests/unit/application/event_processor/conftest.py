"""Fixtures compartilhadas dos testes de aplicacao."""

from __future__ import annotations

import pytest

from finanalytics_ai.domain.events.models import DomainEvent, EventPayload
from finanalytics_ai.domain.events.value_objects import EventType
from tests.unit.application.event_processor.fakes import (
    FakeEventRepository,
    FakeIdempotencyStore,
    FakeObservability,
)


@pytest.fixture
def fake_repo() -> FakeEventRepository:
    return FakeEventRepository()


@pytest.fixture
def fake_idem() -> FakeIdempotencyStore:
    return FakeIdempotencyStore()


@pytest.fixture
def fake_obs() -> FakeObservability:
    return FakeObservability()


@pytest.fixture
def price_event() -> DomainEvent:
    return DomainEvent.create(
        EventPayload(
            event_type=EventType.PRICE_UPDATE,
            data={"ticker": "PETR4", "price": 38.5},
            source="brapi",
        )
    )
