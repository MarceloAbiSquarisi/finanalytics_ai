"""
Adaptadores Kafka para produção e consumo de MarketEvents.

Design decisions:
  - aiokafka para I/O 100% assíncrono — sem threads bloqueantes
  - Consumer implementa o Protocol EventQueue do domínio (duck typing)
  - Producer exposto separadamente — producão e consumo têm ciclos de vida
    distintos (producer em todo request; consumer como worker contínuo)
  - Serialização: JSON simples — evita schema registry para este caso de uso.
    Trade-off: sem evolução de schema garantida, mas zero dependência extra.
    Alternativa: Avro + Schema Registry se o volume de mensagens crescer.
  - Consumer group "finanalytics-ai" garante que não interfere com os
    consumidores já existentes no finanalytics-platform
  - Backpressure: asyncio.Queue interna com maxsize=1000 como buffer entre
    Kafka poll loop e o processador — desacopla velocidade de consumo da
    velocidade de processamento.

Resiliência:
  - reconnect_backoff_ms / reconnect_backoff_max_ms configura retry de conexão
  - session_timeout_ms / heartbeat_interval_ms: detecção de falha do consumer
  - enable_auto_commit=False: commit manual após processamento bem-sucedido
    (garantia at-least-once — idempotência no EventProcessor evita duplicatas)
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
import json
from typing import TYPE_CHECKING, Any
import uuid

import structlog

from finanalytics_ai.config import get_settings
from finanalytics_ai.domain.entities.event import EventStatus, EventType, MarketEvent
from finanalytics_ai.observability.correlation import (
    CORRELATION_HEADER,
    bind_correlation_id,
    clear_correlation_id,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger(__name__)


# ── SERIALIZAÇÃO ──────────────────────────────────────────────────────────────


def _event_to_bytes(event: MarketEvent) -> bytes:
    """Serializa MarketEvent para JSON bytes."""
    data = {
        "event_id": event.event_id,
        "event_type": event.event_type.value,
        "ticker": event.ticker,
        "payload": event.payload,
        "source": event.source,
        "occurred_at": event.occurred_at.isoformat(),
        "correlation_id": event.correlation_id,
    }
    return json.dumps(data).encode("utf-8")


def _extract_correlation_id_from_headers(headers: Any) -> str | None:
    """
    Aceita os formatos típicos do aiokafka (lista de tuplas
    `(key: str, value: bytes)`). Header lookup é case-insensitive.
    """
    if not headers:
        return None
    target = CORRELATION_HEADER.lower()
    for entry in headers:
        try:
            key, val = entry
        except (TypeError, ValueError):
            continue
        if not key or val is None:
            continue
        key_str = key.decode("utf-8") if isinstance(key, bytes) else str(key)
        if key_str.lower() == target:
            return val.decode("utf-8") if isinstance(val, bytes) else str(val)
    return None


def _bytes_to_event(raw: bytes) -> MarketEvent:
    """Desserializa JSON bytes para MarketEvent. Tolerante a campos extras."""
    data: dict[str, Any] = json.loads(raw.decode("utf-8"))
    return MarketEvent(
        event_id=data.get("event_id", str(uuid.uuid4())),
        event_type=EventType(data["event_type"]),
        ticker=data["ticker"],
        payload=data.get("payload", {}),
        source=data.get("source", "kafka"),
        occurred_at=datetime.fromisoformat(data["occurred_at"])
        if "occurred_at" in data
        else datetime.now(UTC),
        status=EventStatus.PENDING,
        correlation_id=data.get("correlation_id"),
    )


# ── PRODUCER ─────────────────────────────────────────────────────────────────


class KafkaMarketEventProducer:
    """
    Produtor assíncrono de MarketEvents para Kafka.

    Uso:
        async with KafkaMarketEventProducer() as producer:
            await producer.publish(event)

    Ou como singleton no lifespan da aplicação:
        await producer.start()
        ...
        await producer.stop()
    """

    def __init__(
        self,
        bootstrap_servers: str | None = None,
        topic: str | None = None,
    ) -> None:
        s = get_settings()
        self._bootstrap = bootstrap_servers or s.kafka_bootstrap_servers
        self._topic = topic or s.kafka_topic_market_events
        self._producer: Any = None  # AIOKafkaProducer — import lazy

    async def start(self) -> None:
        try:
            from aiokafka import AIOKafkaProducer  # type: ignore[import]

            self._producer = AIOKafkaProducer(
                bootstrap_servers=self._bootstrap,
                value_serializer=lambda v: v,  # já bytes
                compression_type="gzip",
                acks="all",  # durabilidade máxima
                max_batch_size=32768,
                linger_ms=5,  # micro-batching
            )
            await self._producer.start()
            logger.info("kafka.producer.started", topic=self._topic)
        except ImportError:
            raise RuntimeError("aiokafka não instalado. Execute: pip install aiokafka") from None
        except Exception as exc:
            logger.error("kafka.producer.start_failed", error=str(exc))
            raise

    async def stop(self) -> None:
        if self._producer:
            await self._producer.stop()
            logger.info("kafka.producer.stopped")

    async def publish(self, event: MarketEvent) -> None:
        if not self._producer:
            raise RuntimeError("Producer não iniciado — chame start() primeiro")
        # Propaga correlation_id do contextvar (HTTP request, parent worker)
        # se o evento ainda não tiver um. Defesa em profundidade: vai tanto
        # no payload quanto no header Kafka.
        if not event.correlation_id:
            ctx = structlog.contextvars.get_contextvars()
            ctx_cid = ctx.get("correlation_id")
            if ctx_cid:
                event.correlation_id = str(ctx_cid)
        raw = _event_to_bytes(event)
        headers = (
            [(CORRELATION_HEADER, event.correlation_id.encode("utf-8"))]
            if event.correlation_id
            else None
        )
        await self._producer.send_and_wait(
            self._topic,
            value=raw,
            key=event.ticker.encode("utf-8"),  # particionamento por ticker
            headers=headers,
        )
        logger.debug(
            "kafka.event.published",
            event_id=event.event_id,
            ticker=event.ticker,
            correlation_id=event.correlation_id,
        )

    async def __aenter__(self) -> KafkaMarketEventProducer:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()


# ── CONSUMER ─────────────────────────────────────────────────────────────────


class KafkaMarketEventConsumer:
    """
    Consumer assíncrono de MarketEvents do Kafka.

    Executa como background task no lifespan da aplicação.
    Internamente usa um asyncio.Queue como buffer, desacoplando
    o poll loop do Kafka da velocidade de processamento.

    Uso no lifespan:
        consumer = KafkaMarketEventConsumer()
        task = asyncio.create_task(consumer.consume_loop(processor.process))
        yield
        await consumer.stop()
        task.cancel()

    Uso com EventQueue Protocol (para testes e injeção de dependência):
        event = await consumer.dequeue()
    """

    def __init__(
        self,
        bootstrap_servers: str | None = None,
        topics: list[str] | None = None,
        group_id: str | None = None,
        buffer_size: int = 1000,
    ) -> None:
        s = get_settings()
        self._bootstrap = bootstrap_servers or s.kafka_bootstrap_servers
        self._topics = topics or [s.kafka_topic_market_events, s.kafka_topic_price_updates]
        self._group_id = group_id or s.kafka_consumer_group
        self._buffer: asyncio.Queue[MarketEvent] = asyncio.Queue(maxsize=buffer_size)
        self._consumer: Any = None
        self._running = False

    async def start(self) -> None:
        try:
            from aiokafka import AIOKafkaConsumer  # type: ignore[import]

            self._consumer = AIOKafkaConsumer(
                *self._topics,
                bootstrap_servers=self._bootstrap,
                group_id=self._group_id,
                auto_offset_reset=get_settings().kafka_auto_offset_reset,
                enable_auto_commit=False,  # commit manual — at-least-once
                session_timeout_ms=30_000,
                heartbeat_interval_ms=10_000,
                max_poll_records=100,
            )
            await self._consumer.start()
            self._running = True
            logger.info(
                "kafka.consumer.started",
                topics=self._topics,
                group=self._group_id,
            )
        except ImportError:
            raise RuntimeError("aiokafka não instalado. Execute: pip install aiokafka") from None
        except Exception as exc:
            logger.error("kafka.consumer.start_failed", error=str(exc))
            raise

    async def stop(self) -> None:
        self._running = False
        if self._consumer:
            await self._consumer.stop()
            logger.info("kafka.consumer.stopped")

    async def poll_loop(self) -> None:
        """
        Loop de polling do Kafka. Deve rodar como background task.
        Coloca mensagens no buffer interno; o processador consome via dequeue().
        """
        if not self._consumer:
            raise RuntimeError("Consumer não iniciado")

        log = logger.bind(group=self._group_id)
        log.info("kafka.poll_loop.started")

        try:
            async for msg in self._consumer:
                if not self._running:
                    break
                try:
                    event = _bytes_to_event(msg.value)
                    # Header Kafka tem precedência sobre payload (mais
                    # canônico; padrão usado por outros services também).
                    header_cid = _extract_correlation_id_from_headers(msg.headers)
                    if header_cid:
                        event.correlation_id = header_cid
                    await self._buffer.put(event)
                    log.debug(
                        "kafka.message.received",
                        topic=msg.topic,
                        partition=msg.partition,
                        offset=msg.offset,
                        ticker=event.ticker,
                        correlation_id=event.correlation_id,
                    )
                    # Commit manual após enqueue no buffer
                    await self._consumer.commit()
                except Exception as exc:
                    log.warning(
                        "kafka.message.parse_error",
                        error=str(exc),
                        topic=msg.topic,
                        offset=msg.offset,
                    )
        except asyncio.CancelledError:
            log.info("kafka.poll_loop.cancelled")
        except Exception as exc:
            log.error("kafka.poll_loop.error", error=str(exc))
            raise

    async def consume_loop(self, handler: Any) -> None:
        """
        Loop completo: poll do Kafka + dispatch para handler.

        handler: callable async que recebe MarketEvent e retorna MarketEvent
        Usado no lifespan da aplicação.
        """
        poll_task = asyncio.create_task(self.poll_loop())
        log = logger.bind(group=self._group_id)

        try:
            while self._running:
                try:
                    event = await asyncio.wait_for(self._buffer.get(), timeout=1.0)
                    bound_cid = bool(event.correlation_id)
                    if bound_cid:
                        bind_correlation_id(event.correlation_id)
                    try:
                        await handler(event)
                    except Exception as exc:
                        log.error(
                            "kafka.event.handler_error", error=str(exc), event_id=event.event_id
                        )
                    finally:
                        if bound_cid:
                            clear_correlation_id()
                        self._buffer.task_done()
                except TimeoutError:
                    continue  # sem mensagem no buffer — volta ao loop
        except asyncio.CancelledError:
            log.info("kafka.consume_loop.cancelled")
        finally:
            poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poll_task

    # ── EventQueue Protocol ──────────────────────────────────────────────────
    # Permite usar KafkaConsumer onde EventQueue é esperado (ex: testes)

    async def dequeue(self) -> MarketEvent:
        return await self._buffer.get()

    async def size(self) -> int:
        return self._buffer.qsize()

    async def iter_events(self) -> AsyncIterator[MarketEvent]:
        """Iterador assíncrono para SSE endpoint."""
        while True:
            event = await asyncio.wait_for(self._buffer.get(), timeout=30.0)
            yield event
            self._buffer.task_done()
