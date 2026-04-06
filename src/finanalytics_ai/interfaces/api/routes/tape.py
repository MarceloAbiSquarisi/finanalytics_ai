"""
finanalytics_ai.interfaces.api.routes.tape
------------------------------------------
Tape Reading â€” endpoints REST e SSE.

GET  /api/v1/tape/tickers             -- tickers ativos no tape
GET  /api/v1/tape/metrics/{ticker}    -- metricas de um ticker
GET  /api/v1/tape/metrics             -- metricas de todos os tickers
GET  /api/v1/tape/trades/{ticker}     -- ultimos negocios
GET  /api/v1/tape/stream/{ticker}     -- SSE stream em tempo real
POST /api/v1/tape/simulate            -- inicia simulacao (para testes)
"""
import asyncio
import json
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/tape", tags=["Tape Reading"])


def _get_tape(request: Request) -> Any:
    svc = getattr(request.app.state, "tape_service", None)
    if svc is None:
        raise HTTPException(503, "TapeService nao inicializado")
    return svc


@router.get("/tickers", summary="Tickers ativos no tape")
async def get_tickers(request: Request) -> dict[str, Any]:
    tape = _get_tape(request)
    return {"tickers": tape.get_active_tickers()}


@router.get("/metrics", summary="Metricas de todos os tickers")
async def get_all_metrics(request: Request) -> dict[str, Any]:
    tape = _get_tape(request)
    metrics = tape.get_all_metrics()
    return {"total": len(metrics), "metrics": metrics}


@router.get("/metrics/{ticker}", summary="Metricas de um ticker")
async def get_metrics(ticker: str, request: Request) -> dict[str, Any]:
    tape = _get_tape(request)
    m = tape.get_metrics(ticker.upper())
    if not m:
        raise HTTPException(404, f"Nenhum dado de tape para {ticker.upper()}")
    return m.to_dict()


@router.get("/trades/{ticker}", summary="Ultimos negocios do tape")
async def get_trades(
    ticker: str,
    request: Request,
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    tape = _get_tape(request)
    trades = tape.get_recent_trades(ticker.upper(), limit)
    return {"ticker": ticker.upper(), "total": len(trades), "trades": trades}


@router.get("/stream/{ticker}", summary="SSE stream de negocios em tempo real")
async def stream_tape(ticker: str, request: Request) -> StreamingResponse:
    """
    Server-Sent Events com cada negocio e metricas atualizadas.
    Conecte com: EventSource('/api/v1/tape/stream/PETR4')
    """
    tape = _get_tape(request)
    ticker = ticker.upper()
    q = tape.subscribe(ticker)

    async def event_generator():
        try:
            # Envia metricas iniciais
            m = tape.get_metrics(ticker)
            if m:
                yield f"data: {json.dumps({'type': 'metrics', 'data': m.to_dict()})}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=5.0)
                    yield f"data: {json.dumps({'type': 'tick', **event})}\n\n"
                except asyncio.TimeoutError:
                    yield "data: {\"type\":\"heartbeat\"}\n\n"
        finally:
            tape.unsubscribe(ticker, q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/simulate", summary="Inicia simulacao de tape (para testes)")
async def start_simulation(
    request: Request,
    tickers: str = Query("PETR4,VALE3,WINFUT", description="Tickers separados por virgula"),
    duration: int = Query(300, ge=10, le=3600, description="Duracao em segundos"),
    tps: float = Query(5.0, ge=0.5, le=50.0, description="Negocios por segundo"),
) -> dict[str, Any]:
    """
    Inicia simulacao de fluxo de negocios para testes sem mercado aberto.
    Util para validar a interface e metricas antes de segunda-feira.
    """
    tape = _get_tape(request)
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]

    asyncio.create_task(
        tape.simulate(ticker_list, duration_seconds=duration, tps=tps)
    )

    return {
        "started": True,
        "tickers": ticker_list,
        "duration_seconds": duration,
        "tps": tps,
        "message": f"Simulacao iniciada para {len(ticker_list)} tickers por {duration}s",
        "stream_url": f"/api/v1/tape/stream/{ticker_list[0]}",
    }