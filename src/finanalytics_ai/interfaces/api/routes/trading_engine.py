"""Rotas read-only para a UI do trading-engine.

Lê do schema `trading_engine_orders` via role `trading_engine_reader`.
Sem mutações — toda escrita fica no trading-engine. Auth obrigatório
(get_current_user).

GET /api/v1/trading-engine/orders          — orders 24h (paginadas)
GET /api/v1/trading-engine/trade-journal   — trades fechados + agregados
GET /api/v1/trading-engine/engine-events   — audit feed (filtrável por tipo)
GET /api/v1/trading-engine/backtests       — runs persistidas
GET /api/v1/trading-engine/backtests/{id}  — detalhe + equity curve
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from finanalytics_ai.domain.auth.entities import User
from finanalytics_ai.interfaces.api.dependencies import (
    get_current_user,
    get_trading_engine_db_session,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/trading-engine", tags=["trading-engine"])


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row._mapping)


@router.get("/orders")
async def list_orders(
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(200, ge=1, le=1000),
    status_filter: str | None = Query(None, alias="status"),
    session: AsyncSession = Depends(get_trading_engine_db_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Orders das últimas N horas (default 24h, máx 7d)."""
    where_status = "AND status = :status_filter" if status_filter else ""
    query = text(
        f"""
        SELECT id, broker_id, strategy, symbol, side, status,
               entry_price, stop_price, target_price, qty,
               filled_price, filled_qty, confidence, rationale,
               submitted_at, filled_at, created_at, updated_at
        FROM trading_engine_orders.orders
        WHERE submitted_at > NOW() - make_interval(hours => :hours)
        {where_status}
        ORDER BY submitted_at DESC
        LIMIT :limit
        """
    )
    params: dict[str, Any] = {"hours": hours, "limit": limit}
    if status_filter:
        params["status_filter"] = status_filter
    result = await session.execute(query, params)
    rows = [_row_to_dict(r) for r in result]
    return {"hours": hours, "count": len(rows), "orders": rows}


@router.get("/trade-journal")
async def list_trade_journal(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_trading_engine_db_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Trades fechados + agregados (PnL acumulado, hit rate, n_trades)."""
    agg_q = text(
        """
        SELECT
            COUNT(*) AS n_trades,
            COUNT(*) FILTER (WHERE is_winner) AS n_wins,
            COALESCE(SUM(pnl), 0) AS pnl_total,
            COALESCE(AVG(pnl), 0) AS pnl_avg,
            COALESCE(AVG(pnl) FILTER (WHERE is_winner), 0) AS avg_win,
            COALESCE(AVG(pnl) FILTER (WHERE is_winner = false), 0) AS avg_loss,
            MIN(entry_date) AS first_entry,
            MAX(COALESCE(exit_date, entry_date)) AS last_exit
        FROM trading_engine_orders.trade_journal
        WHERE entry_date > NOW() - make_interval(days => :days)
          AND is_complete = true
        """
    )
    rows_q = text(
        """
        SELECT id, ticker, direction, entry_date, exit_date,
               entry_price, exit_price, quantity, pnl, pnl_pct,
               is_winner, setup, timeframe, external_order_id
        FROM trading_engine_orders.trade_journal
        WHERE entry_date > NOW() - make_interval(days => :days)
          AND is_complete = true
        ORDER BY COALESCE(exit_date, entry_date) DESC
        LIMIT :limit
        """
    )
    agg_row = (await session.execute(agg_q, {"days": days})).first()
    agg = _row_to_dict(agg_row) if agg_row else {}
    n_trades = agg.get("n_trades") or 0
    n_wins = agg.get("n_wins") or 0
    agg["hit_rate"] = (n_wins / n_trades) if n_trades else 0.0
    rows = [_row_to_dict(r) for r in await session.execute(rows_q, {"days": days, "limit": limit})]
    return {"days": days, "aggregates": agg, "trades": rows}


@router.get("/engine-events")
async def list_engine_events(
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(500, ge=1, le=5000),
    event_type: str | None = Query(None),
    session: AsyncSession = Depends(get_trading_engine_db_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Audit feed (event_type filtrável)."""
    where_type = "AND event_type = :event_type" if event_type else ""
    query = text(
        f"""
        SELECT ts, event_type, key, payload
        FROM trading_engine_orders.engine_events
        WHERE ts > NOW() - make_interval(hours => :hours)
        {where_type}
        ORDER BY ts DESC
        LIMIT :limit
        """
    )
    params: dict[str, Any] = {"hours": hours, "limit": limit}
    if event_type:
        params["event_type"] = event_type
    rows = [_row_to_dict(r) for r in await session.execute(query, params)]
    return {"hours": hours, "count": len(rows), "events": rows}


@router.get("/backtests")
async def list_backtests(
    limit: int = Query(50, ge=1, le=500),
    strategy: str | None = Query(None),
    symbol: str | None = Query(None),
    session: AsyncSession = Depends(get_trading_engine_db_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Lista backtests persistidos (sem equity curve — leve)."""
    filters = []
    params: dict[str, Any] = {"limit": limit}
    if strategy:
        filters.append("strategy = :strategy")
        params["strategy"] = strategy
    if symbol:
        filters.append("symbol = :symbol")
        params["symbol"] = symbol
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    query = text(
        f"""
        SELECT run_id, strategy, symbol, timeframe, from_ts, to_ts,
               git_sha, pnl_total, sharpe, calmar, max_drawdown,
               hit_rate, profit_factor, payoff, n_trades,
               duration_ms, created_at
        FROM trading_engine_orders.backtest_runs
        {where}
        ORDER BY created_at DESC
        LIMIT :limit
        """
    )
    rows = [_row_to_dict(r) for r in await session.execute(query, params)]
    return {"count": len(rows), "backtests": rows}


@router.get("/backtests/{run_id}")
async def get_backtest(
    run_id: str,
    session: AsyncSession = Depends(get_trading_engine_db_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Detalhe completo de um backtest (inclui equity_curve + params)."""
    query = text(
        """
        SELECT *
        FROM trading_engine_orders.backtest_runs
        WHERE run_id = :run_id
        """
    )
    row = (await session.execute(query, {"run_id": run_id})).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"backtest run_id={run_id} não encontrado",
        )
    return _row_to_dict(row)
