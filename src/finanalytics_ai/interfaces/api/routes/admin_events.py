"""
Admin router — Dead-letter queue management.

Endpoints:
    GET  /admin/events/dead-letter          — lista eventos em dead-letter
    GET  /admin/events/dead-letter/{id}     — detalhe de um evento
    POST /admin/events/dead-letter/{id}/requeue — recoloca evento em pending

Decisões:
- Sem autenticação neste arquivo: a autenticação deve ser aplicada no
  middleware do app principal (Bearer token, mTLS, IP allowlist, etc.).
  Adicionar `dependencies=[Depends(require_admin)]` na inclusão do router.
- Paginação simples com limit/offset: suficiente para inspeção manual.
  Se o volume de dead-letters for alto, há um problema maior a resolver.
- Resposta com TypedDict explícito evita `dict[str, Any]` solto nos retornos.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from finanalytics_ai.domain.events.entities import EventId
from finanalytics_ai.infrastructure.database.repositories.event_repository import (
    PostgresEventRepository
)

router = APIRouter(prefix="/admin/events", tags=["admin", "events"])

# ──────────────────────────────────────────────────────────────────────────────
# Response schemas (Pydantic v2)
# ──────────────────────────────────────────────────────────────────────────────

class DeadLetterEventResponse(BaseModel):
    event_id: str
    event_type: str
    source: str
    correlation_id: str | None
    created_at: str
    attempt: int
    last_error: str | None
    payload_preview: dict[str, Any]  # primeiros N campos do payload

class DeadLetterListResponse(BaseModel):
    total_returned: int
    limit: int
    offset: int
    items: list[DeadLetterEventResponse]

class RequeueResponse(BaseModel):
    event_id: str
    requeued: bool
    message: str

# ──────────────────────────────────────────────────────────────────────────────
# Dependency — session
# Nota: adapte get_db para o padrão de DI do seu app.py principal.
# ──────────────────────────────────────────────────────────────────────────────

async def get_db() -> AsyncSession:  # type: ignore[return]
    """Placeholder — substitua pelo session factory do app principal.

    Exemplo de uso no app.py:
        from finanalytics_ai.container import build_session_factory, build_engine
        session_factory = build_session_factory(build_engine(settings))

        async def get_db():
            async with session_factory() as session:
                yield session
    """
    raise NotImplementedError("Configure get_db no app.py principal")

# ──────────────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/dead-letter", response_model=DeadLetterListResponse)
async def list_dead_letter_events(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db)
) -> DeadLetterListResponse:
    """Lista todos os eventos em dead-letter com paginação.

    Use para triagem manual de eventos que falharam permanentemente.
    """
    repo = PostgresEventRepository(session)
    pairs = await repo.get_dead_letter_events(limit=limit, offset=offset)

    items = [
        DeadLetterEventResponse(
            event_id=str(event.id),
            event_type=event.event_type.value,
            source=event.source,
            correlation_id=event.correlation_id,
            created_at=event.created_at.isoformat(),
            attempt=record.attempt,
            last_error=record.last_error,
            payload_preview=dict(list(event.payload.items())[:5]),  # max 5 campos
        )
        for event, record in pairs
    ]

    return DeadLetterListResponse(
        total_returned=len(items),
        limit=limit,
        offset=offset,
        items=items
    )

@router.post("/dead-letter/{event_id}/requeue", response_model=RequeueResponse)
async def requeue_dead_letter_event(
    event_id: str,
    session: AsyncSession = Depends(get_db)
) -> RequeueResponse:
    """Recoloca um evento dead-letter na fila como 'pending'.

    O event_worker vai processá-lo no próximo poll.
    Reseta attempt=0 para garantir todas as retentativas.

    Quando usar:
    - Após corrigir um bug que causou o dead-letter.
    - Após intervenção manual nos dados que o evento dependia.

    Quando NÃO usar:
    - Se o payload estiver corrompido (o evento vai falhar novamente).
      Neste caso, crie um novo evento com o payload correto.
    """
    try:
        eid = EventId.from_str(event_id)
    except Exception:
        raise HTTPException(status_code=422, detail=f"event_id inválido: {event_id!r}")

    async with session.begin():
        repo = PostgresEventRepository(session)
        requeued = await repo.requeue_dead_letter(eid)

    if not requeued:
        raise HTTPException(
            status_code=404,
            detail=f"Evento {event_id!r} não encontrado em dead-letter."
        )

    return RequeueResponse(
        event_id=event_id,
        requeued=True,
        message="Evento recolocado em 'pending'. Será processado no próximo poll do worker."
    )
