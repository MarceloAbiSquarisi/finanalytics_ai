"""
Hub router — endpoints de gerenciamento do pipeline de eventos.

Endpoints:
    POST /hub/events              — submete novo evento para processamento
    GET  /hub/events              — lista eventos com filtros e paginação
    GET  /hub/stats               — estatísticas de processamento por status
    POST /hub/events/{id}/reprocess — recoloca evento FAILED/DEAD_LETTER em PENDING

Design:
- Sem autenticação neste router (adicionar via dependencies no app.py se necessário)
- get_db é placeholder substituído via dependency_overrides no app.py
- Resposta com Pydantic models para validação e documentação automática
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
import structlog

from finanalytics_ai.domain.events.models import DomainEvent, EventPayload, EventStatus
from finanalytics_ai.domain.events.value_objects import CorrelationId, EventType
from finanalytics_ai.infrastructure.event_processor.repository import SqlEventRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/hub", tags=["hub"])


# ──────────────────────────────────────────────────────────────────────────────
# Request / Response schemas
# ──────────────────────────────────────────────────────────────────────────────


class CreateEventRequest(BaseModel):
    event_type: str = Field(..., min_length=1, max_length=128)
    source: str = Field(..., min_length=1, max_length=256)
    data: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None


class EventResponse(BaseModel):
    event_id: str
    event_type: str
    source: str
    correlation_id: str | None
    status: str
    error_message: str | None
    retry_count: int
    created_at: str
    processed_at: str | None


class EventListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    events: list[EventResponse]


class StatsResponse(BaseModel):
    counts: dict[str, int]
    total: int


class ReprocessResponse(BaseModel):
    event_id: str
    previous_status: str
    new_status: str
    message: str


# ──────────────────────────────────────────────────────────────────────────────
# Dependency — session placeholder (overridden in app.py)
# ──────────────────────────────────────────────────────────────────────────────


async def get_db() -> AsyncSession:  # type: ignore[return]
    """Placeholder — substituído via app.dependency_overrides no app.py."""
    raise NotImplementedError("Configure get_db no app.py principal")


def _make_repo(session: AsyncSession) -> SqlEventRepository:
    return SqlEventRepository(lambda: session)  # type: ignore[arg-type]


def _serialize_event(e: DomainEvent) -> EventResponse:
    return EventResponse(
        event_id=str(e.event_id),
        event_type=str(e.payload.event_type),
        source=e.payload.source,
        correlation_id=(
            str(e.payload.correlation_id) if e.payload.correlation_id else None
        ),
        status=e.status.value,
        error_message=e.error_message,
        retry_count=e.retry_count,
        created_at=e.created_at.isoformat(),
        processed_at=e.processed_at.isoformat() if e.processed_at else None,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.post("/events", status_code=201, response_model=EventResponse)
async def create_event(
    body: CreateEventRequest,
    session: AsyncSession = Depends(get_db),
) -> EventResponse:
    """Submete um novo evento para processamento assíncrono.

    O evento é persistido com status PENDING. O event_worker processa
    no próximo ciclo de poll.
    """
    try:
        payload = EventPayload(
            event_type=EventType(body.event_type),
            data=body.data,
            source=body.source,
            correlation_id=(
                CorrelationId(body.correlation_id) if body.correlation_id else None
            ),
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    event = DomainEvent.create(payload)
    repo = _make_repo(session)
    await repo.upsert(event)

    logger.info(
        "hub.event_created",
        event_id=str(event.event_id),
        event_type=body.event_type,
    )
    return _serialize_event(event)


@router.get("/events", response_model=EventListResponse)
async def list_events(
    status: str | None = Query(default=None, description="Filtrar por status"),
    event_type: str | None = Query(default=None, description="Filtrar por tipo"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> EventListResponse:
    """Lista eventos com filtros opcionais e paginação."""
    parsed_status: EventStatus | None = None
    if status is not None:
        try:
            parsed_status = EventStatus(status)
        except ValueError:
            valid = [s.value for s in EventStatus]
            raise HTTPException(
                status_code=422,
                detail=f"Status inválido: {status!r}. Válidos: {valid}",
            ) from None

    repo = _make_repo(session)
    events = await repo.find_filtered(
        status=parsed_status,
        event_type=event_type,
        limit=limit,
        offset=offset,
    )
    return EventListResponse(
        total=len(events),
        limit=limit,
        offset=offset,
        events=[_serialize_event(e) for e in events],
    )


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    session: AsyncSession = Depends(get_db),
) -> StatsResponse:
    """Estatísticas de processamento agrupadas por status."""
    repo = _make_repo(session)
    counts = await repo.count_by_status()
    total = sum(counts.values())
    return StatsResponse(counts=counts, total=total)


REPROCESSABLE = {EventStatus.FAILED, EventStatus.DEAD_LETTER}


@router.post(
    "/events/{event_id}/reprocess",
    status_code=202,
    response_model=ReprocessResponse,
)
async def reprocess_event(
    event_id: str,
    session: AsyncSession = Depends(get_db),
) -> ReprocessResponse:
    """Recoloca evento FAILED ou DEAD_LETTER em PENDING para reprocessamento.

    O event_worker processará no próximo ciclo. A chave de idempotência
    é liberada automaticamente para permitir nova tentativa.
    """
    try:
        eid = uuid.UUID(event_id)
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"event_id inválido: {event_id!r}"
        ) from None

    repo = _make_repo(session)
    event = await repo.find_by_id(eid)

    if event is None:
        raise HTTPException(status_code=404, detail=f"Evento {event_id} não encontrado.")

    if event.status not in REPROCESSABLE:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Status atual: '{event.status.value}'. "
                f"Apenas {[s.value for s in REPROCESSABLE]} podem ser reprocessados."
            ),
        )

    previous = event.status
    event.status = EventStatus.PENDING
    event.error_message = None
    await repo.upsert(event)

    # Libera idempotência (best-effort)
    try:
        from finanalytics_ai.config import get_settings
        from finanalytics_ai.container_v2 import build_idempotency_store

        settings = get_settings()
        idem = build_idempotency_store(settings)
        await idem.release(f"evt_idem:{event_id}")
    except Exception as exc:
        logger.warning("hub.idempotency_release_failed", event_id=event_id, error=str(exc))

    logger.info("hub.event_requeued", event_id=event_id, previous_status=previous.value)

    return ReprocessResponse(
        event_id=event_id,
        previous_status=previous.value,
        new_status=EventStatus.PENDING.value,
        message="Evento marcado como PENDING. Será processado no próximo ciclo do worker.",
    )
