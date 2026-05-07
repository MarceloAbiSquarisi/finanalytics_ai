"""
finanalytics_ai.interfaces.api.routes.admin
Acesso restrito a UserRole.MASTER
Endpoints:
  GET    /api/v1/admin/users
  POST   /api/v1/admin/users
  PATCH  /api/v1/admin/users/{id}/role
  PATCH  /api/v1/admin/users/{id}/active
  POST   /api/v1/admin/users/{id}/reset-password
  GET    /api/v1/admin/agents
  POST   /api/v1/admin/agents
  PATCH  /api/v1/admin/agents/{id}
  DELETE /api/v1/admin/agents/{id}
  POST   /api/v1/admin/bootstrap
  POST   /api/v1/admin/ohlc/rebuild  (sessão 30/abr)
"""

from datetime import date as _date, timedelta
import os
import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from finanalytics_ai.domain.auth.entities import User, UserRole
from finanalytics_ai.infrastructure.auth.password_hasher import get_password_hasher
from finanalytics_ai.infrastructure.database.repositories.admin_repo import FinancialAgentRepository
from finanalytics_ai.infrastructure.database.repositories.user_repo import UserModel, UserRepository
from finanalytics_ai.interfaces.api.dependencies import get_current_user, get_db_session

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])

MASTER_EMAIL = "marceloabisquarisi@gmail.com"

# ── Guard ─────────────────────────────────────────────────────────────────────


def require_master(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.has_admin_access:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Acesso restrito a administradores.")
    return current_user


# ── Schemas ───────────────────────────────────────────────────────────────────


class CreateUserRequest(BaseModel):
    email: str = Field(..., min_length=5)
    full_name: str = Field(..., min_length=2)
    password: str = Field(..., min_length=8)
    role: str = Field(default="user")
    is_admin: bool = Field(default=False)


class ChangeRoleRequest(BaseModel):
    role: str


class ChangeAdminFlagRequest(BaseModel):
    is_admin: bool


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=8)


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=2)
    code: str | None = None
    agent_type: str = "corretora"
    country: str = "BRA"
    website: str | None = None
    is_active: bool = True
    note: str | None = None


class AgentUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    agent_type: str | None = None
    country: str | None = None
    website: str | None = None
    is_active: bool | None = None
    note: str | None = None


# ── Bootstrap ─────────────────────────────────────────────────────────────────


async def run_bootstrap(session: AsyncSession) -> dict:
    """Garante que marceloabisquarisi é sempre MASTER."""
    from sqlalchemy import select, update as sa_update

    res = await session.execute(select(UserModel).where(UserModel.email == MASTER_EMAIL))
    user = res.scalar_one_or_none()
    if not user:
        return {"status": "not_found", "email": MASTER_EMAIL}
    if user.role == UserRole.MASTER.value:
        return {"status": "already_master", "user_id": user.user_id}
    await session.execute(
        sa_update(UserModel)
        .where(UserModel.email == MASTER_EMAIL)
        .values(role=UserRole.MASTER.value, is_active=True)
    )
    await session.commit()
    logger.info("bootstrap.master_promoted", email=MASTER_EMAIL)
    return {"status": "promoted", "user_id": user.user_id}


@router.post("/bootstrap", status_code=200, include_in_schema=False)
async def bootstrap(session: AsyncSession = Depends(get_db_session)) -> dict:
    return await run_bootstrap(session)


# ── Users ─────────────────────────────────────────────────────────────────────


@router.get("/users")
async def list_users(
    _: User = Depends(require_master), session: AsyncSession = Depends(get_db_session)
) -> list[dict]:
    from sqlalchemy import select

    res = await session.execute(select(UserModel).order_by(UserModel.created_at.desc()))
    users = res.scalars().all()
    return [
        {
            "user_id": u.user_id,
            "email": u.email,
            "full_name": u.full_name,
            "role": u.role,
            "is_admin": bool(getattr(u, "is_admin", False)),
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "totp_enabled": u.totp_enabled,
        }
        for u in users
    ]


@router.post("/users", status_code=201)
async def create_user(
    body: CreateUserRequest,
    _: User = Depends(require_master),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    repo = UserRepository(session)
    if await repo.email_exists(body.email):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="E-mail já cadastrado.")
    try:
        role = UserRole(body.role)
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Role inválido: {body.role}"
        )
    hasher = get_password_hasher()
    user = User(
        user_id=str(uuid.uuid4()),
        email=body.email.lower().strip(),
        hashed_password=hasher.hash(body.password),
        full_name=body.full_name,
        role=role,
        is_active=True,
        is_admin=bool(body.is_admin),
    )
    created = await repo.create(user)
    await session.commit()
    return {
        "user_id": created.user_id,
        "email": created.email,
        "role": created.role.value,
        "is_admin": created.is_admin,
    }


@router.patch("/users/{user_id}/role")
async def change_role(
    user_id: str,
    body: ChangeRoleRequest,
    actor: User = Depends(require_master),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    from sqlalchemy import update as sa_update

    # Protege o dono do sistema
    res = await session.execute(
        __import__("sqlalchemy", fromlist=["select"])
        .select(UserModel)
        .where(UserModel.user_id == user_id)
    )
    target = res.scalar_one_or_none()
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")
    if target.email == MASTER_EMAIL and body.role != UserRole.MASTER.value:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="Não é possível rebaixar o usuário master do sistema."
        )
    # 'admin' virou flag ortogonal (use /admin-flag), não é mais role valido.
    if body.role == "admin":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Admin agora é flag separada. Use PATCH /users/{id}/admin-flag.",
        )
    try:
        UserRole(body.role)
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Role inválido: {body.role}"
        )
    await session.execute(
        sa_update(UserModel).where(UserModel.user_id == user_id).values(role=body.role)
    )
    await session.commit()
    logger.info("admin.role_changed", target=user_id, role=body.role, actor=actor.user_id)
    return {"user_id": user_id, "role": body.role}


@router.patch("/users/{user_id}/admin-flag")
async def change_admin_flag(
    user_id: str,
    body: ChangeAdminFlagRequest,
    actor: User = Depends(require_master),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Liga/desliga a flag is_admin — ortogonal a role (user/master)."""
    from sqlalchemy import select, update as sa_update

    res = await session.execute(select(UserModel).where(UserModel.user_id == user_id))
    target = res.scalar_one_or_none()
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")
    await session.execute(
        sa_update(UserModel)
        .where(UserModel.user_id == user_id)
        .values(is_admin=bool(body.is_admin))
    )
    await session.commit()
    logger.info(
        "admin.admin_flag_changed", target=user_id, is_admin=body.is_admin, actor=actor.user_id
    )
    return {"user_id": user_id, "is_admin": bool(body.is_admin)}


@router.patch("/users/{user_id}/active")
async def toggle_active(
    user_id: str,
    actor: User = Depends(require_master),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    from sqlalchemy import select, update as sa_update

    res = await session.execute(select(UserModel).where(UserModel.user_id == user_id))
    target = res.scalar_one_or_none()
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")
    if target.email == MASTER_EMAIL:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="Não é possível desativar o usuário master."
        )
    new_status = not target.is_active
    await session.execute(
        sa_update(UserModel).where(UserModel.user_id == user_id).values(is_active=new_status)
    )
    await session.commit()
    logger.info("admin.active_toggled", target=user_id, is_active=new_status, actor=actor.user_id)
    return {"user_id": user_id, "is_active": new_status}


@router.post("/users/{user_id}/reset-password")
async def admin_reset_password(
    user_id: str,
    body: ResetPasswordRequest,
    actor: User = Depends(require_master),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    from sqlalchemy import select

    res = await session.execute(select(UserModel).where(UserModel.user_id == user_id))
    target = res.scalar_one_or_none()
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")
    hasher = get_password_hasher()
    target.hashed_password = hasher.hash(body.new_password)
    target.reset_token = None
    target.reset_token_exp = None
    await session.commit()
    logger.info("admin.password_reset", target=user_id, actor=actor.user_id)
    return {"message": "Senha redefinida com sucesso."}


# ── Financial Agents ──────────────────────────────────────────────────────────


@router.get("/agents")
async def list_agents(
    _: User = Depends(require_master), session: AsyncSession = Depends(get_db_session)
) -> list[dict]:
    return await FinancialAgentRepository(session).list_all()


@router.post("/agents", status_code=201)
async def create_agent(
    body: AgentCreate,
    _: User = Depends(require_master),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    agent = await FinancialAgentRepository(session).create(body.model_dump())
    await session.commit()
    return agent


@router.patch("/agents/{agent_id}")
async def update_agent(
    agent_id: str,
    body: AgentUpdate,
    _: User = Depends(require_master),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    updated = await FinancialAgentRepository(session).update(
        agent_id, body.model_dump(exclude_none=True)
    )
    if not updated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Agente não encontrado.")
    await session.commit()
    return updated


@router.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: str,
    _: User = Depends(require_master),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    ok = await FinancialAgentRepository(session).delete(agent_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Agente não encontrado.")
    await session.commit()


# ── OHLC rebuild (sessão 30/abr) ──────────────────────────────────────────────
# Reconstrói bars 1m a partir do continuous aggregate ohlc_1m_from_ticks
# (que filtra ticks fora do pregão B3, 12-21 UTC). Mesma lógica do
# tick_to_ohlc_backfill_job, mas acionável manualmente por admin para
# corrigir dias com ruído pre-market (heartbeats trade_type=3) ou
# bars stale após restart do agent.

_TS_DSN_RAW = (
    os.getenv("TIMESCALE_URL")
    or os.getenv("PROFIT_TIMESCALE_DSN")
    or "postgresql://finanalytics:timescale_secret@timescale:5432/market_data"
)
_TS_DSN = _TS_DSN_RAW.replace("postgresql+asyncpg://", "postgres://").replace(
    "postgresql://", "postgres://"
)


class OHLCRebuildRequest(BaseModel):
    date: _date = Field(..., description="Dia a reconstruir (YYYY-MM-DD)")
    ticker: str | None = Field(
        default=None,
        description="Ticker específico ou null para todos do dia (so' aplica a 1m)",
        max_length=20,
    )
    timeframe: str = Field(
        default="1m",
        description="Timeframe a reconstruir: 1m | 5m | 15m | 1h | 1d",
        pattern=r"^(1m|5m|15m|1h|1d)$",
    )


# Hierarquia das CAGGs (refresh upstream antes do alvo p/ garantir
# consistencia caso refresh policies estejam atrasadas).
_CAGG_HIERARCHY: list[str] = ["ohlc_5m", "ohlc_15m", "ohlc_1h", "ohlc_1d"]


@router.post("/ohlc/rebuild")
async def rebuild_ohlc_day(
    body: OHLCRebuildRequest,
    actor: User = Depends(require_master),
) -> dict:
    target = body.date.isoformat()
    ticker = (body.ticker or "").strip().upper() or None
    timeframe = body.timeframe

    conn = await asyncpg.connect(_TS_DSN)
    try:
        # ── 1m: DELETE+INSERT a partir do continuous aggregate raw ───────────
        if timeframe == "1m":
            async with conn.transaction():
                if ticker:
                    deleted_q = await conn.execute(
                        """
                        DELETE FROM ohlc_1m
                        WHERE time >= $1::date
                          AND time <  ($1::date + INTERVAL '1 day')
                          AND ticker = $2
                        """,
                        target, ticker,
                    )
                    inserted_q = await conn.execute(
                        """
                        INSERT INTO ohlc_1m (time, ticker, open, high, low, close, volume, trades, source)
                        SELECT time, ticker, open, high, low, close, volume, COALESCE(trades, 0), 'tick_agg_v1'
                        FROM ohlc_1m_from_ticks
                        WHERE time >= $1::date
                          AND time <  ($1::date + INTERVAL '1 day')
                          AND ticker = $2
                        """,
                        target, ticker,
                    )
                else:
                    deleted_q = await conn.execute(
                        """
                        DELETE FROM ohlc_1m
                        WHERE time >= $1::date
                          AND time <  ($1::date + INTERVAL '1 day')
                        """,
                        target,
                    )
                    inserted_q = await conn.execute(
                        """
                        INSERT INTO ohlc_1m (time, ticker, open, high, low, close, volume, trades, source)
                        SELECT time, ticker, open, high, low, close, volume, COALESCE(trades, 0), 'tick_agg_v1'
                        FROM ohlc_1m_from_ticks
                        WHERE time >= $1::date
                          AND time <  ($1::date + INTERVAL '1 day')
                        """,
                        target,
                    )
            deleted = int(deleted_q.split()[-1]) if deleted_q.startswith("DELETE") else 0
            inserted = int(inserted_q.split()[-1]) if inserted_q.startswith("INSERT") else 0
            logger.info(
                "admin.ohlc.rebuild",
                date=target, ticker=ticker, timeframe=timeframe,
                deleted=deleted, inserted=inserted, actor=actor.user_id,
            )
            return {
                "status": "ok",
                "date": target,
                "ticker": ticker,
                "timeframe": timeframe,
                "deleted": deleted,
                "inserted": inserted,
                "net_change": inserted - deleted,
            }

        # ── 5m/15m/1h/1d: refresh continuous aggregate ────────────────────────
        # CAGG refresh nao aceita filtro de ticker — opera por bucket. Se
        # ticker foi passado, ignora (com aviso no response).
        # Hierarquia: refresha upstream antes do alvo p/ consistencia.
        target_view = f"ohlc_{timeframe}"
        idx = _CAGG_HIERARCHY.index(target_view)
        chain = _CAGG_HIERARCHY[: idx + 1]
        bars_before = {}
        for view in chain:
            row = await conn.fetchrow(
                f"SELECT count(*) AS c FROM {view} "
                "WHERE time >= $1::date AND time < ($1::date + INTERVAL '1 day')",
                body.date,
            )
            bars_before[view] = int(row["c"]) if row else 0
        # CALL refresh_continuous_aggregate nao roda em transaction.
        # asyncpg connection default e' auto-commit (sem transaction()).
        # Datas validadas pelo Pydantic (_date), seguro p/ string-format.
        next_day = (body.date + timedelta(days=1)).isoformat()
        for view in chain:
            await conn.execute(
                f"CALL refresh_continuous_aggregate("
                f"'{view}', '{target}'::timestamptz, '{next_day}'::timestamptz)"
            )
        bars_after = {}
        for view in chain:
            row = await conn.fetchrow(
                f"SELECT count(*) AS c FROM {view} "
                "WHERE time >= $1::date AND time < ($1::date + INTERVAL '1 day')",
                body.date,
            )
            bars_after[view] = int(row["c"]) if row else 0

        logger.info(
            "admin.ohlc.cagg_refresh",
            date=target, timeframe=timeframe, chain=chain,
            bars_before=bars_before, bars_after=bars_after,
            actor=actor.user_id,
        )
        warnings = []
        if ticker:
            warnings.append(
                f"ticker={ticker} ignorado: CAGG refresh opera por bucket "
                "(re-agrega TODOS os tickers do dia)."
            )
        return {
            "status": "ok",
            "date": target,
            "ticker": None,
            "timeframe": timeframe,
            "refreshed_chain": chain,
            "bars_before": bars_before,
            "bars_after": bars_after,
            "deleted": 0,
            "inserted": sum(bars_after.values()) - sum(bars_before.values()),
            "net_change": sum(bars_after.values()) - sum(bars_before.values()),
            "warnings": warnings,
        }
    except Exception as exc:
        logger.warning(
            "admin.ohlc.rebuild_failed",
            error=str(exc), date=target, ticker=ticker, timeframe=timeframe,
        )
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    finally:
        await conn.close()


# ── Market Data gap analysis (07/mai) ─────────────────────────────────────────
# Identifica dias uteis sem dado para um ticker num range. Util pra escolher
# alvos de backfill direcionado depois de import incompleto / lacunas
# historicas.

_GAPS_SOURCES = {
    "ohlc_1m": ("ohlc_1m", "time"),
    "market_history_trades": ("market_history_trades", "trade_date"),
}


@router.get("/marketdata/gaps")
async def get_marketdata_gaps(
    ticker: str = Query(..., min_length=1, max_length=20),
    date_start: _date = Query(...),
    date_end: _date = Query(...),
    source: str = Query("ohlc_1m"),
    _: User = Depends(require_master),
) -> dict:
    if date_end < date_start:
        raise HTTPException(400, "date_end < date_start")
    if (date_end - date_start) > timedelta(days=730):
        raise HTTPException(400, "range maximo: 2 anos")
    if source not in _GAPS_SOURCES:
        raise HTTPException(400, f"source invalido. Use: {list(_GAPS_SOURCES)}")
    table, time_col = _GAPS_SOURCES[source]

    ticker_u = ticker.upper().strip()
    sql = f"""
        SELECT DISTINCT ({time_col})::date AS day
        FROM {table}
        WHERE ticker = $1
          AND {time_col} >= $2::date
          AND {time_col} <  ($3::date + INTERVAL '1 day')
        ORDER BY day
    """

    conn = await asyncpg.connect(_TS_DSN)
    try:
        rows = await conn.fetch(sql, ticker_u, date_start, date_end)
        present = {r["day"] for r in rows}
    finally:
        await conn.close()

    # gera lista de trading days no range (skip fim-de-semana e feriados B3)
    from finanalytics_ai.infrastructure.database.repositories.backfill_repo import (
        trading_days_in_range,
    )
    all_days = trading_days_in_range(date_start, date_end)
    missing = [d for d in all_days if d not in present]
    present_in_range = [d for d in all_days if d in present]
    coverage = (
        round(100.0 * len(present_in_range) / len(all_days), 1)
        if all_days else 0.0
    )

    return {
        "ticker": ticker_u,
        "source": source,
        "date_start": date_start.isoformat(),
        "date_end": date_end.isoformat(),
        "total_trading_days": len(all_days),
        "present_count": len(present_in_range),
        "missing_count": len(missing),
        "coverage_pct": coverage,
        "present_days": [d.isoformat() for d in present_in_range],
        "missing_days": [d.isoformat() for d in missing],
    }
