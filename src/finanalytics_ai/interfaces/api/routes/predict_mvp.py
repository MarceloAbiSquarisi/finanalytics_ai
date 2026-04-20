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
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from finanalytics_ai.config import get_settings
from finanalytics_ai.observability.logging import get_logger


log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/ml", tags=["ML Probabilistico"])


import os as _os_mod
_default_models = Path(__file__).resolve().parents[5] / "models"
MODELS_DIR = Path(_os_mod.environ.get("FINANALYTICS_MODELS_DIR", str(_default_models)))

FEATURES_DEFAULT = [
    "close",
    "r_1d", "r_5d", "r_21d",
    "atr_14", "vol_21d", "vol_rel_20",
    "sma_50", "sma_200", "rsi_14",
]

# Features RF adicionais (MVP v2 cross-asset). Carregadas via JOIN em
# features_daily_full quando o pickle declarar essas colunas.
RF_FEATURES_AVAILABLE = {
    "slope_1y_5y", "slope_2y_10y", "curvatura_butterfly",
    "tsmom_di1_1y_3m", "tsmom_di1_2y_3m", "tsmom_di1_5y_3m",
    "tsmom_di1_1y_12m", "tsmom_di1_2y_12m", "tsmom_di1_5y_12m",
    "carry_roll_di1_2y", "carry_roll_di1_5y",
    "value_di1_1y_z", "value_di1_2y_z", "value_di1_5y_z",
    "value_ntnb_2y_z", "value_ntnb_5y_z",
    "breakeven_1y", "breakeven_2y", "breakeven_5y",
    "ns_level", "ns_slope", "ns_curvature", "ns_lambda",
    "vm_combo_1y", "vm_combo_2y", "vm_combo_5y",
    "fra_1y2y", "fra_2y5y",
}


class CalibrationInfo(BaseModel):
    th_buy: float
    th_sell: float
    horizon_days: int
    best_sharpe: float | None = None
    best_return_pct: float | None = None
    best_trades: int | None = None
    best_win_rate: float | None = None
    calibrated_at: str | None = None


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
    signal: str | None = Field(
        None, description="BUY | SELL | HOLD | None (se sem calibração)"
    )
    signal_method: str | None = Field(
        None,
        description=(
            "scaled_linear_1d: th_buy/th_sell calibrados em horizon_days "
            "(ex 21d) divididos por horizon_days para comparação com "
            "prediction 1d — aproximação de primeira ordem"
        ),
    )
    calibration: CalibrationInfo | None = None


def _find_latest_pickle(ticker: str, prefer_horizon: int | None = None) -> tuple[Path, dict] | None:
    """Localiza o pickle mais recente em models/ para o ticker alvo.

    Se prefer_horizon for dado, escolhe primeiro pickles com horizon_days
    igual. Fallback: ultimo pickle disponivel (mais recente)."""
    if not MODELS_DIR.exists():
        return None
    candidates = sorted(MODELS_DIR.glob(f"*_{ticker}_*.pkl"), reverse=True)
    parsed: list[tuple[Path, dict]] = []
    for pkl in candidates:
        meta_path = pkl.with_suffix(".json")
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if meta.get("ticker", "").upper() != ticker.upper():
            continue
        parsed.append((pkl, meta))

    if not parsed:
        return None
    if prefer_horizon is not None:
        for pkl, meta in parsed:
            if int(meta.get("horizon_days", 1)) == prefer_horizon:
                return pkl, meta
    return parsed[0]


def _load_calibration(ticker: str, dsn: str) -> dict[str, Any] | None:
    """Busca calibração em ticker_ml_config."""
    try:
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT th_buy, th_sell, horizon_days,
                       best_sharpe, best_return_pct, best_trades, best_win_rate,
                       calibrated_at
                  FROM ticker_ml_config WHERE ticker=%s
                """,
                (ticker.upper(),),
            )
            row = cur.fetchone()
    except Exception as exc:
        log.warning("predict_mvp.calibration_query_failed", ticker=ticker, error=str(exc))
        return None
    if not row:
        return None
    return {
        "th_buy":         float(row[0]),
        "th_sell":        float(row[1]),
        "horizon_days":   int(row[2]),
        "best_sharpe":    float(row[3]) if row[3] is not None else None,
        "best_return_pct": float(row[4]) if row[4] is not None else None,
        "best_trades":    int(row[5]) if row[5] is not None else None,
        "best_win_rate":  float(row[6]) if row[6] is not None else None,
        "calibrated_at":  row[7].isoformat() if row[7] is not None else None,
    }


def _signal_from_prediction(
    pred_log: float, cfg: dict[str, Any], model_horizon: int | None = None,
) -> tuple[str, str]:
    """Deriva BUY/SELL/HOLD comparando prediction com thresholds calibrados.

    Se model_horizon == cfg.horizon_days (ex ambos 21d): comparacao direta.
    Caso contrario: aproximacao linear (divide thresholds por horizon).
    """
    cfg_h = max(int(cfg["horizon_days"]), 1)
    if model_horizon is not None and int(model_horizon) == cfg_h:
        th_buy = float(cfg["th_buy"])
        th_sell = float(cfg["th_sell"])
        method = "direct_match_horizon"
    else:
        th_buy  = float(cfg["th_buy"])  / cfg_h
        th_sell = float(cfg["th_sell"]) / cfg_h
        method = "scaled_linear_1d"

    if pred_log >= th_buy:
        sig = "BUY"
    elif pred_log <= th_sell:
        sig = "SELL"
    else:
        sig = "HOLD"
    return sig, method


def _load_latest_features(ticker: str, dsn: str, features: list[str]) -> dict[str, Any] | None:
    """Lê última linha de features_daily_full (JOIN técnicas + RF)."""
    cols = ", ".join(features)
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT dia, {cols} FROM features_daily_full "
            f"WHERE ticker = %s ORDER BY dia DESC LIMIT 1",
            (ticker.upper(),),
        )
        row = cur.fetchone()
    if not row:
        return None
    d: dict[str, Any] = {"dia": row[0]}
    for i, f in enumerate(features, 1):
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

    import os as _os
    dsn_early = (
        _os.environ.get("TIMESCALE_URL")
        or _os.environ.get("PROFIT_TIMESCALE_DSN")
        or "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
    ).replace("postgresql+asyncpg://", "postgresql://")
    cfg_early = _load_calibration(ticker_u, dsn_early)
    prefer_h = cfg_early["horizon_days"] if cfg_early else None

    pkl_info = _find_latest_pickle(ticker_u, prefer_horizon=prefer_h)
    if pkl_info is None:
        raise HTTPException(
            404,
            detail=f"No MVP model found in models/ for {ticker_u}. "
                   f"Train first: python scripts/train_petr4_mvp.py --ticker {ticker_u}",
        )
    pkl_path, meta = pkl_info
    features = list(meta.get("features") or FEATURES_DEFAULT)
    model_horizon = int(meta.get("horizon_days", 1))
    dsn = dsn_early
    try:
        feats = _load_latest_features(ticker_u, dsn, features)
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
    missing = [f for f in features if feats.get(f) is None]
    if missing:
        raise HTTPException(
            422,
            detail=f"features_daily latest row has nulls in: {missing}. "
                   f"Ticker may have incomplete history.",
        )

    x_vec = np.array([[feats[f] for f in features]], dtype=float)

    try:
        with pkl_path.open("rb") as fh:
            model = pickle.load(fh)
        pred_log = float(model.predict(x_vec)[0])
    except Exception as exc:
        log.error("predict_mvp.inference_error", ticker=ticker_u, error=str(exc))
        raise HTTPException(500, detail=f"inference failed: {exc}") from exc

    pred_pct = (float(np.exp(pred_log)) - 1.0) * 100.0

    cfg = cfg_early
    signal: str | None = None
    signal_method: str | None = None
    calibration: CalibrationInfo | None = None
    if cfg is not None:
        signal, signal_method = _signal_from_prediction(pred_log, cfg, model_horizon=model_horizon)
        calibration = CalibrationInfo(
            th_buy=cfg["th_buy"],
            th_sell=cfg["th_sell"],
            horizon_days=cfg["horizon_days"],
            best_sharpe=cfg["best_sharpe"],
            best_return_pct=cfg["best_return_pct"],
            best_trades=cfg["best_trades"],
            best_win_rate=cfg["best_win_rate"],
            calibrated_at=cfg["calibrated_at"],
        )

    return PredictResponse(
        ticker=ticker_u,
        reference_date=feats["dia"],
        predicted_log_return=pred_log,
        predicted_return_pct=round(pred_pct, 4),
        model_file=pkl_path.name,
        model_trained_at=meta.get("trained_at_utc"),
        model_metrics=meta.get("metrics"),
        features_used={"dia": str(feats["dia"]), **{f: feats[f] for f in features}},
        signal=signal,
        signal_method=signal_method,
        calibration=calibration,
    )


# ─── /signals batch ────────────────────────────────────────────────────────

class SignalItem(BaseModel):
    ticker: str
    signal: str | None = None
    predicted_log_return: float | None = None
    predicted_return_pct: float | None = None
    reference_date: date | None = None
    th_buy: float | None = None
    th_sell: float | None = None
    horizon_days: int | None = None
    best_sharpe: float | None = None
    error: str | None = None


class SignalsResponse(BaseModel):
    count: int
    buy: int
    sell: int
    hold: int
    errors: int
    items: list[SignalItem]


def _load_all_calibrations(dsn: str, min_sharpe: float | None) -> list[dict[str, Any]]:
    sql = (
        "SELECT ticker, th_buy, th_sell, horizon_days, best_sharpe "
        "FROM ticker_ml_config"
    )
    params: tuple = ()
    if min_sharpe is not None:
        sql += " WHERE best_sharpe >= %s"
        params = (min_sharpe,)
    sql += " ORDER BY best_sharpe DESC NULLS LAST"
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        {"ticker": r[0], "th_buy": float(r[1]), "th_sell": float(r[2]),
         "horizon_days": int(r[3]),
         "best_sharpe": float(r[4]) if r[4] is not None else None}
        for r in rows
    ]


@router.get("/signals", response_model=SignalsResponse)
async def signals_batch(
    min_sharpe: float | None = Query(None, description="Filtra por best_sharpe >= N"),
    limit: int = Query(200, ge=1, le=500),
) -> SignalsResponse:
    """Retorna signals em batch para todos os tickers calibrados com
    pickle disponivel. Tickers sem pickle retornam error='no_model'."""
    import os as _os
    dsn = (
        _os.environ.get("TIMESCALE_URL")
        or _os.environ.get("PROFIT_TIMESCALE_DSN")
        or "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
    )
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")

    configs = _load_all_calibrations(dsn, min_sharpe)[:limit]
    model_cache: dict[str, tuple[Any, dict, Path]] = {}
    items: list[SignalItem] = []
    counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
    errors = 0

    for cfg in configs:
        t = cfg["ticker"]
        base = SignalItem(
            ticker=t, th_buy=cfg["th_buy"], th_sell=cfg["th_sell"],
            horizon_days=cfg["horizon_days"], best_sharpe=cfg["best_sharpe"],
        )
        pkl_info = _find_latest_pickle(t, prefer_horizon=cfg["horizon_days"])
        if pkl_info is None:
            base.error = "no_model"
            items.append(base); errors += 1; continue
        pkl_path, meta = pkl_info
        features = list(meta.get("features") or FEATURES_DEFAULT)
        model_horizon = int(meta.get("horizon_days", 1))

        if t not in model_cache:
            try:
                with pkl_path.open("rb") as fh:
                    model_cache[t] = (pickle.load(fh), meta, pkl_path)
            except Exception as exc:
                base.error = f"load_fail:{type(exc).__name__}"
                items.append(base); errors += 1; continue
        model, _meta, _pkl_path = model_cache[t]

        try:
            feats = _load_latest_features(t, dsn, features)
        except Exception as exc:
            base.error = f"features_fail:{type(exc).__name__}"
            items.append(base); errors += 1; continue
        if feats is None:
            base.error = "no_features"
            items.append(base); errors += 1; continue

        missing = [f for f in features if feats.get(f) is None]
        if missing:
            base.error = f"feature_nulls:{len(missing)}"
            items.append(base); errors += 1; continue

        x_vec = np.array([[feats[f] for f in features]], dtype=float)
        try:
            pred_log = float(model.predict(x_vec)[0])
        except Exception as exc:
            base.error = f"inference_fail:{type(exc).__name__}"
            items.append(base); errors += 1; continue

        sig, _method = _signal_from_prediction(pred_log, cfg, model_horizon=model_horizon)
        base.signal = sig
        base.predicted_log_return = pred_log
        base.predicted_return_pct = round((float(np.exp(pred_log)) - 1.0) * 100.0, 4)
        base.reference_date = feats["dia"]
        items.append(base)
        counts[sig] += 1

    return SignalsResponse(
        count=len(items),
        buy=counts["BUY"], sell=counts["SELL"], hold=counts["HOLD"],
        errors=errors, items=items,
    )
