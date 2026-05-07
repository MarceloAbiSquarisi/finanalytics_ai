"""
Endpoints da aba /admin → Backfill.

Todos sob require_master (mesma guard do admin.py).

  POST   /api/v1/admin/backfill/jobs              cria job + dispara worker
  GET    /api/v1/admin/backfill/jobs              lista jobs recentes
  GET    /api/v1/admin/backfill/jobs/{id}         detalhe + counters
  GET    /api/v1/admin/backfill/jobs/{id}/items   items (filtro ?status=)
  POST   /api/v1/admin/backfill/jobs/{id}/cancel  marca cancel_requested
  GET    /api/v1/admin/backfill/failures          falhas (filtro de datas)
  GET    /api/v1/admin/backfill/tickers           lista tickers ativos do agent
"""

from __future__ import annotations

from datetime import date as _date, timedelta
import os
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
import structlog

from finanalytics_ai.application.services import backfill_runner
from finanalytics_ai.domain.auth.entities import User
from finanalytics_ai.infrastructure.database.repositories import backfill_repo
from finanalytics_ai.interfaces.api.routes.admin import require_master

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/admin/backfill", tags=["Admin Backfill"])


# ── schemas ──────────────────────────────────────────────────────────────────


class CreateJobRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=1, max_length=500)
    date_start: _date
    date_end: _date
    force_refetch: bool = Field(default=False)
    # Opcional: lista exata de dias a coletar. Se vazio, usa todos os
    # trading_days no range. Util p/ "Preencher agora" focar so' nos gaps.
    dates: list[_date] | None = Field(default=None)


# ── tickers (DB-direct, funciona com agent off) ─────────────────────────────


_TS_DSN_RAW = (
    os.getenv("TIMESCALE_URL")
    or os.getenv("PROFIT_TIMESCALE_DSN")
    or "postgresql://finanalytics:timescale_secret@timescale:5432/market_data"
)
_TS_DSN = _TS_DSN_RAW.replace("postgresql+asyncpg://", "postgres://").replace(
    "postgresql://", "postgres://"
)


@router.get("/agent/history_progress")
async def agent_history_progress(
    _: User = Depends(require_master),
) -> dict[str, Any]:
    """Proxy p/ profit_agent /history_progress.

    Retorna o estado da coleta /collect_history em curso (callback DLL).
    UI usa pra mostrar barra "% do dia atual" ao lado do progress total.

    Se agent off ou sem coleta ativa: {active: False}.
    """
    import httpx

    agent_url = os.getenv("PROFIT_AGENT_URL", "http://172.17.80.1:8002")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{agent_url.rstrip('/')}/history_progress")
            if r.status_code != 200:
                return {"active": False, "agent_status": r.status_code}
            return r.json()
    except Exception as exc:
        return {"active": False, "agent_error": f"{type(exc).__name__}: {str(exc)[:120]}"}


@router.get("/tickers")
async def list_subscribed_tickers(
    include_inactive: bool = Query(False),
    _: User = Depends(require_master),
) -> dict[str, Any]:
    """Lista tickers da tabela profit_subscribed_tickers (TimescaleDB).

    Default: apenas active=TRUE. Funciona mesmo com profit_agent off (lê DB
    direto). Tabela populada pelo agent quando ticker e' adicionado via
    /tickers/add ou pelo arquivo de subscribed.
    """
    conn = await asyncpg.connect(_TS_DSN)
    try:
        if include_inactive:
            rows = await conn.fetch(
                """
                SELECT ticker, exchange, active
                FROM profit_subscribed_tickers
                ORDER BY active DESC, ticker
                """
            )
        else:
            rows = await conn.fetch(
                """
                SELECT ticker, exchange, active
                FROM profit_subscribed_tickers
                WHERE active = TRUE
                ORDER BY ticker
                """
            )
        out = [
            {
                "ticker": r["ticker"].upper(),
                "exchange": (r["exchange"] or "B").upper(),
                "active": bool(r["active"]),
            }
            for r in rows
        ]
        return {"tickers": out, "count": len(out), "source": "db"}
    except Exception as exc:
        logger.warning("backfill.tickers.db_failed", error=str(exc))
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"erro ao ler profit_subscribed_tickers: {exc}",
        )
    finally:
        await conn.close()


# ── jobs ─────────────────────────────────────────────────────────────────────


@router.post("/jobs")
async def create_job(
    body: CreateJobRequest,
    actor: User = Depends(require_master),
) -> dict[str, Any]:
    if body.date_end < body.date_start:
        raise HTTPException(400, "date_end < date_start")
    if (body.date_end - body.date_start) > timedelta(days=365):
        raise HTTPException(400, "range maximo: 365 dias")
    try:
        result = await backfill_repo.create_job_with_items(
            tickers=body.tickers,
            date_start=body.date_start,
            date_end=body.date_end,
            force_refetch=body.force_refetch,
            requested_by=str(getattr(actor, "user_id", "") or actor.email or ""),
            exchange_for={
                t.upper(): backfill_runner._exchange_for_ticker(t) for t in body.tickers
            },
            specific_days=body.dates,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    job_id = int(result["id"])
    await backfill_runner.enqueue_job(job_id)
    logger.info(
        "backfill.job.create",
        job_id=job_id,
        tickers=len(body.tickers),
        total_items=result["total_items"],
        actor=getattr(actor, "user_id", None),
    )
    return result


@router.get("/jobs")
async def list_jobs(
    limit: int = Query(20, ge=1, le=200),
    _: User = Depends(require_master),
) -> dict[str, Any]:
    jobs = await backfill_repo.list_jobs(limit=limit)
    return {"jobs": jobs, "count": len(jobs)}


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, _: User = Depends(require_master)) -> dict[str, Any]:
    job = await backfill_repo.get_job(job_id)
    if not job:
        raise HTTPException(404, "job nao encontrado")
    return job


@router.get("/jobs/{job_id}/items")
async def get_job_items(
    job_id: int,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(2000, ge=1, le=10000),
    _: User = Depends(require_master),
) -> dict[str, Any]:
    if status_filter and status_filter not in ("pending", "running", "ok", "skip", "err"):
        raise HTTPException(400, "status invalido")
    items = await backfill_repo.list_items(job_id, status=status_filter, limit=limit)
    return {"items": items, "count": len(items)}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: int, actor: User = Depends(require_master)
) -> dict[str, Any]:
    ok = await backfill_repo.cancel_job(job_id)
    if not ok:
        raise HTTPException(409, "job nao esta em queued/running")
    logger.info("backfill.job.cancel", job_id=job_id, actor=getattr(actor, "user_id", None))
    return {"status": "cancel_requested", "job_id": job_id}


# ── failures dashboard ──────────────────────────────────────────────────────


@router.get("/failures")
async def list_failures(
    date_start: _date = Query(...),
    date_end: _date = Query(...),
    ticker: str | None = Query(None),
    limit: int = Query(1000, ge=1, le=5000),
    _: User = Depends(require_master),
) -> dict[str, Any]:
    if date_end < date_start:
        raise HTTPException(400, "date_end < date_start")
    items = await backfill_repo.list_failures(
        date_start=date_start,
        date_end=date_end,
        ticker=ticker,
        limit=limit,
    )
    return {"failures": items, "count": len(items)}
