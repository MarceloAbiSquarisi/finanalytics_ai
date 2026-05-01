"""
Endpoints do robo de trade (R1).

GET    /api/v1/robot/status             — paused, last heartbeat, signals 24h, P&L
GET    /api/v1/robot/strategies         — registry com enabled/config/account
GET    /api/v1/robot/signals_log?limit  — auditoria recente (proxy paginado)
PUT    /api/v1/robot/pause              — kill switch ON (sudo)
PUT    /api/v1/robot/resume             — kill switch OFF (sudo)

Acesso ao TimescaleDB via psycopg2 (sync) — robo nao mora no PG primario.
Read endpoints liberados a usuarios autenticados; mutate (pause/resume)
exige sudo_token (mesmo padrao /agent/restart).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
import structlog

from finanalytics_ai.domain.auth.entities import User
from finanalytics_ai.interfaces.api.dependencies import get_current_user, require_sudo

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/robot", tags=["robot"])


def _resolve_dsn() -> str:
    """
    Resolve DSN para psycopg2 sync. Container-aware: PROFIT_TIMESCALE_DSN
    aponta p/ localhost:5433 (host view, .env compartilhado) — quando rodando
    dentro do container, converte para hostname interno timescale:5432.

    Ordem:
      1. ROBOT_TIMESCALE_DSN explicito (override)
      2. TIMESCALE_URL (asyncpg form -> converte para psycopg2 sync)
      3. PROFIT_TIMESCALE_DSN (com fallback localhost->timescale se in-container)
    """
    if explicit := os.environ.get("ROBOT_TIMESCALE_DSN"):
        return explicit

    if ts_url := os.environ.get("TIMESCALE_URL"):
        # postgresql+asyncpg://... -> postgresql://...
        return ts_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    raw = os.environ.get(
        "PROFIT_TIMESCALE_DSN",
        "postgresql://finanalytics:timescale_secret@timescale:5432/market_data",
    )
    # Heurística: rodando em container e DSN aponta para localhost? Reescreve.
    if os.path.exists("/.dockerenv") and "localhost" in raw:
        return raw.replace("localhost:5433", "timescale:5432").replace(
            "127.0.0.1:5433", "timescale:5432"
        )
    return raw


def _conn():
    """psycopg2 connection. Sync e pequeno, sem pool."""
    import psycopg2

    return psycopg2.connect(_resolve_dsn())


# ── Schemas ───────────────────────────────────────────────────────────────────


class PauseRequest(BaseModel):
    reason: str = Field("manual", min_length=1, max_length=200)


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/status")
async def robot_status(_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """
    Snapshot rapido para UI:
      - paused (kill switch)
      - n strategies enabled
      - signals nas ultimas 24h (sent_to_dll vs skipped)
      - heartbeat mais recente
      - P&L do dia (placeholder — cobre Phase 2)
    """
    today = date.today()
    cutoff = datetime.now(UTC) - timedelta(hours=24)

    try:
        with _conn() as conn, conn.cursor() as cur:
            # Risk state
            cur.execute(
                """SELECT paused, paused_reason, paused_at, total_pnl,
                          realized_pnl, positions_count, updated_at
                   FROM robot_risk_state WHERE date = %s""",
                (today,),
            )
            risk = cur.fetchone()

            # Strategies enabled
            cur.execute("SELECT COUNT(*) FROM robot_strategies WHERE enabled = TRUE")
            n_enabled = cur.fetchone()[0]

            # Signals 24h
            cur.execute(
                """SELECT COUNT(*) FILTER (WHERE sent_to_dll = TRUE) AS sent,
                          COUNT(*) FILTER (WHERE sent_to_dll = FALSE) AS skipped,
                          COUNT(*) AS total
                   FROM robot_signals_log
                   WHERE computed_at >= %s""",
                (cutoff,),
            )
            sent, skipped, total = cur.fetchone()

            # Last heartbeat
            cur.execute(
                """SELECT computed_at, action, reason_skipped
                   FROM robot_signals_log
                   ORDER BY computed_at DESC LIMIT 1"""
            )
            last = cur.fetchone()
    except Exception as exc:
        logger.error("robot.status.db_error", error=str(exc))
        raise HTTPException(503, f"Erro acessando robot DB: {exc}") from exc

    return {
        "paused": bool(risk[0]) if risk else False,
        "paused_reason": risk[1] if risk else None,
        "paused_at": risk[2].isoformat() if risk and risk[2] else None,
        "n_strategies_enabled": int(n_enabled),
        "signals_24h": {
            "sent_to_dll": int(sent or 0),
            "skipped": int(skipped or 0),
            "total": int(total or 0),
        },
        "last_signal": (
            {
                "computed_at": last[0].isoformat() if last[0] else None,
                "action": last[1],
                "reason": last[2],
            }
            if last
            else None
        ),
        "pnl_today": {
            "total": float(risk[3] or 0.0) if risk else 0.0,
            "realized": float(risk[4] or 0.0) if risk else 0.0,
            "positions_count": int(risk[5] or 0) if risk else 0,
        },
        "updated_at": risk[6].isoformat() if risk and risk[6] else None,
    }


@router.get("/strategies")
async def robot_strategies(_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Registry completo de strategies (enabled + disabled)."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, enabled, config_json, account_id, description,
                          created_at, updated_at
                   FROM robot_strategies ORDER BY id"""
            )
            rows = cur.fetchall()
    except Exception as exc:
        raise HTTPException(503, f"Erro acessando robot DB: {exc}") from exc

    items = [
        {
            "id": r[0],
            "name": r[1],
            "enabled": bool(r[2]),
            "config": r[3] or {},
            "account_id": r[4],
            "description": r[5],
            "created_at": r[6].isoformat() if r[6] else None,
            "updated_at": r[7].isoformat() if r[7] else None,
        }
        for r in rows
    ]
    return {"count": len(items), "items": items}


@router.get("/signals_log")
async def robot_signals_log(
    _user: User = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=500),
    strategy: str | None = Query(None),
    ticker: str | None = Query(None),
    only_sent: bool = Query(False),
) -> dict[str, Any]:
    """Auditoria paginada de signals."""
    where = []
    params: list[Any] = []
    if strategy:
        where.append("strategy_name = %s")
        params.append(strategy)
    if ticker:
        where.append("ticker = %s")
        params.append(ticker.upper())
    if only_sent:
        where.append("sent_to_dll = TRUE")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    sql = f"""
        SELECT id, strategy_id, strategy_name, ticker, action, computed_at,
               sent_to_dll, local_order_id, reason_skipped, payload_json
        FROM robot_signals_log
        {where_sql}
        ORDER BY computed_at DESC
        LIMIT %s
    """
    params.append(limit)

    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    except Exception as exc:
        raise HTTPException(503, f"Erro acessando robot DB: {exc}") from exc

    items = [
        {
            "id": r[0],
            "strategy_id": r[1],
            "strategy_name": r[2],
            "ticker": r[3],
            "action": r[4],
            "computed_at": r[5].isoformat() if r[5] else None,
            "sent_to_dll": bool(r[6]),
            "local_order_id": r[7],
            "reason_skipped": r[8],
            "payload": r[9],
        }
        for r in rows
    ]
    return {"count": len(items), "limit": limit, "items": items}


@router.put("/pause")
async def robot_pause(
    body: PauseRequest,
    user: User = Depends(require_sudo),
) -> dict[str, Any]:
    """
    Aciona kill switch — UPSERT em robot_risk_state.paused=true. Bloqueia
    novas entradas IMEDIATAMENTE no proximo tick do worker. Posicoes abertas
    NAO sao zeradas (responsabilidade de OCO/SL existentes).
    """
    today = date.today()
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO robot_risk_state (date, paused, paused_at, paused_reason)
                   VALUES (%s, TRUE, NOW(), %s)
                   ON CONFLICT (date) DO UPDATE
                   SET paused = TRUE, paused_at = NOW(), paused_reason = EXCLUDED.paused_reason,
                       updated_at = NOW()""",
                (today, body.reason),
            )
            conn.commit()
    except Exception as exc:
        logger.error("robot.pause.db_error", error=str(exc))
        raise HTTPException(503, f"Erro acessando robot DB: {exc}") from exc

    logger.warning("robot.paused", user=user.email, reason=body.reason)
    return {"paused": True, "reason": body.reason, "by": user.email}


@router.put("/resume")
async def robot_resume(user: User = Depends(require_sudo)) -> dict[str, Any]:
    """Sobe kill switch — UPSERT paused=false. Worker volta a operar no proximo tick."""
    today = date.today()
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO robot_risk_state (date, paused)
                   VALUES (%s, FALSE)
                   ON CONFLICT (date) DO UPDATE
                   SET paused = FALSE, paused_at = NULL, paused_reason = NULL,
                       updated_at = NOW()""",
                (today,),
            )
            conn.commit()
    except Exception as exc:
        raise HTTPException(503, f"Erro acessando robot DB: {exc}") from exc

    logger.warning("robot.resumed", user=user.email)
    return {"paused": False, "by": user.email}
