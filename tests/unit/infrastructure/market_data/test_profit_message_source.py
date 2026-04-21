"""
Testes unitarios do ProfitDLLMessageSource.

Usa NoOpProfitClient + InMemoryQueue para simular ticks sem DLL.
Todos os testes rodam em qualquer SO.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
import uuid

import pytest

from finanalytics_ai.infrastructure.market_data.profit_dll.message_source import (
    ProfitDLLMessageSource,
    _tick_to_event,
)


@dataclass
class FakeTick:
    """Simula o dataclass de tick do ProfitDLLClient."""

    ticker: str
    price: float
    volume: float
    exchange: str = "B"
    timestamp: datetime | None = None


class FakeProfitClient:
    """Client fake com asyncio.Queue para simular ticks."""

    def __init__(self) -> None:
        self._tick_queue: asyncio.Queue = asyncio.Queue()

    async def put_tick(self, tick: Any) -> None:
        await self._tick_queue.put(tick)


class TestTickToEvent:
    def test_dataclass_tick_converted(self) -> None:
        tick = FakeTick("PETR4", 38.5, 1000.0)
        result = _tick_to_event(tick)

        assert result is not None
        assert result["event_type"] == "price.update"
        assert result["source"] == "profit_dll"
        assert result["data"]["ticker"] == "PETR4"
        assert result["data"]["price"] == 38.5
        assert result["data"]["volume"] == 1000.0
        assert "event_id" in result
        # event_id deve ser UUID valido
        uuid.UUID(result["event_id"])

    def test_dict_tick_converted(self) -> None:
        tick = {"ticker": "VALE3", "price": 95.0, "volume": 500.0}
        result = _tick_to_event(tick)

        assert result is not None
        assert result["data"]["ticker"] == "VALE3"

    def test_ticker_uppercased(self) -> None:
        tick = FakeTick("petr4", 38.5, 0.0)
        result = _tick_to_event(tick)
        assert result is not None
        assert result["data"]["ticker"] == "PETR4"

    def test_zero_price_returns_none(self) -> None:
        tick = FakeTick("PETR4", 0.0, 0.0)
        result = _tick_to_event(tick)
        assert result is None

    def test_empty_ticker_returns_none(self) -> None:
        tick = FakeTick("", 38.5, 0.0)
        result = _tick_to_event(tick)
        assert result is None

    def test_timestamp_preserved(self) -> None:
        ts = datetime(2025, 1, 15, 10, 30, tzinfo=UTC)
        tick = FakeTick("PETR4", 38.5, 0.0, timestamp=ts)
        result = _tick_to_event(tick)
        assert result is not None
        assert "2025-01-15T10:30:00" in result["data"]["timestamp"]

    def test_naive_timestamp_gets_utc(self) -> None:
        ts = datetime(2025, 1, 15, 10, 30)  # sem tzinfo
        tick = FakeTick("PETR4", 38.5, 0.0, timestamp=ts)
        result = _tick_to_event(tick)
        assert result is not None
        assert "+00:00" in result["data"]["timestamp"]

    def test_dict_tick_invalid_price(self) -> None:
        tick = {"ticker": "PETR4", "price": -1.0, "volume": 0.0}
        result = _tick_to_event(tick)
        assert result is None

    def test_each_tick_gets_unique_event_id(self) -> None:
        tick = FakeTick("PETR4", 38.5, 0.0)
        ids = {_tick_to_event(tick)["event_id"] for _ in range(10)}
        assert len(ids) == 10  # todos diferentes


@pytest.mark.asyncio
class TestProfitDLLMessageSource:
    async def test_iterates_ticks(self) -> None:
        client = FakeProfitClient()
        source = ProfitDLLMessageSource(client, poll_timeout=0.05)

        tick = FakeTick("PETR4", 38.5, 1000.0)
        await client.put_tick(tick)

        messages: list[dict] = []

        async def _collect() -> None:
            async for msg in source:
                messages.append(msg)
                await source.stop()

        await asyncio.wait_for(_collect(), timeout=2.0)
        assert len(messages) == 1
        assert messages[0]["data"]["ticker"] == "PETR4"

    async def test_skips_invalid_ticks(self) -> None:
        client = FakeProfitClient()
        source = ProfitDLLMessageSource(client, poll_timeout=0.05)

        await client.put_tick(FakeTick("", 0.0, 0.0))  # invalido
        await client.put_tick(FakeTick("VALE3", 95.0, 0.0))  # valido

        messages: list[dict] = []

        async def _collect() -> None:
            async for msg in source:
                messages.append(msg)
                if len(messages) >= 1:
                    await source.stop()

        await asyncio.wait_for(_collect(), timeout=2.0)
        assert len(messages) == 1
        assert messages[0]["data"]["ticker"] == "VALE3"

    async def test_stop_halts_iteration(self) -> None:
        client = FakeProfitClient()
        source = ProfitDLLMessageSource(client, poll_timeout=0.05)

        collected = 0

        async def _collect() -> None:
            nonlocal collected
            async for _ in source:
                collected += 1

        task = asyncio.create_task(_collect())
        await asyncio.sleep(0.1)
        await source.stop()
        await asyncio.wait_for(task, timeout=1.0)
        assert collected == 0  # nenhum tick foi publicado

    async def test_no_queue_attribute_is_safe(self) -> None:
        class BadClient:
            pass  # sem _tick_queue

        source = ProfitDLLMessageSource(BadClient(), poll_timeout=0.05)
        messages = []
        async for msg in source:
            messages.append(msg)
        # Deve sair imediatamente sem erro
        assert messages == []

    async def test_multiple_ticks_in_sequence(self) -> None:
        client = FakeProfitClient()
        source = ProfitDLLMessageSource(client, poll_timeout=0.05)

        tickers = ["PETR4", "VALE3", "ITUB4"]
        for t in tickers:
            await client.put_tick(FakeTick(t, 38.5, 0.0))

        collected: list[str] = []

        async def _collect() -> None:
            async for msg in source:
                collected.append(msg["data"]["ticker"])
                if len(collected) >= len(tickers):
                    await source.stop()

        await asyncio.wait_for(_collect(), timeout=3.0)
        assert collected == tickers
