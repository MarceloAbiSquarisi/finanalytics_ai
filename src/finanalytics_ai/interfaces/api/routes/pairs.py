"""
Endpoints de pairs trading (R3.3) — read-only stub.

GET    /api/v1/pairs/active       — pares cointegrados ativos com Z-score current
GET    /api/v1/pairs/positions    — posições abertas (robot_pair_positions)

Tabelas em Postgres principal (Alembic 0023, 0024). Acesso via psycopg2 sync.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
import structlog

from finanalytics_ai.domain.auth.entities import User
from finanalytics_ai.domain.pairs.cointegration import compute_residuals
from finanalytics_ai.domain.pairs.strategy_logic import (
    DEFAULT_Z_ENTRY,
    DEFAULT_Z_EXIT,
    DEFAULT_Z_STOP,
    compute_zscore,
)
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


class ZScorePoint(BaseModel):
    date: str  # ISO YYYY-MM-DD
    spread: float
    z: float | None  # None nos primeiros lookback_days bars (rolling window não cheia)


class ZScoreHistoryOut(BaseModel):
    pair_key: str
    ticker_a: str
    ticker_b: str
    beta: float
    lookback_days: int  # janela rolling do z-score
    points: list[ZScorePoint]


class ZScoreOut(BaseModel):
    pair_key: str
    ticker_a: str
    ticker_b: str
    beta: float
    z: float | None  # None quando série degenerada (std=0) ou bars insuficientes
    current_spread: float | None
    spread_mean: float | None
    spread_std: float | None
    history_size: int
    bars_age_days: int | None  # gap entre last bar e hoje (data freshness)
    reason_skipped: str | None  # populado se z=None (debug)
    # Limiares de referência (mesmo do PairsTradingStrategy default — UI usa
    # pra colorir verde/amarelo/vermelho)
    z_entry: float = DEFAULT_Z_ENTRY
    z_exit: float = DEFAULT_Z_EXIT
    z_stop: float = DEFAULT_Z_STOP


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


@router.get("/zscores/{pair_key}/history", response_model=ZScoreHistoryOut)
async def get_zscore_history(
    request: Request,
    pair_key: str,
    days: int = Query(180, ge=30, le=504, description="Total de bars retornados"),
    lookback_days: int = Query(60, ge=20, le=252, description="Janela rolling do z-score"),
    _user: User = Depends(get_current_user),
) -> ZScoreHistoryOut:
    """
    Histórico de Z-score pra um par específico — drilldown pra UI plottar
    chart timeseries.

    Calcula rolling z-score: pra cada bar `t`, z(t) = (spread[t] - mean(spread[t-lookback:t]))
    / std(spread[t-lookback:t]). Primeiros `lookback_days` bars retornam z=None.

    pair_key esperado em formato 'TICKER_A-TICKER_B' (ordem alfabética
    canônica de cointegrated_pairs).
    """
    if "-" not in pair_key:
        raise HTTPException(400, f"pair_key invalido (esperado A-B): {pair_key}")
    ticker_a, ticker_b = pair_key.split("-", 1)

    # Lookup beta + valida que par existe e cointegrou
    sql = """
        SELECT beta FROM cointegrated_pairs
         WHERE ticker_a = %s AND ticker_b = %s AND cointegrated = TRUE
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (ticker_a, ticker_b))
            row = cur.fetchone()
    except Exception as exc:
        logger.error("pairs.zscore_history.db_error", error=str(exc))
        raise HTTPException(503, f"DB error: {exc}") from exc
    if row is None:
        raise HTTPException(404, f"Par {pair_key} nao encontrado ou nao cointegrado")
    beta = float(row[0])

    market = getattr(request.app.state, "market_client", None)
    if market is None:
        raise HTTPException(503, "market_client indisponivel")

    range_period = "1y" if days <= 252 else "2y"
    try:
        bars_a = await market.get_ohlc_bars(ticker_a, range_period=range_period)
        bars_b = await market.get_ohlc_bars(ticker_b, range_period=range_period)
    except Exception as exc:
        raise HTTPException(503, f"Falha ao buscar bars: {exc}") from exc

    if not bars_a or not bars_b:
        raise HTTPException(404, "Bars indisponíveis pra um dos tickers")

    # Alinha pelo N comum mais recente
    n_target = days + lookback_days  # buffer pra rolling window inicial
    n = min(len(bars_a), len(bars_b), n_target)
    bars_a = bars_a[-n:]
    bars_b = bars_b[-n:]

    from datetime import UTC, datetime

    points: list[ZScorePoint] = []
    spreads: list[float] = []
    for ba, bb in zip(bars_a, bars_b, strict=False):
        ca = ba.get("close")
        cb = bb.get("close")
        if ca is None or cb is None:
            continue
        spread = float(ca) - beta * float(cb)
        spreads.append(spread)
        # Rolling z (excluindo o ponto atual do mean/std — usa bars anteriores)
        if len(spreads) <= lookback_days:
            z_val = None
        else:
            window = spreads[-lookback_days - 1 : -1]  # últimos lookback bars (excluindo current)
            mean = sum(window) / len(window)
            var = sum((s - mean) ** 2 for s in window) / max(1, len(window) - 1)
            std = var**0.5
            z_val = (spread - mean) / std if std > 0 else None
        # Date parsing — bars trazem time epoch ou date string
        ts = ba.get("time") or ba.get("date")
        try:
            if isinstance(ts, int | float):
                date_iso = datetime.fromtimestamp(int(ts), UTC).date().isoformat()
            else:
                date_iso = (
                    datetime.fromisoformat(str(ts).replace("Z", "+00:00")).date().isoformat()
                )
        except Exception:
            date_iso = "unknown"
        points.append(ZScorePoint(date=date_iso, spread=spread, z=z_val))

    # Trim aos últimos `days` pontos (descarta os primeiros lookback_days que ficaram None)
    points = points[-days:]

    return ZScoreHistoryOut(
        pair_key=pair_key,
        ticker_a=ticker_a,
        ticker_b=ticker_b,
        beta=beta,
        lookback_days=lookback_days,
        points=points,
    )


@router.get("/zscores", response_model=list[ZScoreOut])
async def list_zscores(
    request: Request,
    lookback_days: int = Query(60, ge=20, le=252, description="Janela do Z-score"),
    _user: User = Depends(get_current_user),
) -> list[ZScoreOut]:
    """
    Z-score atual de cada par cointegrado ativo. Calcula on-the-fly:
      1. Lê ativos de cointegrated_pairs (mesmos filtros que /active)
      2. Para cada par, fetch closes 1y de A e B via market_client (DB → Yahoo → BRAPI)
      3. Alinha pelo N comum, calcula spread = A - beta * B
      4. spread_history = todos menos último, current_spread = último
      5. z = (current - mean) / std

    Mesma janela default (60d) que o worker em PairsServiceConfig. Caller
    pode customizar via ?lookback_days= entre 20 e 252.

    Datapoints retornados:
      - z = None se bars insuficientes ou std degenerado (reason_skipped explica)
      - bars_age_days = gap entre last bar e hoje (audit data freshness)
    """
    market = getattr(request.app.state, "market_client", None)
    if market is None:
        raise HTTPException(503, "market_client indisponivel")

    sql = """
        SELECT ticker_a, ticker_b, beta
          FROM cointegrated_pairs
         WHERE cointegrated = TRUE
         ORDER BY p_value_adf ASC
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql)
            pairs = cur.fetchall()
    except Exception as exc:
        logger.error("pairs.zscores.db_error", error=str(exc))
        raise HTTPException(503, f"Erro acessando pairs DB: {exc}") from exc

    n_needed = lookback_days + 5  # buffer p/ alinhamento + 1 ponto current
    out: list[ZScoreOut] = []
    from datetime import UTC, datetime

    today = datetime.now(UTC).date()

    for ticker_a, ticker_b, beta in pairs:
        beta = float(beta)
        try:
            bars_a = await market.get_ohlc_bars(ticker_a, range_period="1y")
            bars_b = await market.get_ohlc_bars(ticker_b, range_period="1y")
        except Exception as exc:
            logger.warning(
                "pairs.zscores.fetch_failed",
                pair=f"{ticker_a}-{ticker_b}",
                error=str(exc),
            )
            out.append(
                ZScoreOut(
                    pair_key=f"{ticker_a}-{ticker_b}",
                    ticker_a=ticker_a,
                    ticker_b=ticker_b,
                    beta=beta,
                    z=None,
                    current_spread=None,
                    spread_mean=None,
                    spread_std=None,
                    history_size=0,
                    bars_age_days=None,
                    reason_skipped=f"fetch_failed: {exc}",
                )
            )
            continue

        if not bars_a or not bars_b:
            out.append(
                ZScoreOut(
                    pair_key=f"{ticker_a}-{ticker_b}",
                    ticker_a=ticker_a,
                    ticker_b=ticker_b,
                    beta=beta,
                    z=None,
                    current_spread=None,
                    spread_mean=None,
                    spread_std=None,
                    history_size=0,
                    bars_age_days=None,
                    reason_skipped="no_bars",
                )
            )
            continue

        # Alinha pelo N comum mais recente
        n = min(len(bars_a), len(bars_b), n_needed)
        if n < lookback_days + 1:
            out.append(
                ZScoreOut(
                    pair_key=f"{ticker_a}-{ticker_b}",
                    ticker_a=ticker_a,
                    ticker_b=ticker_b,
                    beta=beta,
                    z=None,
                    current_spread=None,
                    spread_mean=None,
                    spread_std=None,
                    history_size=n - 1 if n > 0 else 0,
                    bars_age_days=None,
                    reason_skipped=f"insufficient_bars ({n} < {lookback_days + 1})",
                )
            )
            continue

        ca = [float(b["close"]) for b in bars_a[-n:] if b.get("close")]
        cb = [float(b["close"]) for b in bars_b[-n:] if b.get("close")]
        n_aligned = min(len(ca), len(cb))
        ca = ca[-n_aligned:]
        cb = cb[-n_aligned:]

        residuals = compute_residuals(ca, cb, beta)
        spread_history = list(residuals[:-1])
        current_spread = float(residuals[-1])
        z = compute_zscore(spread_history, current_spread)

        # Audit data freshness
        bars_age = None
        try:
            last_bar = bars_a[-1]
            ts = last_bar.get("time") or last_bar.get("date")
            if ts is not None:
                if isinstance(ts, int | float):
                    last_date = datetime.fromtimestamp(int(ts), UTC).date()
                else:
                    last_date = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).date()
                bars_age = (today - last_date).days
        except Exception:
            pass

        if z is None:
            out.append(
                ZScoreOut(
                    pair_key=f"{ticker_a}-{ticker_b}",
                    ticker_a=ticker_a,
                    ticker_b=ticker_b,
                    beta=beta,
                    z=None,
                    current_spread=current_spread,
                    spread_mean=None,
                    spread_std=None,
                    history_size=len(spread_history),
                    bars_age_days=bars_age,
                    reason_skipped="zscore_undefined (std degenerado)",
                )
            )
            continue

        spread_mean = sum(spread_history) / len(spread_history)
        # variance estimator com ddof=1 (mesmo do compute_zscore)
        var = sum((s - spread_mean) ** 2 for s in spread_history) / max(
            1, len(spread_history) - 1
        )
        spread_std = var**0.5

        out.append(
            ZScoreOut(
                pair_key=f"{ticker_a}-{ticker_b}",
                ticker_a=ticker_a,
                ticker_b=ticker_b,
                beta=beta,
                z=z,
                current_spread=current_spread,
                spread_mean=spread_mean,
                spread_std=spread_std,
                history_size=len(spread_history),
                bars_age_days=bars_age,
                reason_skipped=None,
            )
        )

    return out


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
