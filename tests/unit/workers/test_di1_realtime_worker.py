"""
Testes unitarios — di1_realtime_worker.

Foco: fluxo de poll -> publish sem I/O real (Kafka, Postgres, profit_agent).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from finanalytics_ai.workers import di1_realtime_worker as m


class FakePoolAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return False


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return FakePoolAcquire(self._conn)


@pytest.mark.asyncio
async def test_poll_publishes_new_ticks_to_kafka(monkeypatch):
    m.METRICS = m.Metrics()

    row = {
        "time":         datetime(2026, 4, 20, 14, 0, tzinfo=timezone.utc),
        "ticker":       "DI1F27",
        "price":        12.85,
        "quantity":     5,
        "volume":       64.25,
        "buy_agent":    308,
        "sell_agent":   3,
        "trade_number": 1001,
        "trade_type":   3,
    }

    conn = AsyncMock()
    conn.fetch.return_value = [row]
    pool = FakePool(conn)

    producer = AsyncMock()
    producer.send_and_wait = AsyncMock()

    monkeypatch.setattr(m, "CONTRACTS", ["DI1F27"])

    worker = m.DI1RealtimeWorker()
    worker._pool = pool
    worker._producer = producer
    worker._last_trade_number = {"DI1F27": 1000}

    await worker._poll_once()

    producer.send_and_wait.assert_awaited_once()
    call_kwargs = producer.send_and_wait.await_args.kwargs
    assert call_kwargs["value"]["ticker"] == "DI1F27"
    assert call_kwargs["value"]["price"] == 12.85
    assert call_kwargs["value"]["trade_number"] == 1001
    assert worker._last_trade_number["DI1F27"] == 1001
    assert m.METRICS.ticks_total == 1
    assert m.METRICS.kafka_published_total == 1


@pytest.mark.asyncio
async def test_poll_skips_when_no_new_rows(monkeypatch):
    m.METRICS = m.Metrics()

    conn = AsyncMock()
    conn.fetch.return_value = []
    pool = FakePool(conn)
    producer = AsyncMock()
    producer.send_and_wait = AsyncMock()

    monkeypatch.setattr(m, "CONTRACTS", ["DI1F27"])

    worker = m.DI1RealtimeWorker()
    worker._pool = pool
    worker._producer = producer
    worker._last_trade_number = {"DI1F27": 500}

    await worker._poll_once()

    producer.send_and_wait.assert_not_awaited()
    assert m.METRICS.ticks_total == 0


@pytest.mark.asyncio
async def test_poll_counts_kafka_error_and_continues(monkeypatch):
    m.METRICS = m.Metrics()

    row = {
        "time":         datetime(2026, 4, 20, 14, 0, tzinfo=timezone.utc),
        "ticker":       "DI1F27",
        "price":        12.85,
        "quantity":     5,
        "volume":       64.25,
        "buy_agent":    308,
        "sell_agent":   3,
        "trade_number": 1001,
        "trade_type":   3,
    }

    conn = AsyncMock()
    conn.fetch.return_value = [row]
    pool = FakePool(conn)

    producer = AsyncMock()
    producer.send_and_wait = AsyncMock(side_effect=RuntimeError("kafka down"))

    monkeypatch.setattr(m, "CONTRACTS", ["DI1F27"])

    worker = m.DI1RealtimeWorker()
    worker._pool = pool
    worker._producer = producer
    worker._last_trade_number = {"DI1F27": 1000}

    await worker._poll_once()

    assert m.METRICS.kafka_errors_total == 1
    assert m.METRICS.ticks_total == 0
    assert worker._last_trade_number["DI1F27"] == 1000


def test_metrics_render_prom_format():
    metrics = m.Metrics()
    metrics.ticks_total = 42
    metrics.kafka_published_total = 42
    metrics.ticks_per_contract = {"DI1F27": 30, "DI1F28": 12}
    text = metrics.render_prom()

    assert "di1_worker_uptime_seconds" in text
    assert "di1_worker_ticks_total 42" in text
    assert "di1_worker_kafka_published_total 42" in text
    assert 'di1_worker_ticks_per_contract_total{contract="DI1F27"} 30' in text
