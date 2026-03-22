"""
EventPublisher — helper de publicação de eventos.

Responsabilidade única: criar um Event com o payload correto e persisti-lo
na tabela `events` + criar o EventProcessingRecord inicial como 'pending'.

Por que não chamar o repositório diretamente nos serviços?
    O publisher encapsula:
    1. Validação mínima do payload antes de persistir.
    2. Geração do correlation_id a partir do contexto atual.
    3. Um ponto único para adicionar tracing/sampling no futuro.

Uso em FintzSyncService:
    publisher = EventPublisher(session)
    await publisher.publish(
        EventType.FINTZ_SYNC_COMPLETED,
        payload={"dataset": dataset, "rows_synced": n, ...},
        source="fintz_sync_worker",
    )

O event_worker vai pegar o evento no próximo poll e processar.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from finanalytics_ai.domain.events.entities import (
    Event,
    EventId,
    EventProcessingRecord,
    EventStatus,
    EventType,
)
from finanalytics_ai.infrastructure.database.repositories.event_repository import (
    PostgresEventRepository,
)
from finanalytics_ai.observability.logging import get_logger

log = get_logger(__name__)


class EventPublisher:
    """Publica eventos no pipeline assíncrono.

    Thread/coroutine safe: cada instância usa sua própria sessão.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = PostgresEventRepository(session)

    async def publish(
        self,
        event_type: EventType,
        payload: dict,
        source: str,
        correlation_id: str | None = None,
    ) -> Event:
        """Cria, persiste e enfileira o evento para processamento assíncrono.

        Retorna o Event criado (com ID gerado) para uso no caller se necessário.
        É idempotente via ON CONFLICT DO NOTHING: publicar duas vezes o mesmo
        evento (mesmo event_id) não cria duplicata.
        """
        event = Event.create(
            event_type=event_type,
            payload=payload,
            source=source,
            correlation_id=correlation_id,
        )

        record = EventProcessingRecord(
            event_id=event.id,
            status=EventStatus.PENDING,
        )

        await self._repo.save_event(event)
        await self._repo.upsert_processing_record(record)

        log.info(
            "event_published",
            event_id=str(event.id),
            event_type=event_type.value,
            source=source,
        )
        return event

    async def publish_fintz_sync_completed(
        self,
        dataset: str,
        rows_synced: int,
        errors: int,
        duration_s: float,
        source: str = "fintz_sync_worker",
    ) -> Event:
        """Shortcut tipado para FINTZ_SYNC_COMPLETED.

        Garante que o payload sempre tem os campos que FintzSyncCompletedRule espera.
        Evita magic dicts espalhados pelo código.
        """
        return await self.publish(
            EventType.FINTZ_SYNC_COMPLETED,
            payload={
                "dataset": dataset,
                "rows_synced": rows_synced,
                "errors": errors,
                "duration_s": duration_s,
            },
            source=source,
        )

    async def publish_fintz_sync_failed(
        self,
        dataset: str,
        error_type: str,
        error_message: str,
        attempt: int = 1,
        source: str = "fintz_sync_worker",
    ) -> Event:
        """Shortcut tipado para FINTZ_SYNC_FAILED."""
        return await self.publish(
            EventType.FINTZ_SYNC_FAILED,
            payload={
                "dataset": dataset,
                "error_type": error_type,
                "error_message": error_message,
                "attempt": attempt,
            },
            source=source,
        )
