"""
Adapter Kafka para o EventConsumerWorker.

Implementa AsyncIterator[dict] que o EventConsumerWorker consome.
O worker nao sabe que existe Kafka — conhece apenas o iterador.

Decisao de design: KafkaMessageSource e um AsyncIterator puro.
Alternativa seria passar o consumer diretamente ao worker, mas isso
acoplaria o worker ao aiokafka. O iterador permite trocar por Redis Streams,
SQS, ou InMemoryQueue sem alterar o worker.

Retry de conexao: tenacity com backoff exponencial.
Se o Kafka nao estiver disponivel no startup, o worker retentar
ate MAX_CONNECT_RETRIES antes de levantar.

Graceful shutdown: o metodo stop() sinaliza para o iterador parar apos
a mensagem atual. O consumer do aiokafka faz commit antes de fechar.
"""
from __future__ import annotations
import contextlib

import asyncio
import json
from typing import TYPE_CHECKING, Any

import structlog
import tenacity
from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaConnectionError, KafkaError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger(__name__)

MAX_CONNECT_RETRIES = 10
CONNECT_BASE_DELAY = 2.0
CONNECT_MAX_DELAY = 60.0


class KafkaMessageSource:
    """
    AsyncIterator[dict] backed by aiokafka.

    Uso:
        source = KafkaMessageSource(
            bootstrap_servers="localhost:9092",
            topic="finanalytics.events",
            group_id="event-processor",
        )
        async for message in source:
            await worker.process(message)
    """

    def __init__(
        self,
        *,
        bootstrap_servers: str,
        topic: str,
        group_id: str,
        auto_offset_reset: str = "earliest",
        max_poll_records: int = 10,
        session_timeout_ms: int = 30_000,
        heartbeat_interval_ms: int = 10_000,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._topic = topic
        self._group_id = group_id
        self._auto_offset_reset = auto_offset_reset
        self._max_poll_records = max_poll_records
        self._session_timeout_ms = session_timeout_ms
        self._heartbeat_interval_ms = heartbeat_interval_ms
        self._consumer: AIOKafkaConsumer | None = None
        self._running = False

    async def _connect(self) -> None:
        """Conecta ao Kafka com retry exponencial."""

        @tenacity.retry(
            retry=tenacity.retry_if_exception_type((KafkaConnectionError, OSError)),
            stop=tenacity.stop_after_attempt(MAX_CONNECT_RETRIES),
            wait=tenacity.wait_random_exponential(
                multiplier=CONNECT_BASE_DELAY, max=CONNECT_MAX_DELAY
            ),
            before_sleep=tenacity.before_sleep_log(logger, "WARNING"),  # type: ignore[arg-type]
            reraise=True,
        )
        async def _try_connect() -> None:
            consumer = AIOKafkaConsumer(
                self._topic,
                bootstrap_servers=self._bootstrap_servers,
                group_id=self._group_id,
                auto_offset_reset=self._auto_offset_reset,
                max_poll_records=self._max_poll_records,
                session_timeout_ms=self._session_timeout_ms,
                heartbeat_interval_ms=self._heartbeat_interval_ms,
                enable_auto_commit=False,
                value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            )
            await consumer.start()
            self._consumer = consumer
            logger.info(
                "kafka.consumer.connected",
                topic=self._topic,
                group_id=self._group_id,
            )

        await _try_connect()

    async def stop(self) -> None:
        self._running = False
        if self._consumer is not None:
            await self._consumer.stop()
            logger.info("kafka.consumer.stopped")

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[dict[str, Any]]:
        await self._connect()
        assert self._consumer is not None
        self._running = True

        try:
            async for msg in self._consumer:
                if not self._running:
                    break

                if not isinstance(msg.value, dict):
                    logger.warning(
                        "kafka.message.invalid_format",
                        topic=msg.topic,
                        partition=msg.partition,
                        offset=msg.offset,
                    )
                    await self._consumer.commit()
                    continue

                logger.debug(
                    "kafka.message.received",
                    topic=msg.topic,
                    partition=msg.partition,
                    offset=msg.offset,
                )
                yield msg.value

                # Commit apos yield (processamento bem-sucedido ou erro tratado)
                # O worker trata excecoes internamente — se chegar aqui, commitamos
                await self._consumer.commit()

        except KafkaError as exc:
            logger.error("kafka.consumer.error", error=str(exc))
            raise
        finally:
            if self._consumer is not None:
                with contextlib.suppress(Exception):
                    await self._consumer.stop()


class InMemoryMessageSource:
    """
    AsyncIterator[dict] in-memory para desenvolvimento e testes de integracao.

    Permite injetar mensagens via put() e o iterador para quando a fila
    estiver vazia e stop() for chamado.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._running = False

    async def put(self, message: dict[str, Any]) -> None:
        await self._queue.put(message)

    async def stop(self) -> None:
        self._running = False
        await self._queue.put(None)  # sentinel

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[dict[str, Any]]:
        self._running = True
        while self._running:
            item = await self._queue.get()
            if item is None:
                break
            yield item

