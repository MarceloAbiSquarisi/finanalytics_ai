"""
Endpoints administrativos para gerenciamento de eventos.

POST /api/v1/events/{event_id}/reprocess
    Recoloca um evento FAILED ou DEAD_LETTER de volta para PENDING,
    liberando a chave de idempotencia para permitir reprocessamento.

GET /api/v1/events/dead-letter
    Lista eventos em dead-letter com paginacao.

Acesso: ADMIN ou MASTER apenas.

Decisao: endpoint de reprocessamento nao reexecuta o evento imediatamente --
apenas o marca como PENDING e libera a idempotencia. O worker vai pegalo
no proximo ciclo. Isso evita:
1. Sobrecarga de requests HTTP bloqueando enquanto o evento processa
2. Acoplamento entre a API e o worker
3. Problemas de timeout em eventos lentos
"""

from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from finanalytics_ai.domain.auth.entities import User
from finanalytics_ai.domain.events.models import EventStatus
from finanalytics_ai.infrastructure.event_processor.idempotency import RedisIdempotencyStore
from finanalytics_ai.infrastructure.event_processor.repository import SqlEventRepository
from finanalytics_ai.interfaces.api.dependencies import get_current_user, get_db_session

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/events", tags=["Events Admin"])

REPROCESSABLE_STATUSES = {EventStatus.FAILED, EventStatus.DEAD_LETTER}


def _require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.has_admin_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Acesso restrito a administradores."
        )
    return current_user


@router.post(
    "/{event_id}/reprocess",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reprocessar evento",
    description=(
        "Marca um evento FAILED ou DEAD_LETTER como PENDING e libera a "
        "chave de idempotencia. O worker reprocessara no proximo ciclo."
    ),
)
async def reprocess_event(
    event_id: str,
    _: User = Depends(_require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        eid = uuid.UUID(event_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"event_id invalido: {event_id!r}",
        ) from None

    repo = SqlEventRepository(session)
    event = await repo.find_by_id(eid)

    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Evento {event_id} nao encontrado."
        )

    if event.status not in REPROCESSABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Evento esta com status '{event.status.value}'. "
                f"Apenas {[s.value for s in REPROCESSABLE_STATUSES]} podem ser reprocessados."
            ),
        )

    # Libera idempotencia para permitir reprocessamento
    try:
        from finanalytics_ai.config import get_settings

        settings = get_settings()
        redis_url = str(settings.redis_url)
        from redis.asyncio import from_url

        redis = from_url(redis_url)
        idem_store = RedisIdempotencyStore(redis)
        idem_key = f"evt_idem:{event_id}"
        await idem_store.release(idem_key)
        await redis.aclose()
    except Exception as exc:
        logger.warning("reprocess.idempotency_release_failed", event_id=event_id, error=str(exc))
        # Nao bloqueia o reprocessamento se o Redis falhar

    # Reseta o estado para PENDING
    previous_status = event.status
    event.status = EventStatus.PENDING
    event.error_message = None
    await repo.upsert(event)

    logger.info("event.requeued", event_id=event_id, previous_status=previous_status.value)

    return {
        "event_id": event_id,
        "previous_status": previous_status.value,
        "new_status": EventStatus.PENDING.value,
        "message": "Evento marcado como PENDING. O worker reprocessara no proximo ciclo.",
    }


@router.get("/dead-letter", summary="Listar eventos dead-letter")
async def list_dead_letter(
    limit: int = Query(default=50, ge=1, le=200),
    _: User = Depends(_require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    repo = SqlEventRepository(session)
    events = await repo.find_by_status(EventStatus.DEAD_LETTER, limit=limit)

    return {
        "total": len(events),
        "limit": limit,
        "events": [
            {
                "event_id": str(e.event_id),
                "event_type": str(e.payload.event_type),
                "source": e.payload.source,
                "error_message": e.error_message,
                "retry_count": e.retry_count,
                "created_at": e.created_at.isoformat(),
                "processed_at": e.processed_at.isoformat() if e.processed_at else None,
            }
            for e in events
        ],
    }


@router.get("/failed", summary="Listar eventos com falha (retriaveis)")
async def list_failed(
    limit: int = Query(default=50, ge=1, le=200),
    _: User = Depends(_require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    repo = SqlEventRepository(session)
    events = await repo.find_by_status(EventStatus.FAILED, limit=limit)

    return {
        "total": len(events),
        "limit": limit,
        "events": [
            {
                "event_id": str(e.event_id),
                "event_type": str(e.payload.event_type),
                "source": e.payload.source,
                "error_message": e.error_message,
                "retry_count": e.retry_count,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
    }
