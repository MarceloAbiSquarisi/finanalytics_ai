"""
Worker assincrono que consome eventos e delega ao EventProcessorService.

Suporta dois backends: Kafka (producao) e InMemoryQueue (testes/dev).

Decisao de retry: tenacity com backoff exponencial jittered.
Motivo do jitter: evita thundering herd quando multiplos workers
reiniciam simultaneamente apos falha.

Decisao sobre concorrencia: asyncio.Semaphore para limitar processamento
paralelo. Alternativa seria usar asyncio.gather() com chunks fixos,
mas Semaphore oferece controle mais fino e backpressure natural.

O consumer nao conhece as regras de negocio — apenas orquestra o fluxo
de mensagens e delega ao servico.
"""
from __future__ import annotations

import asyncio
from datetime import UTC
from typing import TYPE_CHECKING

import structlog
import tenacity

from finanalytics_ai.domain.events.exceptions import PermanentError, TransientError
from finanalytics_ai.domain.events.models import DomainEvent, EventPayload
from finanalytics_ai.domain.events.value_objects import CorrelationId, EventType

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from finanalytics_ai.application.event_processor.service import EventProcessorService

logger = structlog.get_logger(__name__)


def _make_retry_policy(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> tenacity.AsyncRetrying:
    """
    Politica de retry: backoff exponencial com jitter full.
    Jitter full: delay = random(0, min(max_delay, base * 2^attempt))
    Melhor distribuicao que jitter fixo para evitar sincronizacao de retries.
    """
    return tenacity.AsyncRetrying(
        retry=tenacity.retry_if_exception_type(TransientError),
        stop=tenacity.stop_after_attempt(max_retries + 1),
        wait=tenacity.wait_random_exponential(multiplier=base_delay, max=max_delay),
        reraise=True,
        before_sleep=tenacity.before_sleep_log(logger, "WARNING"),  # type: ignore[arg-type]
    )


class EventConsumerWorker:
    """
    Worker que consome eventos de uma fila/stream e processa via servico.

    Injecao de dependencia: recebe o servico pronto (ja montado pela factory).
    Nao sabe como o servico foi construido.
    """

    def __init__(
        self,
        service: EventProcessorService,
        *,
        concurrency: int = 10,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        retry_max_delay: float = 30.0,
    ) -> None:
        self._service = service
        self._semaphore = asyncio.Semaphore(concurrency)
        self._retry_policy = _make_retry_policy(max_retries, retry_base_delay, retry_max_delay)
        self._running = False

    async def run(self, message_source: AsyncIterator[dict]) -> None:  # type: ignore[type-arg]
        """
        Loop principal de consumo.
        message_source: qualquer iterador async que produza dicts de mensagens.
        Exemplos: KafkaConsumer, RedisStream, InMemoryQueue.
        """
        self._running = True
        logger.info("consumer.started")
        tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]

        try:
            async for raw_message in message_source:
                if not self._running:
                    break

                task = asyncio.create_task(
                    self._process_with_semaphore(raw_message)
                )
                tasks.add(task)
                task.add_done_callback(tasks.discard)

        finally:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("consumer.stopped")

    async def stop(self) -> None:
        self._running = False

    async def _process_with_semaphore(self, raw_message: dict) -> None:  # type: ignore[type-arg]
        async with self._semaphore:
            await self._process_message(raw_message)

    async def _process_message(self, raw_message: dict) -> None:  # type: ignore[type-arg]
        log = logger.bind(raw_message_keys=list(raw_message.keys()))
        try:
            event = _deserialize_event(raw_message)
            log = log.bind(event_id=str(event.event_id), event_type=str(event.payload.event_type))

            async for attempt in self._retry_policy:
                with attempt:
                    await self._service.process(event)

        except PermanentError as exc:
            log.error("consumer.permanent_error", error=str(exc))
            # Dead-letter ja foi marcado no servico
        except TransientError as exc:
            log.error("consumer.max_retries_exceeded", error=str(exc))
            # Considerar envio para DLQ externa (Kafka DLQ topic)
        except Exception as exc:
            log.exception("consumer.unexpected_error", error=str(exc))


def _deserialize_event(raw: dict) -> DomainEvent:  # type: ignore[type-arg]
    """
    Desserializa uma mensagem bruta em DomainEvent.
    Ponto de entrada de dados externos — validacao acontece aqui.

    Decisao: validacao manual em vez de Pydantic para evitar dependencia
    de framework na borda do consumer. Se o schema ficar complexo,
    migrar para Pydantic model de entrada (DTO de entrada).
    """
    from finanalytics_ai.domain.events.exceptions import InvalidEventError

    required_fields = {"event_id", "event_type", "source", "data"}
    missing = required_fields - raw.keys()
    if missing:
        raise InvalidEventError(f"Mensagem invalida: campos ausentes {missing}")

    try:
        import uuid
        payload = EventPayload(
            event_type=EventType(raw["event_type"]),
            data=raw["data"],
            source=raw["source"],
            correlation_id=(
                CorrelationId(raw["correlation_id"])
                if raw.get("correlation_id")
                else None
            ),
        )
        from datetime import datetime
        return DomainEvent(
            event_id=uuid.UUID(raw["event_id"]),
            payload=payload,
            status=__import__(
                "finanalytics_ai.domain.events.models", fromlist=["EventStatus"]
            ).EventStatus.PENDING,
            created_at=datetime.now(tz=UTC),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidEventError(f"Falha ao desserializar evento: {exc}") from exc
