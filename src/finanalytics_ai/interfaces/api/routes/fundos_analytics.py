"""
Fundos Analytics (M3) — endpoints de análise sobre fundos CVM.

Separado de routes/fundos.py porque o `/{cnpj:path}` greedy lá captura
qualquer subpath (ex: /style/<cnpj> seria interpretado como cnpj=style).
Prefix dedicado /api/v1/fundos-analytics evita conflito.

Endpoints:
  GET /api/v1/fundos-analytics/style/{cnpj}       — regressão OLS vs fatores
  GET /api/v1/fundos-analytics/peer-ranking       — top-N por sharpe na categoria
  GET /api/v1/fundos-analytics/anomalies/{cnpj}   — saltos NAV > N σ
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from finanalytics_ai.interfaces.api.dependencies import get_db_session

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/fundos-analytics", tags=["Fundos Analytics"])


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _fetch_fund_log_returns(
    session: AsyncSession, cnpj: str, days: int, end_date=None
) -> list[tuple[Any, float]]:
    """Lê informes diários até `end_date` (default: max disponível) e calcula log returns."""
    from datetime import timedelta

    if end_date is None:
        max_row = await session.execute(
            text("SELECT MAX(data_ref) FROM fundos_informe_diario WHERE cnpj=:c"),
            {"c": cnpj},
        )
        end_date = max_row.scalar()
        if end_date is None:
            return []
        if hasattr(end_date, "date"):
            end_date = end_date.date()
    start_date = end_date - timedelta(days=days)

    rows = await session.execute(
        text(
            "SELECT data_ref::date AS dia, vl_quota::float AS vq "
            "FROM fundos_informe_diario WHERE cnpj=:c AND data_ref BETWEEN :s AND :e "
            "ORDER BY data_ref ASC"
        ),
        {"c": cnpj, "s": start_date, "e": end_date},
    )
    raw = [(r[0], float(r[1])) for r in rows.fetchall() if r[1] is not None and r[1] > 0]
    if len(raw) < 2:
        return []
    import math

    out: list[tuple[Any, float]] = []
    for i in range(1, len(raw)):
        prev = raw[i - 1][1]
        cur = raw[i][1]
        if prev > 0 and cur > 0:
            out.append((raw[i][0], math.log(cur / prev)))
    return out


def _fetch_factor_log_returns_sync(
    ticker: str, days: int, end_date=None
) -> list[tuple[Any, float]]:
    """Lê features_daily.close (banco TIMESCALE) entre [end_date - days, end_date]."""
    import math
    import os
    from datetime import date as _date, timedelta

    import psycopg2

    dsn = (
        os.environ.get("TIMESCALE_URL")
        or os.environ.get("PROFIT_TIMESCALE_DSN")
        or "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
    )
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    if end_date is None:
        end_date = _date.today()
    start_date = end_date - timedelta(days=days)
    out: list[tuple[Any, float]] = []
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT dia, close::float FROM features_daily "
            "WHERE ticker=%s AND dia BETWEEN %s AND %s ORDER BY dia ASC",
            (ticker, start_date, end_date),
        )
        raw = [(r[0], float(r[1])) for r in cur.fetchall() if r[1] is not None and r[1] > 0]
    for i in range(1, len(raw)):
        prev = raw[i - 1][1]
        cur_p = raw[i][1]
        if prev > 0 and cur_p > 0:
            out.append((raw[i][0], math.log(cur_p / prev)))
    return out


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get("/style/{cnpj:path}")
async def fund_style_analysis(
    cnpj: str,
    session: AsyncSession = Depends(get_db_session),
    months: int = Query(12, ge=3, le=60, description="Janela em meses"),
    factors: str = Query(
        "BOVA11,SMAL11,IMAB11,GOLD11",
        description="CSV de tickers usados como fatores",
    ),
) -> dict:
    """Style analysis: regressão OLS retornos do fundo vs fatores.

    Revela exposição implícita: ex. um multimercado pode estar 80% IBOV + 20% USD.
    Coeficientes em decimal; pct = peso normalizado dos |betas|.
    """
    from finanalytics_ai.domain.fundos.analytics import style_analysis

    days = months * 31
    fund_returns = await _fetch_fund_log_returns(session, cnpj, days)
    if len(fund_returns) < 30:
        return {"ok": False, "error": "Dados insuficientes do fundo (<30 obs)"}

    # Alinha factor end_date com a última data do fundo (informe diário pode estar desatualizado)
    fund_end = fund_returns[-1][0]
    if hasattr(fund_end, "date"):
        fund_end = fund_end.date()

    factor_list = [t.strip().upper() for t in factors.split(",") if t.strip()]
    factor_returns: dict[str, list] = {}
    for ticker in factor_list:
        s = _fetch_factor_log_returns_sync(ticker, days, end_date=fund_end)
        if len(s) >= 20:
            factor_returns[ticker] = s

    if not factor_returns:
        return {
            "ok": False,
            "error": f"Nenhum fator com >=30 obs em features_daily. Tentados: {factor_list}",
        }

    result = style_analysis(fund_returns, factor_returns)
    if result is None:
        return {
            "ok": False,
            "error": "Overlap insuficiente entre fundo e fatores (<30 datas comuns)",
        }
    result["cnpj"] = cnpj
    return {"ok": True, **result}


@router.get("/peer-ranking")
async def peer_ranking_endpoint(
    session: AsyncSession = Depends(get_db_session),
    tipo: str = Query(
        "Multimercado", description="Classe CVM (Multimercado, Ações, Renda Fixa, FII...)"
    ),
    months: int = Query(6, ge=1, le=36),
    top: int = Query(20, ge=5, le=100),
    min_pl: float = Query(0.0, description="PL mínimo (de pl_atual em fundos_cadastro)"),
    end_date: str | None = Query(
        None, description="Data final (YYYY-MM-DD). Default: max(data_ref) em fundos_informe_diario"
    ),
) -> dict:
    """Top-N fundos da categoria pelo Sharpe rolling.

    Janela = `months` meses anteriores a `end_date` (ou ao último informe diário disponível).
    Filtros: `classe` ILIKE `%tipo%` em fundos_cadastro + min_pl. CNPJs sem dados na
    janela são pulados.
    """
    import math

    from finanalytics_ai.domain.fundos.analytics import peer_ranking

    from datetime import date as _date

    days = months * 31
    if end_date is None:
        end_row = await session.execute(text("SELECT MAX(data_ref) FROM fundos_informe_diario"))
        end_dt = end_row.scalar()
        if end_dt is None:
            return {"ok": False, "error": "fundos_informe_diario vazia — rode sync"}
        if hasattr(end_dt, "date"):
            end_dt = end_dt.date()
    else:
        end_dt = _date.fromisoformat(end_date)
    end_date_str = end_dt.isoformat()

    cand = await session.execute(
        text(
            "SELECT cnpj, denominacao FROM fundos_cadastro "
            "WHERE classe ILIKE :t AND COALESCE(pl_atual, 0) >= :pl "
            "ORDER BY pl_atual DESC NULLS LAST LIMIT 500"
        ),
        {"t": f"%{tipo}%", "pl": min_pl},
    )
    candidates = cand.fetchall()
    if not candidates:
        return {"ok": False, "error": f"Sem fundos com tipo='{tipo}' e PL>={min_pl}"}

    funds_data = []
    for cnpj, denom in candidates:
        rows = await session.execute(
            text(
                "SELECT vl_quota::float FROM fundos_informe_diario "
                "WHERE cnpj=:c AND data_ref BETWEEN :start AND :ed "
                "ORDER BY data_ref ASC"
            ),
            {
                "c": cnpj,
                "ed": end_dt,
                "start": end_dt - __import__("datetime").timedelta(days=days),
            },
        )
        quotas = [float(r[0]) for r in rows.fetchall() if r[0] is not None and r[0] > 0]
        if len(quotas) < 30:
            continue
        returns = [
            math.log(quotas[i] / quotas[i - 1])
            for i in range(1, len(quotas))
            if quotas[i - 1] > 0 and quotas[i] > 0
        ]
        if len(returns) < 30:
            continue
        funds_data.append((cnpj, denom, returns))

    ranking = peer_ranking(funds_data, window_months=months, top_n=top)

    # N10 (28/abr): warning para classes com peculiaridades de cota.
    # FIDC/FIDC-NP: cota low-vol → sharpe inflado (denominador pequeno).
    # FIP/FIP Multi: cota atualizada com baixa frequência → vol/return instáveis.
    warning = None
    if tipo in ("FIDC", "FIDC-NP", "FIC FIDC"):
        warning = (
            "FIDC têm cota de baixa volatilidade (carteira de recebíveis). "
            "Sharpe absoluto pode estar inflado pelo denominador pequeno; "
            "compare entre FIDCs, não entre FIDCs e ações."
        )
    elif tipo in ("FIP", "FIP Multi", "FIC FIP", "FMIEE"):
        warning = (
            "FIP têm cota atualizada com baixa frequência (PE/VC); "
            "métricas diárias podem ser instáveis. Considere janela ≥24m "
            "e prefira analisar evolução qualitativa em vez de sharpe."
        )

    return {
        "ok": True,
        "tipo": tipo,
        "window_months": months,
        "min_pl": min_pl,
        "end_date": end_date_str,
        "total_evaluated": len(funds_data),
        "warning": warning,
        "top": ranking,
    }


@router.get("/anomalies/{cnpj:path}")
async def fund_anomalies(
    cnpj: str,
    session: AsyncSession = Depends(get_db_session),
    months: int = Query(12, ge=3, le=60),
    threshold_sigma: float = Query(3.0, ge=2.0, le=5.0),
    rolling_window: int = Query(30, ge=10, le=120),
) -> dict:
    """Saltos > N σ na cota do fundo (suspeita de marcação errada/evento)."""
    from finanalytics_ai.domain.fundos.analytics import nav_anomalies

    from datetime import timedelta as _td

    days = months * 31
    max_row = await session.execute(
        text("SELECT MAX(data_ref) FROM fundos_informe_diario WHERE cnpj=:c"), {"c": cnpj}
    )
    max_dt = max_row.scalar()
    if max_dt is None:
        return {"ok": False, "error": "Sem informes diários para este CNPJ"}
    if hasattr(max_dt, "date"):
        max_dt = max_dt.date()
    start_dt = max_dt - _td(days=days)
    rows = await session.execute(
        text(
            "SELECT data_ref::date AS dia, vl_quota::float AS vq "
            "FROM fundos_informe_diario WHERE cnpj=:c "
            "AND data_ref BETWEEN :s AND :e "
            "ORDER BY data_ref ASC"
        ),
        {"c": cnpj, "s": start_dt, "e": max_dt},
    )
    series = [(r[0], float(r[1])) for r in rows.fetchall() if r[1] is not None]
    if len(series) < rolling_window + 5:
        return {"ok": False, "error": f"Dados insuficientes (<{rolling_window + 5} obs)"}

    result = nav_anomalies(series, rolling_window=rolling_window, threshold_sigma=threshold_sigma)
    if result is None:
        return {"ok": False, "error": "Cálculo retornou None"}
    result["cnpj"] = cnpj
    return {"ok": True, **result}
