"""Producer Kafka sync para ticks B3 — Contrato C1 (`market_data.ticks.v1`).

Wrapper fino sobre `confluent_kafka.Producer` + `fastavro.schemaless_writer`.
Sync por design: roda dentro da thread do callback DLL no profit_agent (sync),
sem bridge async. Produce e poll(0) sao non-blocking.

Schema canonico em `contracts/upstream/kafka_market_data_v1.avsc` (replicado
do trading-engine). Decoder esperado: `KafkaMarketDataSource` em
src/trading_engine/infrastructure/kafka_market_data.py.

Lazy init via env:
    PROFIT_KAFKA_BOOTSTRAP=kafka:9092 ativa o producer.
    Sem essa env, MarketDataProducer.publish_tick() vira noop -> backward
    compatible com instalacoes que nao querem ainda usar Kafka.

Uso:
    producer = MarketDataProducer.from_env()
    producer.publish_tick(
        symbol="WINFUT",
        ts_us=1714512000_000_000,
        price=Decimal("130000.5000"),
        volume=1,
        aggressor=1,
    )
    producer.flush(timeout_s=5.0)  # no shutdown
"""

from __future__ import annotations

from decimal import Decimal
import io
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

log = logging.getLogger("kafka_producer")

# Path do schema Avro replicado em contracts/upstream/.
# Resolvido relativo ao root do projeto (cwd ou via PROFIT_KAFKA_SCHEMA_PATH).
DEFAULT_SCHEMA_PATH = "contracts/upstream/kafka_market_data_v1.avsc"


def _aggressor_to_avro(aggressor: int | None) -> str | None:
    """Mapeia agressor int (DLL) -> enum Avro (BUY/SELL/null)."""
    if aggressor == 1:
        return "BUY"
    if aggressor == -1:
        return "SELL"
    return None  # 0 = leilao/desconhecido


class MarketDataProducer:
    """Producer sync de ticks B3 para Kafka topic `market_data.ticks.v1`.

    Quando inicializado sem bootstrap, `publish_tick` vira noop. Producer
    pode ser instanciado em modulos onde Kafka pode ou nao estar disponivel.
    """

    def __init__(
        self,
        bootstrap_servers: str | None,
        topic: str = "market_data.ticks.v1",
        schema_path: str | os.PathLike[str] = DEFAULT_SCHEMA_PATH,
        producer_factory: Any = None,
        extra_config: Mapping[str, Any] | None = None,
    ) -> None:
        self._topic = topic
        self._enabled = bool(bootstrap_servers)
        self._producer: Any = None
        self._schema: Any = None

        if not self._enabled:
            log.info("kafka_producer.disabled bootstrap=%s", bootstrap_servers)
            return

        try:
            schema_dict = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.error("kafka_producer.schema_load_failed path=%s err=%s", schema_path, exc)
            self._enabled = False
            return

        # fastavro import diferido — pyproject ja garante a dep, mas evita
        # falhar import deste modulo em ambientes que nao instalaram o extra.
        try:
            from fastavro import parse_schema
        except ImportError as exc:
            log.error("kafka_producer.fastavro_missing err=%s", exc)
            self._enabled = False
            return
        self._schema = parse_schema(schema_dict)

        config: dict[str, Any] = {
            "bootstrap.servers": bootstrap_servers,
            "linger.ms": 5,  # micro-batch para reduzir overhead
            "compression.type": "lz4",
            "enable.idempotence": True,  # delivery exactly-once por sessao
            "acks": "all",
        }
        if extra_config:
            config.update(extra_config)

        if producer_factory is None:
            try:
                from confluent_kafka import Producer
            except ImportError as exc:
                log.error("kafka_producer.confluent_kafka_missing err=%s", exc)
                self._enabled = False
                return
            producer_factory = Producer

        self._producer = producer_factory(config)
        log.info("kafka_producer.ready topic=%s bootstrap=%s", topic, bootstrap_servers)

    @classmethod
    def from_env(cls) -> MarketDataProducer:
        """Instancia a partir de env vars (PROFIT_KAFKA_BOOTSTRAP/TOPIC)."""
        return cls(
            bootstrap_servers=os.getenv("PROFIT_KAFKA_BOOTSTRAP"),
            topic=os.getenv("PROFIT_KAFKA_TOPIC", "market_data.ticks.v1"),
            schema_path=os.getenv("PROFIT_KAFKA_SCHEMA_PATH", DEFAULT_SCHEMA_PATH),
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def publish_tick(
        self,
        symbol: str,
        ts_us: int,
        price: Decimal | float,
        volume: int,
        aggressor: int | None,
    ) -> None:
        """Publica 1 tick em `market_data.ticks.v1`. Noop se desabilitado."""
        if not self._enabled or self._producer is None:
            return

        # fastavro encoda decimal logical type a partir de Decimal — evita
        # float drift. Aceita float em testes/legacy callers convertendo.
        if not isinstance(price, Decimal):
            price = Decimal(str(price))

        record = {
            "version": 1,
            "symbol": symbol,
            "ts_us": int(ts_us),
            "price": price,
            "volume": int(volume),
            "aggressor": _aggressor_to_avro(aggressor),
        }
        buf = io.BytesIO()
        try:
            from fastavro import schemaless_writer

            schemaless_writer(buf, self._schema, record)
        except Exception as exc:
            log.warning("kafka_producer.encode_failed symbol=%s err=%s", symbol, exc)
            return

        try:
            self._producer.produce(
                topic=self._topic,
                key=symbol.encode("utf-8"),  # particiona por simbolo
                value=buf.getvalue(),
            )
            self._producer.poll(0)  # serve callbacks de delivery
        except BufferError:
            # buffer full: callback do DLL nao pode bloquear; melhor dropar
            # tick com log do que pausar o ingest.
            log.warning("kafka_producer.buffer_full symbol=%s — drop", symbol)
        except Exception as exc:
            log.warning("kafka_producer.produce_failed symbol=%s err=%s", symbol, exc)

    def flush(self, timeout_s: float = 5.0) -> int:
        """Drena buffer; retorna mensagens nao entregues (0 = ok)."""
        if not self._enabled or self._producer is None:
            return 0
        try:
            return int(self._producer.flush(timeout_s))
        except Exception as exc:
            log.warning("kafka_producer.flush_failed err=%s", exc)
            return -1
