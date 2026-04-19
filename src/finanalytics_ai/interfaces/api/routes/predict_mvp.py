"""
routes/predict_mvp.py — endpoint MVP para serving do modelo Sprint 10.

Endpoint novo, complementar ao /api/v1/ml/forecast existente (que usa
QuantileForecaster + MLService + SqlFeatureRepository sobre fintz_cotacoes).

Propósito do MVP:
  - Carregar o pickle mais recente em models/petr4_mvp_<TICKER>_*.pkl
    (LightGBM Regressor, target r_1d_futuro, treinado por
    scripts/train_petr4_mvp.py).
  - Ler última linha de features_daily do ticker solicitado.
  - Retornar predicted_return (log-return 1d ahead) + metadata do modelo.

Quando usar:
  - Validação rápida do pipeline R10 scaffold.
  - Serving por ticker sem depender da stack QuantileForecaster.

Pós-Sprint 1 completo, este endpoint será substituído pelo fluxo
produção (MLStrategy + RiskEstimator) — ver runbook_R10_modelos.md §6.
"""
from __future__ import annotations

import json
import pickle
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import psycopg2
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from finanalytics_ai.config import get_settings
from finanalytics_ai.observability.logging import get_logger


log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/ml", tags=["ML Probabilistico"])


import os as _os_mod
_default_models = Path(__file__).resolve().parents[5] / "models"
MODELS_DIR = Path(_os_mod.environ.get("FINANALYTICS_MODELS_DIR", str(_default_models)))

FEATURES = [
    "close",
    "r_1d", "r_5d", "r_21d",
    "atr_14", "vol_21d", "vol_rel_20",
    "sma_50", "sma_200", "rsi_14",
]


class PredictResponse(BaseModel):
    model_config = {"protected_namespaces": ()}

    ticker: str
    reference_date: date
    predicted_log_return: float = Field(..., description="log(close[t+1]/close[t])")
    predicted_return_pct: float = Field(..., description="(exp(log_ret) - 1) * 100")
    model_file: str
    model_trained_at: str | None = None
    model_metrics: dict[str, Any] | None = None
    features_used: dict[str, Any]


def _find_latest_pickle(ticker: str) -> tuple[Path, dict] | None:
    """Localiza o pickle mais recente em models/ para o ticker alvo.
    Fallback: qualquer pickle com metadata matching ticker."""
    if not MODELS_DIR.exists():
        return None
    candidates = sorted(MODELS_DIR.glob(f"*_{ticker}_*.pkl"), reverse=True)
    for pkl in candidates:
        meta_path = pkl.with_suffix(".json")
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if meta.get("ticker", "").upper() == ticker.upper():
            return pkl, meta
    return None


def _load_latest_features(ticker: str, dsn: str) -> dict[str, Any] | None:
    cols = ", ".join(FEATURES)
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT dia, {cols} FROM features_daily "
            f"WHERE ticker = %s ORDER BY dia DESC LIMIT 1",
            (ticker.upper(),),
        )
        row = cur.fetchone()
    if not row:
        return None
    d: dict[str, Any] = {"dia": row[0]}
    for i, f in enumerate(FEATURES, 1):
        v = row[i]
        d[f] = float(v) if v is not None else None
    return d


@router.get("/predict_mvp/{ticker}", response_model=PredictResponse)
async def predict_mvp(ticker: str) -> PredictResponse:
    """
    Predição r_1d_futuro (log-return 1 dia à frente) usando o modelo MVP
    Sprint 10 para um ticker específico.

    Retorna 404 se não há pickle para o ticker ou se features_daily não tem
    linha para ele.
    """
    ticker_u = ticker.upper()

    pkl_info = _find_latest_pickle(ticker_u)
    if pkl_info is None:
        raise HTTPException(
            404,
            detail=f"No MVP model found in models/ for {ticker_u}. "
                   f"Train first: python scripts/train_petr4_mvp.py --ticker {ticker_u}",
        )
    pkl_path, meta = pkl_info

    import os as _os
    dsn = (
        _os.environ.get("TIMESCALE_URL")
        or _os.environ.get("PROFIT_TIMESCALE_DSN")
        or "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
    )
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    try:
        feats = _load_latest_features(ticker_u, dsn)
    except Exception as exc:
        log.error("predict_mvp.features_error", ticker=ticker_u, error=str(exc))
        raise HTTPException(500, detail=f"features_daily query failed: {exc}") from exc

    if feats is None:
        raise HTTPException(
            404,
            detail=f"No features_daily row for {ticker_u}. Populate first: "
                   f"python scripts/features_daily_builder.py --only {ticker_u} "
                   f"--start 2020-01-02",
        )

    # Checa se todas as features têm valor
    missing = [f for f in FEATURES if feats.get(f) is None]
    if missing:
        raise HTTPException(
            422,
            detail=f"features_daily latest row has nulls in: {missing}. "
                   f"Ticker may have incomplete history.",
        )

    x_vec = np.array([[feats[f] for f in FEATURES]], dtype=float)

    try:
        with pkl_path.open("rb") as fh:
            model = pickle.load(fh)
        pred_log = float(model.predict(x_vec)[0])
    except Exception as exc:
        log.error("predict_mvp.inference_error", ticker=ticker_u, error=str(exc))
        raise HTTPException(500, detail=f"inference failed: {exc}") from exc

    pred_pct = (float(np.exp(pred_log)) - 1.0) * 100.0

    return PredictResponse(
        ticker=ticker_u,
        reference_date=feats["dia"],
        predicted_log_return=pred_log,
        predicted_return_pct=round(pred_pct, 4),
        model_file=pkl_path.name,
        model_trained_at=meta.get("trained_at_utc"),
        model_metrics=meta.get("metrics"),
        features_used={"dia": str(feats["dia"]), **{f: feats[f] for f in FEATURES}},
    )
