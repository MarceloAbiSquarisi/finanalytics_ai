"""Smoke test do MarketDataProducer (Contrato C1).

Cobre:
  - bootstrap=None  -> producer noop, publish_tick nao chama nada
  - bootstrap setado -> producer.produce chamado, payload Avro decodifica de volta
    p/ os mesmos campos (round-trip schemaless)
  - aggressor mapping (1->BUY, -1->SELL, None/0->null)
  - BufferError no produce -> swallowed (nao raise)
"""

from __future__ import annotations

from decimal import Decimal
import io
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from finanalytics_ai.infrastructure.market_data.kafka_producer import (
    MarketDataProducer,
    _aggressor_to_avro,
)

# Path absoluto do schema replicado (test independe de cwd)
SCHEMA_PATH = Path(__file__).parents[4] / "contracts" / "upstream" / "kafka_market_data_v1.avsc"


@pytest.fixture
def schema_dict() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_aggressor_mapping():
    assert _aggressor_to_avro(1) == "BUY"
    assert _aggressor_to_avro(-1) == "SELL"
    assert _aggressor_to_avro(0) is None
    assert _aggressor_to_avro(None) is None


def test_disabled_when_no_bootstrap():
    producer = MarketDataProducer(bootstrap_servers=None)
    assert producer.enabled is False
    # publish e flush viram noop
    producer.publish_tick("WINFUT", 0, Decimal("100.0"), 1, 1)
    assert producer.flush() == 0


def test_publish_tick_round_trip(schema_dict):
    """Payload Avro produzido decodifica de volta p/ os campos originais."""
    fake_producer = MagicMock()

    p = MarketDataProducer(
        bootstrap_servers="kafka:9092",
        topic="market_data.ticks.v1",
        schema_path=SCHEMA_PATH,
        producer_factory=lambda config: fake_producer,
    )
    assert p.enabled is True

    p.publish_tick(
        symbol="WINFUT",
        ts_us=1714512000_000_000,
        price=Decimal("130000.5000"),
        volume=2,
        aggressor=1,
    )

    fake_producer.produce.assert_called_once()
    call_kwargs = fake_producer.produce.call_args.kwargs
    assert call_kwargs["topic"] == "market_data.ticks.v1"
    assert call_kwargs["key"] == b"WINFUT"

    # Decodifica o payload Avro de volta
    from fastavro import parse_schema, schemaless_reader

    parsed = parse_schema(schema_dict)
    record = schemaless_reader(io.BytesIO(call_kwargs["value"]), parsed)

    assert record["version"] == 1
    assert record["symbol"] == "WINFUT"
    assert record["ts_us"] == 1714512000_000_000
    assert record["price"] == Decimal("130000.5000")
    assert record["volume"] == 2
    assert record["aggressor"] == "BUY"


def test_publish_tick_aggressor_null(schema_dict):
    fake_producer = MagicMock()
    p = MarketDataProducer(
        bootstrap_servers="kafka:9092",
        schema_path=SCHEMA_PATH,
        producer_factory=lambda config: fake_producer,
    )
    p.publish_tick("WINFUT", 1, 100.0, 1, aggressor=None)

    from fastavro import parse_schema, schemaless_reader

    parsed = parse_schema(schema_dict)
    record = schemaless_reader(io.BytesIO(fake_producer.produce.call_args.kwargs["value"]), parsed)
    assert record["aggressor"] is None


def test_buffer_full_does_not_raise():
    fake_producer = MagicMock()
    fake_producer.produce.side_effect = BufferError("queue full")

    p = MarketDataProducer(
        bootstrap_servers="kafka:9092",
        schema_path=SCHEMA_PATH,
        producer_factory=lambda config: fake_producer,
    )
    # Nao deve raise — drop silencioso com log warning
    p.publish_tick("WINFUT", 0, Decimal("100"), 1, 1)


def test_flush_returns_count():
    fake_producer = MagicMock()
    fake_producer.flush.return_value = 3

    p = MarketDataProducer(
        bootstrap_servers="kafka:9092",
        schema_path=SCHEMA_PATH,
        producer_factory=lambda config: fake_producer,
    )
    assert p.flush(timeout_s=2.0) == 3
    fake_producer.flush.assert_called_once_with(2.0)
