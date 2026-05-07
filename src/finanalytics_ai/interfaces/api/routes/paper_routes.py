"""
Endpoints forward-test paper R5.

Todos sob require_master.

  GET /api/v1/paper/runs                  list paper_runs
  GET /api/v1/paper/runs/{name}           detalhe + state_json (positions, equity)
  GET /api/v1/paper/runs/{name}/signals   ?date=YYYY-MM-DD (default: latest)
"""
from __future__ import annotations

import os
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
import structlog

from finanalytics_ai.domain.auth.entities import User
from finanalytics_ai.interfaces.api.routes.admin import require_master

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/paper", tags=["Paper Trading"])


_DB_DSN_RAW = (
    os.getenv("DATABASE_URL")
    or "postgresql://finanalytics:secret@postgres:5432/finanalytics"
)
_DB_DSN = _DB_DSN_RAW.replace("postgresql+asyncpg://", "postgres://").replace(
    "postgresql://", "postgres://"
)


@router.get("/runs")
async def list_paper_runs(_: User = Depends(require_master)) -> dict[str, Any]:
    conn = await asyncpg.connect(_DB_DSN)
    try:
        rows = await conn.fetch(
            """SELECT id, name, started_at, last_step_date, is_active,
                      initial_capital, n_slots,
                      jsonb_array_length(state_json->'equity_curve') AS n_snapshots,
                      jsonb_array_length(state_json->'trades_history') AS n_trades,
                      (SELECT count(*) FROM jsonb_object_keys(state_json->'positions')) AS n_open
               FROM paper_runs ORDER BY id DESC"""
        )
        return {"runs": [dict(r) for r in rows]}
    finally:
        await conn.close()


@router.get("/runs/{name}")
async def get_paper_run(name: str, _: User = Depends(require_master)) -> dict[str, Any]:
    conn = await asyncpg.connect(_DB_DSN)
    try:
        row = await conn.fetchrow("SELECT * FROM paper_runs WHERE name = $1", name)
        if not row:
            raise HTTPException(404, f"paper_run '{name}' not found")
        return {"run": dict(row)}
    finally:
        await conn.close()


@router.get("/runs/{name}/signals")
async def get_signals(
    name: str,
    date: str | None = Query(None, description="YYYY-MM-DD; default = latest"),
    _: User = Depends(require_master),
) -> dict[str, Any]:
    conn = await asyncpg.connect(_DB_DSN)
    try:
        run = await conn.fetchrow("SELECT id FROM paper_runs WHERE name = $1", name)
        if not run:
            raise HTTPException(404, f"paper_run '{name}' not found")

        if date:
            from datetime import date as _d
            dt = _d.fromisoformat(date)
            rows = await conn.fetch(
                """SELECT * FROM paper_signals
                   WHERE paper_run_id=$1 AND signal_date=$2
                   ORDER BY ticker""",
                run["id"], dt,
            )
            target_date = dt
        else:
            target_date = await conn.fetchval(
                "SELECT max(signal_date) FROM paper_signals WHERE paper_run_id=$1",
                run["id"],
            )
            if not target_date:
                return {"date": None, "signals": []}
            rows = await conn.fetch(
                """SELECT * FROM paper_signals
                   WHERE paper_run_id=$1 AND signal_date=$2 ORDER BY ticker""",
                run["id"], target_date,
            )
        return {"date": str(target_date), "signals": [dict(r) for r in rows]}
    finally:
        await conn.close()
