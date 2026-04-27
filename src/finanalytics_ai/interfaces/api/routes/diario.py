"""
diario.py — Rota e serviço do Diário de Trade.

GET  /api/v1/diario/entries          lista entradas do usuário
POST /api/v1/diario/entries          cria entrada
GET  /api/v1/diario/entries/{id}     retorna uma entrada
PUT  /api/v1/diario/entries/{id}     atualiza entrada
DELETE /api/v1/diario/entries/{id}   deleta entrada
GET  /api/v1/diario/stats            estatísticas agregadas

GET  /diario                         página HTML
"""

from __future__ import annotations

from datetime import datetime
import pathlib
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
import structlog

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/diario", tags=["Diário de Trade"])


# ── Schemas Pydantic ──────────────────────────────────────────────────────────


class EntryCreate(BaseModel):
    ticker: str
    direction: str = Field("BUY", pattern="^(BUY|SELL)$")
    entry_date: datetime
    exit_date: datetime | None = None
    entry_price: float = Field(..., gt=0)
    exit_price: float | None = Field(None, gt=0)
    quantity: float = Field(..., gt=0)
    setup: str | None = None
    timeframe: str | None = None
    trade_objective: str | None = Field(None, pattern="^(daytrade|swing|buy_hold)$")
    reason_entry: str | None = None
    expectation: str | None = None
    what_happened: str | None = None
    emotional_state: str | None = None
    mistakes: str | None = None
    lessons: str | None = None
    rating: int | None = Field(None, ge=1, le=5)
    tags: list[str] = Field(default_factory=list)
    user_id: str = "user-demo"


class EntryUpdate(BaseModel):
    ticker: str | None = None
    direction: str | None = None
    entry_date: datetime | None = None
    exit_date: datetime | None = None
    entry_price: float | None = Field(None, gt=0)
    exit_price: float | None = Field(None, gt=0)
    quantity: float | None = Field(None, gt=0)
    setup: str | None = None
    timeframe: str | None = None
    trade_objective: str | None = Field(None, pattern="^(daytrade|swing|buy_hold)$")
    reason_entry: str | None = None
    expectation: str | None = None
    what_happened: str | None = None
    emotional_state: str | None = None
    mistakes: str | None = None
    lessons: str | None = None
    rating: int | None = Field(None, ge=1, le=5)
    tags: list[str] | None = None


# ── Dependency ────────────────────────────────────────────────────────────────


def _repo(request: Request) -> Any:
    repo = getattr(request.app.state, "diario_repo", None)
    if repo is None:
        raise HTTPException(503, "DiarioRepository não inicializado")
    return repo


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/entries")
async def list_entries(
    request: Request,
    user_id: str = Query("user-demo"),
    ticker: str | None = Query(None),
    setup: str | None = Query(None),
    direction: str | None = Query(None),
    trade_objective: str | None = Query(None),
    is_complete: bool | None = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
) -> dict[str, Any]:
    repo = _repo(request)
    entries = await repo.list(
        user_id=user_id,
        ticker=ticker,
        setup=setup,
        direction=direction,
        trade_objective=trade_objective,
        is_complete=is_complete,
        limit=limit,
        offset=offset,
    )
    return {"entries": entries, "count": len(entries)}


@router.post("/entries", status_code=201)
async def create_entry(body: EntryCreate, request: Request) -> dict[str, Any]:
    repo = _repo(request)
    entry = await repo.create(body.model_dump())
    logger.info("diario.entry.created", ticker=entry["ticker"], id=entry["id"])
    return entry


@router.get("/entries/{entry_id}")
async def get_entry(
    entry_id: str, request: Request, user_id: str = Query("user-demo")
) -> dict[str, Any]:
    repo = _repo(request)
    entry = await repo.get(entry_id, user_id=user_id)
    if not entry:
        raise HTTPException(404, "Entrada não encontrada")
    return entry


@router.put("/entries/{entry_id}")
async def update_entry(
    entry_id: str,
    body: EntryUpdate,
    request: Request,
    user_id: str = Query("user-demo"),
) -> dict[str, Any]:
    repo = _repo(request)
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    entry = await repo.update(entry_id, data, user_id=user_id)
    if not entry:
        raise HTTPException(404, "Entrada não encontrada")
    return entry


@router.delete("/entries/{entry_id}", status_code=204)
async def delete_entry(entry_id: str, request: Request, user_id: str = Query("user-demo")) -> None:
    repo = _repo(request)
    deleted = await repo.delete(entry_id, user_id=user_id)
    if not deleted:
        raise HTTPException(404, "Entrada não encontrada")


@router.post("/entries/{entry_id}/complete")
async def mark_complete(
    entry_id: str, request: Request, user_id: str = Query("user-demo")
) -> dict[str, Any]:
    repo = _repo(request)
    entry = await repo.set_complete(entry_id, True, user_id=user_id)
    if not entry:
        raise HTTPException(404, "Entrada não encontrada")
    return entry


@router.post("/entries/{entry_id}/uncomplete")
async def mark_incomplete(
    entry_id: str, request: Request, user_id: str = Query("user-demo")
) -> dict[str, Any]:
    repo = _repo(request)
    entry = await repo.set_complete(entry_id, False, user_id=user_id)
    if not entry:
        raise HTTPException(404, "Entrada não encontrada")
    return entry


@router.get("/incomplete_count")
async def incomplete_count(
    request: Request, user_id: str = Query("user-demo")
) -> dict[str, Any]:
    repo = _repo(request)
    n = await repo.count_incomplete(user_id=user_id)
    return {"count": n, "user_id": user_id}


class FromFillBody(BaseModel):
    external_order_id: str = Field(..., min_length=1, max_length=64)
    ticker: str
    direction: str = Field("BUY", pattern="^(BUY|SELL)$")
    entry_date: datetime
    entry_price: float = Field(..., gt=0)
    quantity: float = Field(..., gt=0)
    timeframe: str | None = None
    trade_objective: str | None = Field(None, pattern="^(daytrade|swing|buy_hold)$")
    user_id: str = "user-demo"


@router.post("/from_fill", status_code=201)
async def create_from_fill(body: FromFillBody, request: Request) -> dict[str, Any]:
    """Hook idempotente para criar entry no diário a partir de um fill da DLL.

    Chamado pelo profit_agent quando uma ordem entra em status FILLED.
    Idempotente por external_order_id (callback DLL pode disparar múltiplas vezes).
    """
    repo = _repo(request)
    entry, created = await repo.create_from_fill(body.model_dump())
    logger.info(
        "diario.from_fill",
        external_order_id=body.external_order_id,
        ticker=body.ticker,
        created=created,
    )
    return {"entry": entry, "created": created}


@router.get("/stats")
async def get_stats(
    request: Request,
    user_id: str = Query("user-demo"),
    trade_objective: str | None = Query(None),
) -> dict[str, Any]:
    repo = _repo(request)
    return await repo.stats(user_id=user_id, trade_objective=trade_objective)


# ── Página HTML ───────────────────────────────────────────────────────────────


@router.get("/page", response_class=HTMLResponse, include_in_schema=False)
async def diario_page() -> HTMLResponse:
    path = pathlib.Path(__file__).parent.parent / "static" / "diario.html"
    if path.exists():
        return HTMLResponse(path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>diario.html não encontrado</h1>", status_code=404)
