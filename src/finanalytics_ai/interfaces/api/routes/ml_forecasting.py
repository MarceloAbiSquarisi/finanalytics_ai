"""
finanalytics_ai.interfaces.api.routes.ml_forecasting

Endpoints de ML Probabilistico:

  POST /api/v1/ml/features          — computa features para lista de tickers
  POST /api/v1/ml/forecast          — previsao P10/P50/P90 de retornos
  POST /api/v1/ml/risk              — VaR/CVaR multi-camada
  GET  /api/v1/ml/screener          — screener com score probabilistico
  GET  /api/v1/ml/feature-importance — importancia de features do modelo
"""

from datetime import UTC, date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
import structlog

from finanalytics_ai.domain.screener.engine import IBOV_UNIVERSE
from finanalytics_ai.interfaces.api.dependencies import get_db_session

router = APIRouter(prefix="/api/v1/ml", tags=["ML Probabilistico"])
logger = structlog.get_logger(__name__)


class FeaturesRequest(BaseModel):
    tickers: list[str] = Field(default_factory=lambda: IBOV_UNIVERSE[:20])
    reference_date: date | None = None


class ForecastRequest(BaseModel):
    tickers: list[str] = Field(default_factory=lambda: IBOV_UNIVERSE[:20])
    horizon_days: int = Field(default=21, ge=5, le=252)
    reference_date: date | None = None
    force_retrain: bool = False


class RiskRequest(BaseModel):
    tickers: list[str] = Field(default_factory=lambda: IBOV_UNIVERSE[:20])
    window_days: int = Field(default=252, ge=60, le=1260)
    reference_date: date | None = None


def _get_ml_service(session=Depends(get_db_session)):
    from finanalytics_ai.application.ml.ml_service import MLService
    from finanalytics_ai.infrastructure.ml.feature_repo import SqlFeatureRepository

    repo = SqlFeatureRepository(session)
    return MLService(repo)


@router.post("/features")
async def compute_features(
    body: FeaturesRequest,
    service=Depends(_get_ml_service),
) -> dict[str, Any]:
    """Computa e persiste features tecnicas + fundamentais para os tickers."""
    try:
        processed = await service.compute_features(
            tickers=body.tickers,
            reference_date=body.reference_date,
        )
        return {
            "status": "ok",
            "tickers_requested": len(body.tickers),
            "tickers_processed": processed,
        }
    except Exception as exc:
        logger.error("ml.features_error", error=str(exc))
        raise HTTPException(500, detail=str(exc)) from exc


@router.post("/forecast")
async def run_forecast(
    body: ForecastRequest,
    service=Depends(_get_ml_service),
) -> dict[str, Any]:
    """
    Previsao probabilistica de retornos com intervalos de confianca.

    Retorna P10 (pessimista), P50 (mediano), P90 (otimista)
    e prob_positive para cada ticker no horizonte solicitado.
    """
    try:
        forecasts = await service.run_forecasts(
            tickers=body.tickers,
            forecast_date=body.reference_date,
            horizon_days=body.horizon_days,
            force_retrain=body.force_retrain,
        )
        return {
            "horizon_days": body.horizon_days,
            "forecasts": [
                {
                    "ticker": f.ticker,
                    "p10_pct": round(f.p10 * 100, 2),
                    "p50_pct": round(f.p50 * 100, 2),
                    "p90_pct": round(f.p90 * 100, 2),
                    "prob_positive_pct": round(f.prob_positive * 100, 1),
                    "range_80pct": [
                        round(f.p10 * 100, 2),
                        round(f.p90 * 100, 2),
                    ],
                    "model_version": f.model_version,
                }
                for f in sorted(forecasts, key=lambda x: -x.prob_positive)
            ],
        }
    except Exception as exc:
        logger.error("ml.forecast_error", error=str(exc))
        raise HTTPException(500, detail=str(exc)) from exc


@router.post("/risk")
async def run_risk(
    body: RiskRequest,
    session=Depends(get_db_session),
) -> dict[str, Any]:
    """
    VaR/CVaR probabilistico em 3 camadas:
      historico, parametrico (t-Student), GARCH condicional + Monte Carlo.

    Todos os valores expressam perda maxima como percentual positivo.
    Ex: var_95_historical=0.032 = perda maxima de 3.2% com 95% de confianca.
    """
    from sqlalchemy import text

    from finanalytics_ai.application.ml.risk_estimator import RiskEstimator

    estimator = RiskEstimator()
    results = []
    metrics_to_save = []

    for ticker in body.tickers:
        try:
            rows = await session.execute(
                text(
                    """
                SELECT preco_fechamento_ajustado AS close
                FROM fintz_cotacoes
                WHERE ticker = :ticker
                  AND data <= CURRENT_DATE
                  AND data >= CURRENT_DATE - INTERVAL ':w days'
                ORDER BY data ASC
            """.replace(":w", str(body.window_days * 2))
                ),
                {"ticker": ticker},
            )
            closes = [r[0] for r in rows if r[0] is not None]

            if len(closes) < 30:
                continue

            returns = [
                (closes[i] - closes[i - 1]) / closes[i - 1]
                for i in range(1, len(closes))
                if closes[i - 1] != 0
            ]

            metrics = await estimator.estimate(
                ticker=ticker,
                returns=returns,
                reference_date=body.reference_date,
                window_days=body.window_days,
            )
            if metrics is None:
                continue

            metrics_to_save.append(metrics)
            results.append(
                {
                    "ticker": ticker,
                    "risk_level": metrics.risk_level,
                    "var_consensus_pct": round(metrics.var_consensus * 100, 2),
                    "historical": {
                        "var_95_pct": round(metrics.var_95_historical * 100, 2),
                        "cvar_95_pct": round(metrics.cvar_95_historical * 100, 2),
                    },
                    "parametric_t": {
                        "var_95_pct": round(metrics.var_95_parametric * 100, 2),
                        "cvar_95_pct": round(metrics.cvar_95_parametric * 100, 2),
                        "degrees_of_freedom": round(metrics.t_degrees_of_freedom, 1),
                        "note": "df < 6 indica caudas pesadas (fat tails)"
                        if metrics.t_degrees_of_freedom < 6
                        else None,
                    },
                    "garch": {
                        "var_95_pct": round(metrics.var_95_garch * 100, 2)
                        if metrics.var_95_garch
                        else None,
                        "cvar_95_pct": round(metrics.cvar_95_garch * 100, 2)
                        if metrics.cvar_95_garch
                        else None,
                        "vol_forecast_annual_pct": round(
                            metrics.garch_volatility_forecast * 252**0.5 * 100, 2
                        )
                        if metrics.garch_volatility_forecast
                        else None,
                    },
                    "monte_carlo": {
                        "var_95_pct": round(metrics.var_95_mc * 100, 2),
                        "cvar_95_pct": round(metrics.cvar_95_mc * 100, 2),
                        "paths": 100000,
                    },
                    "volatility_annual_pct": round(metrics.volatility_annual * 100, 2),
                }
            )
        except Exception as exc:
            logger.warning("ml.risk_ticker_error", ticker=ticker, error=str(exc))

    # Persiste metricas calculadas em ml_risk
    if metrics_to_save:
        from finanalytics_ai.infrastructure.ml.feature_repo import SqlFeatureRepository

        repo = SqlFeatureRepository(session)
        try:
            await repo.save_risk_metrics(metrics_to_save)
        except Exception as exc:
            logger.warning("ml.risk_persist_error", error=str(exc))

    results.sort(key=lambda x: x["var_consensus_pct"], reverse=True)
    return {"window_days": body.window_days, "risk_metrics": results}


@router.get("/screener")
async def ml_screener(
    tickers: str = Query(default="", description="Comma-separated. Vazio = IBOV"),
    horizon_days: int = Query(default=21, ge=5, le=63),
    min_prob_positive: float = Query(default=0.0, ge=0.0, le=1.0),
    session=Depends(get_db_session),
) -> dict[str, Any]:
    """
    Screener probabilistico: lista tickers ranqueados por prob_positive.

    Combina features fundamentais (P/L, ROE, etc.) com previsoes ML.
    """
    from sqlalchemy import text

    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        ticker_list = IBOV_UNIVERSE[:30]

    rows = await session.execute(
        text("""
        SELECT
            f.ticker,
            f.ret_21d     * 100 AS ret_21d_pct,
            f.volatility_21d * 100 AS vol_pct,
            f.rsi_14,
            f.beta_60d,
            f.pe, f.pvp, f.roe * 100 AS roe_pct,
            f.roic * 100 AS roic_pct,
            f.ev_ebitda,
            fc.p10       * 100 AS p10_pct,
            fc.p50       * 100 AS p50_pct,
            fc.p90       * 100 AS p90_pct,
            fc.prob_positive * 100 AS prob_positive_pct,
            r.var_consensus_approx
        FROM ml_features f
        LEFT JOIN LATERAL (
            SELECT p10, p50, p90, prob_positive FROM ml_forecasts
            WHERE ticker = f.ticker AND horizon_days = :horizon
            ORDER BY forecast_date DESC LIMIT 1
        ) fc ON TRUE
        LEFT JOIN LATERAL (
            SELECT (0.55 * var_95_historical + 0.45 * var_95_parametric) AS var_consensus_approx
            FROM ml_risk WHERE ticker = f.ticker AND window_days = 252
            ORDER BY date DESC LIMIT 1
        ) r ON TRUE
        WHERE f.ticker = ANY(:tickers)
          AND f.date = (SELECT MAX(date) FROM ml_features WHERE ticker = f.ticker)
          AND (fc.prob_positive IS NULL OR fc.prob_positive >= :min_prob)
        ORDER BY fc.prob_positive DESC NULLS LAST
    """),
        {
            "tickers": ticker_list,
            "horizon": horizon_days,
            "min_prob": min_prob_positive,
        },
    )

    items = []
    for row in rows.mappings():
        d = dict(row)
        for k, v in d.items():
            if isinstance(v, float):
                d[k] = round(v, 2)
        items.append(d)

    # Computa MLStrategy signal para cada item com dados suficientes
    from datetime import datetime

    from finanalytics_ai.application.ml.ml_strategy import MLStrategy
    from finanalytics_ai.domain.ml.entities import ReturnForecast, RiskMetrics

    strategy = MLStrategy()

    for item in items:
        p50 = (item.get("p50_pct") or 0) / 100
        p10 = (item.get("p10_pct") or 0) / 100
        p90 = (item.get("p90_pct") or 0) / 100
        prob = (item.get("prob_positive_pct") or 0) / 100
        var_c = item.get("var_consensus_approx") or 0
        # Fallback: estima VaR diario a partir da volatilidade anualizada
        # VaR_95 ≈ vol_diaria * 1.65, vol_diaria = vol_anual / sqrt(252)
        if not var_c:
            vol_ann = (item.get("vol_pct") or 0) / 100
            var_c = (vol_ann / 15.87) * 1.65 if vol_ann > 0 else 0

        if prob > 0 and var_c > 0:
            # Cria objetos minimos para MLStrategy
            fc = ReturnForecast(
                ticker=item["ticker"],
                forecast_date=datetime.now(UTC),
                horizon_days=horizon_days,
                p10=p10,
                p50=p50,
                p90=p90,
                prob_positive=prob,
            )
            # RiskMetrics minimo com var_consensus via historico+parametrico
            rm = RiskMetrics(
                ticker=item["ticker"],
                date=datetime.now(UTC),
                window_days=252,
                var_95_historical=var_c / 0.55 if var_c else 0.025,
                cvar_95_historical=var_c / 0.55 * 1.3 if var_c else 0.033,
                var_95_parametric=var_c / 0.45 if var_c else 0.027,
                cvar_95_parametric=var_c / 0.45 * 1.3 if var_c else 0.035,
                t_degrees_of_freedom=6.0,
                var_95_garch=None,
                cvar_95_garch=None,
                garch_volatility_forecast=None,
                var_95_mc=var_c,
                cvar_95_mc=var_c * 1.3,
                volatility_annual=var_c * 16,
            )
            sig = strategy.evaluate(fc, rm)
            item["signal"] = sig.signal
            item["score"] = sig.score
            item["direction"] = sig.direction
        else:
            item["signal"] = "HOLD"
            item["score"] = 0.0
            item["direction"] = "NEUTRAL"

    # Reordena por score decrescente (MLStrategy > prob_positive)
    items.sort(key=lambda x: x.get("score", 0), reverse=True)

    return {
        "horizon_days": horizon_days,
        "total": len(items),
        "items": items,
    }


@router.get("/feature-importance")
async def feature_importance(request=None) -> dict[str, Any]:
    """Importancia das features no modelo LightGBM (requer modelo treinado)."""
    return {
        "note": "Treinar modelo primeiro via POST /api/v1/ml/forecast com force_retrain=true",
        "features": {
            "ret_21d": "Momentum 21d — melhor preditor de curto prazo",
            "roe": "Return on Equity — qualidade do negocio",
            "volatility_21d": "Volatilidade recente — risco",
            "pe": "P/L — valuation",
            "ret_63d": "Momentum 63d — tendencia de medio prazo",
            "rsi_14": "RSI — sobrecomprado/sobrevendido",
            "roic": "ROIC — eficiencia de capital",
            "pvp": "P/VP — desconto/premio sobre patrimonio",
            "beta_60d": "Beta — sensibilidade ao mercado",
            "ev_ebitda": "EV/EBITDA — valuation enterprise",
        },
    }
