"""
RF Regime API — detecção de regime da curva DI + recomendação de indexador.

GET /api/v1/rf/regime
    Estado atual: NORMAL | STEEPENING | FLATTENING | INVERSION
    + score de intensidade [0,1]
    + slope_2y_10y atual + delta + z-score
    + histórico últimos N dias
    + recomendação textual + alocação sugerida (CDI / Pre / IPCA+)

Sem libs ML pesadas — algoritmo determinístico baseado em slopes da
view rates_features_daily (já populada pelo DI1 worker).
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query
import psycopg2
import structlog

from finanalytics_ai.domain.rf_regime.classifier import analyze_regime

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/rf", tags=["Renda Fixa"])


def _dsn() -> str:
    dsn = (
        os.environ.get("TIMESCALE_URL")
        or os.environ.get("PROFIT_TIMESCALE_DSN")
        or "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
    )
    return dsn.replace("postgresql+asyncpg://", "postgresql://")


@router.get("/regime")
def get_rf_regime(
    history_days: int = Query(90, ge=10, le=365, description="Dias de histórico no retorno"),
    lookback_days: int = Query(
        500, ge=60, le=2000, description="Dias usados para z-score (interno)"
    ),
):
    """Retorna regime atual da curva DI + recomendação de indexador.

    Lê `slope_2y_10y` da view `rates_features_daily` (DI1 worker mantém atualizada).
    Classifica em 4 regimes via regras determinísticas (slope absoluto + delta z-score).
    """
    dsn = _dsn()
    try:
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT dia, slope_2y_10y FROM rates_features_daily "
                "WHERE slope_2y_10y IS NOT NULL "
                "ORDER BY dia DESC LIMIT %s",
                (lookback_days,),
            )
            rows = cur.fetchall()
    except Exception as exc:
        logger.warning("rf_regime.db_error", error=str(exc))
        raise HTTPException(503, f"Erro DB: {exc}")

    if not rows:
        raise HTTPException(404, "rates_features_daily vazia — DI1 worker não populou")

    # Reverte para ASC (analyze_regime espera ordem cronológica)
    rows_asc = list(reversed(rows))

    result = analyze_regime(rows_asc, history_days=history_days)
    if result is None:
        raise HTTPException(
            422, "Dados insuficientes (<30 dias com slope_2y_10y não-nulo)"
        )
    logger.info(
        "rf_regime.computed",
        regime=result["regime"],
        score=result["score"],
        slope=result["slope_2y_10y"],
    )
    return result
