"""
Endpoints R5 backtest harness — listagem + detalhe + per-ticker.

Todos sob require_master (mesma guard das outras admin routes).

  GET /api/v1/r5/runs                 lista runs (mais recente primeiro)
  GET /api/v1/r5/runs/{id}            detalhe (header + aggregate + Neff + DSR)
  GET /api/v1/r5/runs/{id}/tickers    per-ticker results (sortable via ?sort=)
  GET /api/v1/r5/tickers/{ticker}     histórico cross-run de 1 ticker (compare)
  GET /api/v1/r5/runs/{a}/diff/{b}    diff per-ticker entre 2 runs (sharpe/dd/ret)
"""

from __future__ import annotations

import os
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
import structlog

from finanalytics_ai.domain.auth.entities import User
from finanalytics_ai.interfaces.api.routes.admin import require_master

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/r5", tags=["R5 Backtest"])


_DB_DSN_RAW = (
    os.getenv("DATABASE_URL")
    or "postgresql://finanalytics:secret@postgres:5432/finanalytics"
)
_DB_DSN = _DB_DSN_RAW.replace("postgresql+asyncpg://", "postgres://").replace(
    "postgresql://", "postgres://"
)


SORT_FIELDS = {
    "sharpe": "sharpe_ratio",
    "return": "total_return_pct",
    "drawdown": "max_drawdown_pct",
    "trades": "trades",
    "ticker": "ticker",
}


@router.get("/runs")
async def list_runs(
    limit: int = Query(50, ge=1, le=200),
    _: User = Depends(require_master),
) -> dict[str, Any]:
    """Lista runs ordenadas por generated_at DESC.

    Resposta resumida (sem per_ticker) — UI faz drill-down via /runs/{id}.
    """
    conn = await asyncpg.connect(_DB_DSN)
    try:
        rows = await conn.fetch(
            """
            SELECT id, generated_at, version, elapsed_total_s,
                   horizon, retrain_days, train_end, target_vol, min_close,
                   n_valid, n_total, n_trades_total,
                   sharpe_avg, sharpe_max, sharpe_median,
                   drawdown_avg, drawdown_max,
                   return_avg, return_total_sum,
                   best_ticker, worst_ticker,
                   n_eff_eig, dsr_full_prob_real
            FROM r5_runs
            ORDER BY generated_at DESC
            LIMIT $1
            """,
            limit,
        )
        return {"runs": [dict(r) for r in rows]}
    finally:
        await conn.close()


@router.get("/runs/{run_id}")
async def get_run(
    run_id: int,
    _: User = Depends(require_master),
) -> dict[str, Any]:
    conn = await asyncpg.connect(_DB_DSN)
    try:
        row = await conn.fetchrow("SELECT * FROM r5_runs WHERE id = $1", run_id)
        if not row:
            raise HTTPException(404, "run not found")
        return {"run": dict(row)}
    finally:
        await conn.close()


@router.get("/runs/{run_id}/tickers")
async def list_run_tickers(
    run_id: int,
    sort: Literal["sharpe", "return", "drawdown", "trades", "ticker"] = "sharpe",
    desc: bool = True,
    limit: int = Query(200, ge=1, le=1000),
    _: User = Depends(require_master),
) -> dict[str, Any]:
    """Per-ticker do run, ordenado por `sort` (DESC default)."""
    col = SORT_FIELDS[sort]
    direction = "DESC NULLS LAST" if desc else "ASC NULLS LAST"
    conn = await asyncpg.connect(_DB_DSN)
    try:
        rows = await conn.fetch(
            f"""
            SELECT ticker, ok, error, trades, winners, test_len, retrains, elapsed_s,
                   sharpe_ratio, total_return_pct, max_drawdown_pct, win_rate_pct,
                   profit_factor, calmar_ratio, avg_win_pct, avg_loss_pct,
                   avg_duration_days, final_equity,
                   position_size, train_median_close, train_mean_vol_21d
            FROM r5_ticker_results
            WHERE run_id = $1
            ORDER BY {col} {direction}
            LIMIT $2
            """,
            run_id,
            limit,
        )
        return {"run_id": run_id, "tickers": [dict(r) for r in rows]}
    finally:
        await conn.close()


@router.get("/tickers/{ticker}")
async def list_ticker_history(
    ticker: str,
    _: User = Depends(require_master),
) -> dict[str, Any]:
    """Histórico cross-run de 1 ticker.

    Útil pra ver evolução conforme params do harness mudam (vol-target,
    retrain_days, train_end). Ordenado por generated_at DESC.
    """
    conn = await asyncpg.connect(_DB_DSN)
    try:
        rows = await conn.fetch(
            """
            SELECT t.run_id, r.generated_at, r.target_vol, r.retrain_days, r.train_end,
                   t.position_size, t.sharpe_ratio, t.total_return_pct,
                   t.max_drawdown_pct, t.win_rate_pct, t.trades, t.final_equity
            FROM r5_ticker_results t
            JOIN r5_runs r ON r.id = t.run_id
            WHERE t.ticker = $1
            ORDER BY r.generated_at DESC
            """,
            ticker.upper(),
        )
        return {"ticker": ticker.upper(), "history": [dict(r) for r in rows]}
    finally:
        await conn.close()


@router.get("/runs/{run_a}/diff/{run_b}")
async def diff_runs(
    run_a: int,
    run_b: int,
    _: User = Depends(require_master),
) -> dict[str, Any]:
    """Diff per-ticker entre dois runs (run_a = baseline, run_b = comparison).

    Retorna delta sharpe/return/drawdown por ticker, ordenado pelo |delta_sharpe|.
    """
    conn = await asyncpg.connect(_DB_DSN)
    try:
        meta = await conn.fetch(
            """SELECT id, generated_at, target_vol, min_close,
                      sharpe_avg, drawdown_avg, drawdown_max, best_ticker
               FROM r5_runs WHERE id = ANY($1::bigint[])""",
            [run_a, run_b],
        )
        meta_map = {r["id"]: dict(r) for r in meta}
        if run_a not in meta_map or run_b not in meta_map:
            raise HTTPException(404, "one or both runs not found")

        rows = await conn.fetch(
            """
            SELECT a.ticker,
                   a.sharpe_ratio       AS a_sharpe,
                   b.sharpe_ratio       AS b_sharpe,
                   a.total_return_pct   AS a_ret,
                   b.total_return_pct   AS b_ret,
                   a.max_drawdown_pct   AS a_dd,
                   b.max_drawdown_pct   AS b_dd,
                   a.position_size      AS a_pos,
                   b.position_size      AS b_pos,
                   (b.sharpe_ratio - a.sharpe_ratio)         AS d_sharpe,
                   (b.total_return_pct - a.total_return_pct) AS d_ret,
                   (b.max_drawdown_pct - a.max_drawdown_pct) AS d_dd
            FROM r5_ticker_results a
            JOIN r5_ticker_results b ON a.ticker = b.ticker
            WHERE a.run_id = $1 AND b.run_id = $2
            ORDER BY abs(b.sharpe_ratio - a.sharpe_ratio) DESC NULLS LAST
            """,
            run_a, run_b,
        )
        return {
            "run_a": meta_map[run_a],
            "run_b": meta_map[run_b],
            "diffs": [dict(r) for r in rows],
        }
    finally:
        await conn.close()
