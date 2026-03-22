"""
Infrastructure — PostgresEventRepository.

Implementa EventRepository (Protocol do domínio) usando SQLAlchemy 2.x async.

Decisões:
- ON CONFLICT DO UPDATE garante idempotência no nível do banco.
- Sessão injetada via AsyncSession (não criamos engine aqui).
- Erros asyncpg são traduzidos para InfrastructureError do domínio.
- Nenhum import do domínio de negócio (apenas contratos do port).

Por que não usar o ORM do SQLAlchemy aqui?
    Para eventos de alta frequência, o overhead do ORM (instanciação de objetos,
    tracking de mudanças) não compensa. Usamos Core (text/insert/select) direto.
    Para entidades complexas (Portfolio, Alert), usamos ORM normalmente.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from finanalytics_ai.domain.events.entities import (
    Event,
    EventId,
    EventProcessingRecord,
    EventStatus,
    EventType,
)
from finanalytics_ai.exceptions import TransientDatabaseError


class PostgresEventRepository:
    """Repositório Postgres para eventos e seus registros de processamento."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ──────────────────────────────────────────────────────────────────────────
    # EventRepository implementation
    # ──────────────────────────────────────────────────────────────────────────

    async def save_event(self, event: Event) -> None:
        """Persiste o evento. Idempotente por event.id (ON CONFLICT DO NOTHING)."""
        stmt = text("""
            INSERT INTO events (id, event_type, payload, source, correlation_id, created_at)
            VALUES (:id, :event_type, CAST(:payload AS jsonb), :source, :correlation_id, :created_at)
            ON CONFLICT (id) DO NOTHING
        """)
        await self._execute(
            stmt,
            {
                "id": str(event.id),
                "event_type": event.event_type.value,
                "payload": json.dumps(event.payload),
                "source": event.source,
                "correlation_id": event.correlation_id,
                "created_at": event.created_at,
            },
        )

    async def get_processing_record(
        self, event_id: EventId
    ) -> EventProcessingRecord | None:
        stmt = text("""
            SELECT event_id, status, attempt, last_error, processed_at, result_metadata
            FROM event_processing_records
            WHERE event_id = :event_id
        """)
        result = await self._execute(stmt, {"event_id": str(event_id)})
        row = result.fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    async def upsert_processing_record(self, record: EventProcessingRecord) -> None:
        """ON CONFLICT UPDATE — garante que estado nunca regride acidentalmente."""
        stmt = text("""
            INSERT INTO event_processing_records
                (event_id, status, attempt, last_error, processed_at, result_metadata)
            VALUES
                (:event_id, :status, :attempt, :last_error, :processed_at, CAST(:result_metadata AS jsonb))
            ON CONFLICT (event_id) DO UPDATE SET
                status          = EXCLUDED.status,
                attempt         = EXCLUDED.attempt,
                last_error      = EXCLUDED.last_error,
                processed_at    = EXCLUDED.processed_at,
                result_metadata = EXCLUDED.result_metadata,
                updated_at      = NOW()
        """)
        await self._execute(
            stmt,
            {
                "event_id": str(record.event_id),
                "status": record.status.value,
                "attempt": record.attempt,
                "last_error": record.last_error,
                "processed_at": record.processed_at,
                "result_metadata": json.dumps(record.result_metadata),
            },
        )

    async def get_pending_events(
        self,
        event_type: EventType | None = None,
        limit: int = 100,
        for_update_skip_locked: bool = False,
    ) -> list[Event]:
        """Retorna eventos pendentes.

        for_update_skip_locked=True: adiciona `FOR UPDATE SKIP LOCKED` ao SQL.
        Use quando múltiplos workers precisam consumir eventos sem duplicação.
        Requer que a chamada esteja dentro de uma transação ativa.
        """
        lock_clause = "FOR UPDATE SKIP LOCKED" if for_update_skip_locked else ""

        if event_type is not None:
            stmt = text(f"""
                SELECT e.id, e.event_type, e.payload, e.source, e.correlation_id, e.created_at
                FROM events e
                JOIN event_processing_records r ON r.event_id = e.id
                WHERE r.status IN ('pending', 'failed')
                  AND e.event_type = :event_type
                ORDER BY e.created_at ASC
                LIMIT :limit
                {lock_clause}
            """)
            params: dict[str, Any] = {"event_type": event_type.value, "limit": limit}
        else:
            stmt = text(f"""
                SELECT e.id, e.event_type, e.payload, e.source, e.correlation_id, e.created_at
                FROM events e
                JOIN event_processing_records r ON r.event_id = e.id
                WHERE r.status IN ('pending', 'failed')
                ORDER BY e.created_at ASC
                LIMIT :limit
                {lock_clause}
            """)
            params = {"limit": limit}

        result = await self._execute(stmt, params)
        return [self._row_to_event(row) for row in result.fetchall()]

    async def get_dead_letter_events(
        self, limit: int = 50, offset: int = 0
    ) -> list[tuple[Event, EventProcessingRecord]]:
        """Lista eventos em dead-letter para o admin endpoint.

        Retorna pares (Event, EventProcessingRecord) para exibir contexto completo.
        """
        stmt = text("""
            SELECT
                e.id, e.event_type, e.payload, e.source, e.correlation_id, e.created_at,
                r.status, r.attempt, r.last_error, r.processed_at, r.result_metadata
            FROM events e
            JOIN event_processing_records r ON r.event_id = e.id
            WHERE r.status = 'dead_letter'
            ORDER BY e.created_at DESC
            LIMIT :limit OFFSET :offset
        """)
        result = await self._execute(stmt, {"limit": limit, "offset": offset})
        rows = result.fetchall()

        pairs: list[tuple[Event, EventProcessingRecord]] = []
        for row in rows:
            event = self._row_to_event(row)
            record = EventProcessingRecord(
                event_id=event.id,
                status=EventStatus(row.status),
                attempt=row.attempt,
                last_error=row.last_error,
                processed_at=row.processed_at,
                result_metadata=row.result_metadata
                if isinstance(row.result_metadata, dict)
                else json.loads(row.result_metadata or "{}"),
            )
            pairs.append((event, record))
        return pairs

    async def requeue_dead_letter(self, event_id: EventId) -> bool:
        """Recoloca um evento dead-letter em 'pending' para reprocessamento.

        Retorna True se o evento existia e foi recolocado, False se não encontrado.
        Reseta attempt para 0 para garantir que o evento tenha todas as retentativas.
        """
        stmt = text("""
            UPDATE event_processing_records
            SET status = 'pending',
                attempt = 0,
                last_error = NULL,
                updated_at = NOW()
            WHERE event_id = :event_id
              AND status = 'dead_letter'
        """)
        result = await self._execute(stmt, {"event_id": str(event_id)})
        return result.rowcount > 0  # type: ignore[union-attr]

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    async def _execute(self, stmt: Any, params: dict[str, Any]) -> Any:
        """Executa statement traduzindo erros asyncpg em InfrastructureError."""
        try:
            return await self._session.execute(stmt, params)
        except asyncpg.TooManyConnectionsError as exc:
            raise TransientDatabaseError("Muitas conexões ao banco") from exc
        except asyncpg.DeadlockDetectedError as exc:
            raise TransientDatabaseError("Deadlock detectado") from exc
        except asyncpg.PostgresConnectionError as exc:
            raise TransientDatabaseError(f"Falha de conexão: {exc}") from exc
        except Exception as exc:
            # Não re-wrapa outros erros para não perder stack trace
            raise exc

    @staticmethod
    def _row_to_event(row: Any) -> Event:
        return Event(
            id=EventId.from_str(str(row.id)),
            event_type=EventType(row.event_type),
            payload=row.payload if isinstance(row.payload, dict) else json.loads(row.payload),
            source=row.source,
            created_at=row.created_at.replace(tzinfo=timezone.utc)
            if row.created_at.tzinfo is None
            else row.created_at,
            correlation_id=row.correlation_id,
        )

    @staticmethod
    def _row_to_record(row: Any) -> EventProcessingRecord:
        return EventProcessingRecord(
            event_id=EventId.from_str(str(row.event_id)),
            status=EventStatus(row.status),
            attempt=row.attempt,
            last_error=row.last_error,
            processed_at=row.processed_at,
            result_metadata=row.result_metadata
            if isinstance(row.result_metadata, dict)
            else json.loads(row.result_metadata or "{}"),
        )
