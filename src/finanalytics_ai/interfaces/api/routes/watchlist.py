"""
finanalytics_ai.interfaces.api.routes.watchlist
─────────────────────────────────────────────────
ORDEM DAS ROTAS — crítico para FastAPI:
  Rotas fixas ANTES de rotas com parâmetros no mesmo método HTTP.
  /evaluate e /stream devem vir ANTES de /{item_id}.
  /alerts/{alert_id} deve vir ANTES de /{item_id}.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.requests import Request

from finanalytics_ai.application.services.watchlist_service import WatchlistError, WatchlistService
from finanalytics_ai.interfaces.api.dependencies import get_watchlist_service

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/watchlist", tags=["Watchlist"])


class AddItemRequest(BaseModel):
    user_id: str       = Field(..., min_length=1)
    ticker:  str       = Field(..., min_length=1, max_length=10)
    note:    str       = Field("", max_length=500)
    tags:    list[str] = Field(default_factory=list)

class UpdateItemRequest(BaseModel):
    note: str | None       = None
    tags: list[str] | None = None

class AddAlertRequest(BaseModel):
    alert_type: str        = Field(..., description="rsi_oversold|rsi_overbought|ma_cross_up|ma_cross_down|volume_spike|new_high_52w|new_low_52w|price_above|price_below")
    note:   str            = ""
    config: dict[str, Any] = Field(default_factory=dict)


# ── ROTAS FIXAS primeiro (antes de /{param}) ──────────────────────────────────

@router.get("/evaluate")
async def evaluate_alerts(
    user_id: str = Query(...),
    svc: WatchlistService = Depends(get_watchlist_service),
) -> dict[str, Any]:
    results = await svc.evaluate_all(user_id)
    return {
        "triggered": len(results),
        "alerts": [
            {"alert_id": r.alert_id, "ticker": r.ticker, "alert_type": r.alert_type.value,
             "message": r.message, "severity": r.severity,
             "indicator_value": r.indicator_value, "context": r.context}
            for r in results
        ],
    }


@router.get("/stream")
async def stream_alerts(
    request: Request,
    user_id: str = Query(...),
    svc: WatchlistService = Depends(get_watchlist_service),
) -> StreamingResponse:
    async def _gen():
        yield 'data: {"type": "connected"}\n\n'
        heartbeat = 0
        while not await request.is_disconnected():
            heartbeat += 1
            if heartbeat % 3 == 0:
                try:
                    for r in await svc.evaluate_all(user_id):
                        yield f"data: {json.dumps({'type':'alert_triggered','alert_id':r.alert_id,'ticker':r.ticker,'alert_type':r.alert_type.value,'message':r.message,'severity':r.severity,'indicator_value':r.indicator_value})}\n\n"
                except Exception as exc:
                    logger.warning("watchlist.stream.error", error=str(exc))
            yield ": heartbeat\n\n"
            await asyncio.sleep(20)
    return StreamingResponse(_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.delete("/alerts/{alert_id}", status_code=204)
async def remove_alert(
    alert_id: str,
    user_id: str = Query(...),
    svc: WatchlistService = Depends(get_watchlist_service),
) -> None:
    await svc.remove_smart_alert(user_id, alert_id)


# ── ROTAS COM PARÂMETROS depois ───────────────────────────────────────────────

@router.get("")
async def list_watchlist(
    user_id: str = Query(..., min_length=1),
    svc: WatchlistService = Depends(get_watchlist_service),
) -> list[dict[str, Any]]:
    items = await svc.get_watchlist(user_id)
    return [i.to_dict() for i in items]


@router.post("", status_code=201)
async def add_item(
    body: AddItemRequest,
    svc: WatchlistService = Depends(get_watchlist_service),
) -> dict[str, Any]:
    try:
        item = await svc.add_item(body.user_id, body.ticker, body.note, body.tags)
        return item.to_dict()
    except WatchlistError as e:
        raise HTTPException(422, detail=str(e))


@router.delete("/{item_id}", status_code=204)
async def remove_item(
    item_id: str,
    user_id: str = Query(...),
    svc: WatchlistService = Depends(get_watchlist_service),
) -> None:
    try:
        await svc.remove_item(user_id, item_id)
    except WatchlistError as e:
        raise HTTPException(404, detail=str(e))


@router.patch("/{item_id}")
async def update_item(
    item_id: str,
    user_id: str = Query(...),
    body: UpdateItemRequest = ...,
    svc: WatchlistService = Depends(get_watchlist_service),
) -> dict[str, Any]:
    try:
        item = await svc.update_item(user_id, item_id, body.note, body.tags)
        return item.to_dict()
    except WatchlistError as e:
        raise HTTPException(404, detail=str(e))


@router.post("/{item_id}/alerts", status_code=201)
async def add_alert(
    item_id: str,
    user_id: str = Query(...),
    body: AddAlertRequest = ...,
    svc: WatchlistService = Depends(get_watchlist_service),
) -> dict[str, Any]:
    try:
        alert = await svc.add_smart_alert(user_id, item_id, body.alert_type, body.config, body.note)
        return alert.to_dict()
    except WatchlistError as e:
        raise HTTPException(422, detail=str(e))
