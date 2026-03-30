"""
Testes unitarios da PriceUpdateRule.

Usa um pool fake para verificar a persistencia sem TimescaleDB real.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from finanalytics_ai.application.event_processor.rules.price_update import (
    PriceUpdateRule,
    _parse_timestamp,
)
from finanalytics_ai.domain.events.models import DomainEvent, EventPayload, EventStatus
from finanalytics_ai.domain.events.value_objects import EventType


class FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple] = []

    async def execute(self, query: str, *args: Any) -> None:
        self.executed.append((query, args))

    async def fetchrow(self, query: str, *args: Any) -> None:
        return None


class FakePool:
    def __init__(self) -> None:
        self.conn = FakeConn()

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


def make_price_event(ticker: str = "PETR4", price: float = 38.5) -> DomainEvent:
    return DomainEvent.create(
        EventPayload(
            event_type=EventType.PRICE_UPDATE,
            data={
                "ticker": ticker,
                "price": price,
                "volume": 1000.0,
                "timestamp": "2025-01-15T10:30:00+00:00",
                "source": "profit_dll",
            },
            source="profit_dll",
        )
    )


class TestPriceUpdateRule:
    def test_applies_to_price_update(self) -> None:
        rule = PriceUpdateRule()
        event = make_price_event()
        assert rule.applies_to(event) is True

    def test_does_not_apply_to_other_events(self) -> None:
        rule = PriceUpdateRule()
        event = DomainEvent.create(
            EventPayload(
                event_type=EventType("portfolio.rebalance"),
                data={},
                source="test",
            )
        )
        assert rule.applies_to(event) is False


@pytest.mark.asyncio
class TestPriceUpdateRuleApply:
    async def test_success_with_pool(self) -> None:
        pool = FakePool()
        rule = PriceUpdateRule(timescale_pool=pool)
        event = make_price_event()
        result = await rule.apply(event)

        assert result.status == EventStatus.COMPLETED
        assert result.output is not None
        assert result.output["persisted"] is True
        assert result.output["ticker"] == "PETR4"

    async def test_persists_correct_values(self) -> None:
        pool = FakePool()
        rule = PriceUpdateRule(timescale_pool=pool)
        await rule.apply(make_price_event("VALE3", 95.0))

        assert len(pool.conn.executed) == 1
        query, args = pool.conn.executed[0]
        assert "INSERT INTO fintz_cotacoes_ts" in query
        assert "VALE3" in args
        assert 95.0 in args

    async def test_success_without_pool(self) -> None:
        rule = PriceUpdateRule(timescale_pool=None)
        event = make_price_event()
        result = await rule.apply(event)

        assert result.status == EventStatus.COMPLETED
        assert result.output is not None
        assert result.output["persisted"] is False
        assert "timescale_pool nao configurado" in result.output["reason"]

    async def test_missing_ticker_returns_failure(self) -> None:
        rule = PriceUpdateRule()
        event = DomainEvent.create(
            EventPayload(
                event_type=EventType.PRICE_UPDATE,
                data={"price": 38.5},  # sem ticker
                source="test",
            )
        )
        result = await rule.apply(event)
        assert result.status == EventStatus.FAILED

    async def test_missing_price_returns_failure(self) -> None:
        rule = PriceUpdateRule()
        event = DomainEvent.create(
            EventPayload(
                event_type=EventType.PRICE_UPDATE,
                data={"ticker": "PETR4"},  # sem price
                source="test",
            )
        )
        result = await rule.apply(event)
        assert result.status == EventStatus.FAILED

    async def test_db_error_raises_database_error(self) -> None:
        from finanalytics_ai.domain.events.exceptions import DatabaseError

        class FailingPool:
            @asynccontextmanager
            async def acquire(self):
                raise ConnectionError("timescale indisponivel")
                yield  # para o typechecker

        rule = PriceUpdateRule(timescale_pool=FailingPool())
        event = make_price_event()

        with pytest.raises(DatabaseError):
            await rule.apply(event)


class TestParseTimestamp:
    def test_iso_string_with_tz(self) -> None:
        ts = _parse_timestamp("2025-01-15T10:30:00+00:00")
        assert ts.tzinfo is not None
        assert ts.year == 2025

    def test_iso_string_without_tz_gets_utc(self) -> None:
        ts = _parse_timestamp("2025-01-15T10:30:00")
        assert ts.tzinfo is not None

    def test_none_returns_now(self) -> None:
        ts = _parse_timestamp(None)
        assert ts.tzinfo is not None

    def test_invalid_string_returns_now(self) -> None:
        ts = _parse_timestamp("nao-e-uma-data")
        assert ts.tzinfo is not None
