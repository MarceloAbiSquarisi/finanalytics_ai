from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/v1/tickers", tags=["tickers"])


@router.get("/search")
async def search_tickers(
    request: Request,
    q: str = Query("", min_length=1, max_length=20),
    limit: int = Query(15, ge=1, le=50),
):
    svc = getattr(request.app.state, "ticker_service", None)
    if svc is None:
        return JSONResponse({"results": [], "source": "unavailable"})
    results = await svc.search(q, limit=limit)
    return {"results": results, "count": len(results)}


@router.get("/count")
async def count_tickers(request: Request):
    svc = getattr(request.app.state, "ticker_service", None)
    return {"count": await svc.count() if svc else 0}
