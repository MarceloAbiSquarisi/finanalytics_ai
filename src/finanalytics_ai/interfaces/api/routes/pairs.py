"""
Endpoints de pairs trading (R3.3) — read-only stub.

GET    /api/v1/pairs/active       — pares cointegrados ativos com Z-score current
GET    /api/v1/pairs/positions    — posições abertas (robot_pair_positions)

Tabelas em Postgres principal (Alembic 0023, 0024). Acesso via psycopg2 sync.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import structlog

from finanalytics_ai.domain.auth.entities import User
from finanalytics_ai.interfaces.api.dependencies import get_current_user

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/pairs", tags=["pairs"])


def _resolve_pairs_dsn() -> str:
    """
    DSN p/ Postgres principal (cointegrated_pairs + robot_pair_positions).
    Container-aware: localhost -> postgres hostname.
    """
    if explicit := os.environ.get("PAIRS_DSN"):
        return explicit

    if db_url := os.environ.get("DATABASE_URL_SYNC"):
        return db_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    if db_url := os.environ.get("DATABASE_URL"):
        return db_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    raw = "postgresql://finanalytics:secret@postgres:5432/finanalytics"
    if os.path.exists("/.dockerenv") and "localhost" in raw:
        return raw.replace("localhost:5432", "postgres:5432")
    return raw


def _conn():
    import psycopg2

    return psycopg2.connect(_resolve_pairs_dsn())


# ── Schemas ───────────────────────────────────────────────────────────────────


class ActivePairOut(BaseModel):
    pair_key: str
    ticker_a: str
    ticker_b: str
    beta: float
    rho: float
    p_value_adf: float
    half_life: float | None
    lookback_days: int
    last_test_date: str  # ISO date
    position: str | None  # 'LONG_SPREAD' | 'SHORT_SPREAD' | None se nao aberta


class PositionOut(BaseModel):
    pair_key: str
    position: str
    opened_at: str  # ISO datetime
    last_dispatch_cl_ord_id: str | None


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/active", response_model=list[ActivePairOut])
async def list_active_pairs(
    _user: User = Depends(get_current_user),
) -> list[ActivePairOut]:
    """
    Lista pares cointegrados ativos (cointegrated=TRUE), com info da posição
    aberta se houver. LEFT JOIN com robot_pair_positions p/ saber posição.
    Ordem: p_value_adf ASC (mais cointegrados primeiro).
    """
    sql = """
        SELECT cp.ticker_a, cp.ticker_b, cp.beta, cp.rho, cp.p_value_adf,
               cp.half_life, cp.lookback_days, cp.last_test_date,
               pp.position
          FROM cointegrated_pairs cp
          LEFT JOIN robot_pair_positions pp
                 ON pp.pair_key = cp.ticker_a || '-' || cp.ticker_b
         WHERE cp.cointegrated = TRUE
         ORDER BY cp.p_value_adf ASC
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    except Exception as exc:
        logger.error("pairs.active.db_error", error=str(exc))
        raise HTTPException(503, f"Erro acessando pairs DB: {exc}") from exc

    return [
        ActivePairOut(
            pair_key=f"{a}-{b}",
            ticker_a=a,
            ticker_b=b,
            beta=float(beta),
            rho=float(rho),
            p_value_adf=float(p_adf),
            half_life=float(hl) if hl is not None else None,
            lookback_days=int(lb),
            last_test_date=ltd.isoformat(),
            position=pos,
        )
        for (a, b, beta, rho, p_adf, hl, lb, ltd, pos) in rows
    ]


@router.get("/positions", response_model=list[PositionOut])
async def list_positions(_user: User = Depends(get_current_user)) -> list[PositionOut]:
    """Lista posições atualmente abertas no PairsTradingStrategy."""
    sql = """
        SELECT pair_key, position, opened_at, last_dispatch_cl_ord_id
          FROM robot_pair_positions
         ORDER BY opened_at DESC
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    except Exception as exc:
        logger.error("pairs.positions.db_error", error=str(exc))
        raise HTTPException(503, f"Erro acessando pairs DB: {exc}") from exc

    return [
        PositionOut(
            pair_key=pk,
            position=pos,
            opened_at=oa.isoformat(),
            last_dispatch_cl_ord_id=clid,
        )
        for (pk, pos, oa, clid) in rows
    ]
