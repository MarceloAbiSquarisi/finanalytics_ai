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

from datetime import UTC, date
import json
from pathlib import Path
import pickle
from typing import Any

from fastapi import APIRouter, HTTPException, Query
import numpy as np
import psycopg2
from pydantic import BaseModel, Field

from finanalytics_ai.observability.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/ml", tags=["ML Probabilistico"])


import os as _os_mod

_default_models = Path(__file__).resolve().parents[5] / "models"
MODELS_DIR = Path(_os_mod.environ.get("FINANALYTICS_MODELS_DIR", str(_default_models)))

FEATURES_DEFAULT = [
    "close",
    "r_1d",
    "r_5d",
    "r_21d",
    "atr_14",
    "vol_21d",
    "vol_rel_20",
    "sma_50",
    "sma_200",
    "rsi_14",
]

# Features RF adicionais (MVP v2 cross-asset). Carregadas via JOIN em
# features_daily_full quando o pickle declarar essas colunas.
RF_FEATURES_AVAILABLE = {
    "slope_1y_5y",
    "slope_2y_10y",
    "curvatura_butterfly",
    "tsmom_di1_1y_3m",
    "tsmom_di1_2y_3m",
    "tsmom_di1_5y_3m",
    "tsmom_di1_1y_12m",
    "tsmom_di1_2y_12m",
    "tsmom_di1_5y_12m",
    "carry_roll_di1_2y",
    "carry_roll_di1_5y",
    "value_di1_1y_z",
    "value_di1_2y_z",
    "value_di1_5y_z",
    "value_ntnb_2y_z",
    "value_ntnb_5y_z",
    "breakeven_1y",
    "breakeven_2y",
    "breakeven_5y",
    "ns_level",
    "ns_slope",
    "ns_curvature",
    "ns_lambda",
    "vm_combo_1y",
    "vm_combo_2y",
    "vm_combo_5y",
    "fra_1y2y",
    "fra_2y5y",
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
    signal: str | None = Field(None, description="BUY | SELL | HOLD | None (se sem calibração)")
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
        "th_buy": float(row[0]),
        "th_sell": float(row[1]),
        "horizon_days": int(row[2]),
        "best_sharpe": float(row[3]) if row[3] is not None else None,
        "best_return_pct": float(row[4]) if row[4] is not None else None,
        "best_trades": int(row[5]) if row[5] is not None else None,
        "best_win_rate": float(row[6]) if row[6] is not None else None,
        "calibrated_at": row[7].isoformat() if row[7] is not None else None,
    }


def _signal_from_prediction(
    pred_log: float,
    cfg: dict[str, Any],
    model_horizon: int | None = None,
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
        th_buy = float(cfg["th_buy"]) / cfg_h
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
    asset_class: str | None = None
    signal: str | None = None
    predicted_log_return: float | None = None
    predicted_return_pct: float | None = None
    reference_date: date | None = None
    th_buy: float | None = None
    th_sell: float | None = None
    horizon_days: int | None = None
    best_sharpe: float | None = None
    # N5b (28/abr): fundamentals FII (dy_ttm em %, p_vp adimensional). Vem
    # do snapshot mais recente em fii_fundamentals para asset_class='fii'.
    # null para acoes/ETFs (nao aplica).
    dy_ttm: float | None = None
    p_vp: float | None = None
    error: str | None = None


class SignalsResponse(BaseModel):
    count: int
    buy: int
    sell: int
    hold: int
    errors: int
    items: list[SignalItem]


def _load_all_calibrations(
    dsn: str,
    min_sharpe: float | None,
    asset_class: str | None = None,
) -> list[dict[str, Any]]:
    sql = "SELECT ticker, th_buy, th_sell, horizon_days, best_sharpe, asset_class FROM ticker_ml_config"
    where: list[str] = []
    params: list[Any] = []
    if min_sharpe is not None:
        where.append("best_sharpe >= %s")
        params.append(min_sharpe)
    if asset_class:
        where.append("asset_class = %s")
        params.append(asset_class)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY best_sharpe DESC NULLS LAST"
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
    return [
        {
            "ticker": r[0],
            "th_buy": float(r[1]),
            "th_sell": float(r[2]),
            "horizon_days": int(r[3]),
            "best_sharpe": float(r[4]) if r[4] is not None else None,
            "asset_class": r[5] if len(r) > 5 else "acao",
        }
        for r in rows
    ]


@router.get("/signals", response_model=SignalsResponse)
async def signals_batch(
    min_sharpe: float | None = Query(None, description="Filtra por best_sharpe >= N"),
    asset_class: str | None = Query(
        None, description="Filtra por classe (acao | fii). Default: todas"
    ),
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

    configs = _load_all_calibrations(dsn, min_sharpe, asset_class=asset_class)[:limit]

    # N5b (28/abr): bulk fetch de fundamentals para os FIIs do batch.
    # 1 query no inicio, lookup O(1) depois. DISTINCT ON pega o snapshot
    # mais recente por ticker.
    fii_tickers = [c["ticker"] for c in configs if c.get("asset_class") == "fii"]
    fundamentals_map: dict[str, dict[str, float | None]] = {}
    if fii_tickers:
        try:
            with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (ticker)
                        ticker, dy_ttm, p_vp
                    FROM fii_fundamentals
                    WHERE ticker = ANY(%s)
                    ORDER BY ticker, snapshot_date DESC
                    """,
                    (fii_tickers,),
                )
                for row in cur.fetchall():
                    fundamentals_map[row[0]] = {
                        "dy_ttm": float(row[1]) if row[1] is not None else None,
                        "p_vp": float(row[2]) if row[2] is not None else None,
                    }
        except Exception:
            # tabela ausente / DB transient: segue sem fundamentals
            fundamentals_map = {}

    model_cache: dict[str, tuple[Any, dict, Path]] = {}
    items: list[SignalItem] = []
    counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
    errors = 0

    for cfg in configs:
        t = cfg["ticker"]
        fund = fundamentals_map.get(t, {})
        base = SignalItem(
            ticker=t,
            asset_class=cfg.get("asset_class", "acao"),
            th_buy=cfg["th_buy"],
            th_sell=cfg["th_sell"],
            horizon_days=cfg["horizon_days"],
            best_sharpe=cfg["best_sharpe"],
            dy_ttm=fund.get("dy_ttm"),
            p_vp=fund.get("p_vp"),
        )
        pkl_info = _find_latest_pickle(t, prefer_horizon=cfg["horizon_days"])
        if pkl_info is None:
            base.error = "no_model"
            items.append(base)
            errors += 1
            continue
        pkl_path, meta = pkl_info
        features = list(meta.get("features") or FEATURES_DEFAULT)
        model_horizon = int(meta.get("horizon_days", 1))

        if t not in model_cache:
            try:
                with pkl_path.open("rb") as fh:
                    model_cache[t] = (pickle.load(fh), meta, pkl_path)
            except Exception as exc:
                base.error = f"load_fail:{type(exc).__name__}"
                items.append(base)
                errors += 1
                continue
        model, _meta, _pkl_path = model_cache[t]

        try:
            feats = _load_latest_features(t, dsn, features)
        except Exception as exc:
            base.error = f"features_fail:{type(exc).__name__}"
            items.append(base)
            errors += 1
            continue
        if feats is None:
            base.error = "no_features"
            items.append(base)
            errors += 1
            continue

        missing = [f for f in features if feats.get(f) is None]
        if missing:
            base.error = f"feature_nulls:{len(missing)}"
            items.append(base)
            errors += 1
            continue

        x_vec = np.array([[feats[f] for f in features]], dtype=float)
        try:
            pred_log = float(model.predict(x_vec)[0])
        except Exception as exc:
            base.error = f"inference_fail:{type(exc).__name__}"
            items.append(base)
            errors += 1
            continue

        sig, _method = _signal_from_prediction(pred_log, cfg, model_horizon=model_horizon)
        base.signal = sig
        base.predicted_log_return = pred_log
        base.predicted_return_pct = round((float(np.exp(pred_log)) - 1.0) * 100.0, 4)
        base.reference_date = feats["dia"]
        items.append(base)
        counts[sig] += 1

    return SignalsResponse(
        count=len(items),
        buy=counts["BUY"],
        sell=counts["SELL"],
        hold=counts["HOLD"],
        errors=errors,
        items=items,
    )


# ─── /signal_history ──────────────────────────────────────────────────────


class HistoryItem(BaseModel):
    snapshot_date: date
    ticker: str
    signal: str
    predicted_log_return: float | None = None
    predicted_return_pct: float | None = None
    th_buy: float | None = None
    th_sell: float | None = None
    horizon_days: int | None = None
    best_sharpe: float | None = None
    signal_method: str | None = None


class ChangeItem(BaseModel):
    ticker: str
    snapshot_date: date
    prev_signal: str | None = None
    curr_signal: str
    prev_date: date | None = None
    best_sharpe: float | None = None


@router.get("/signal_history", response_model=list[HistoryItem])
async def signal_history(
    ticker: str | None = Query(None, description="Filtra por ticker"),
    since: date | None = Query(None, description="snapshot_date >= since"),
    limit: int = Query(500, ge=1, le=5000),
):
    """Retorna snapshots historicos de signals (ordenado snapshot_date DESC)."""
    import os as _os

    dsn = (
        _os.environ.get("TIMESCALE_URL")
        or _os.environ.get("PROFIT_TIMESCALE_DSN")
        or "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
    ).replace("postgresql+asyncpg://", "postgresql://")

    sql = (
        "SELECT snapshot_date, ticker, signal, predicted_log_return, "
        "predicted_return_pct, th_buy, th_sell, horizon_days, "
        "best_sharpe, signal_method FROM signal_history WHERE 1=1"
    )
    params: list = []
    if ticker:
        sql += " AND ticker=%s"
        params.append(ticker.upper())
    if since:
        sql += " AND snapshot_date >= %s"
        params.append(since)
    sql += " ORDER BY snapshot_date DESC, ticker LIMIT %s"
    params.append(limit)

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
    return [
        HistoryItem(
            snapshot_date=r[0],
            ticker=r[1],
            signal=r[2],
            predicted_log_return=float(r[3]) if r[3] is not None else None,
            predicted_return_pct=float(r[4]) if r[4] is not None else None,
            th_buy=float(r[5]) if r[5] is not None else None,
            th_sell=float(r[6]) if r[6] is not None else None,
            horizon_days=r[7],
            best_sharpe=float(r[8]) if r[8] is not None else None,
            signal_method=r[9],
        )
        for r in rows
    ]


@router.get("/signal_history/changes", response_model=list[ChangeItem])
async def signal_history_changes(
    snapshot_date: date | None = Query(None, description="default: snapshot mais recente"),
    limit: int = Query(100, ge=1, le=500),
):
    """Retorna tickers que mudaram de signal vs snapshot anterior."""
    import os as _os

    dsn = (
        _os.environ.get("TIMESCALE_URL")
        or _os.environ.get("PROFIT_TIMESCALE_DSN")
        or "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
    ).replace("postgresql+asyncpg://", "postgresql://")

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        if snapshot_date is None:
            cur.execute("SELECT max(snapshot_date) FROM signal_history")
            r = cur.fetchone()
            if not r or not r[0]:
                return []
            snapshot_date = r[0]

        cur.execute(
            """
            WITH prev AS (
              SELECT DISTINCT ON (ticker) ticker, signal AS prev_signal,
                     snapshot_date AS prev_date
                FROM signal_history WHERE snapshot_date < %s
                ORDER BY ticker, snapshot_date DESC
            ),
            curr AS (
              SELECT ticker, signal AS curr_signal, best_sharpe
                FROM signal_history WHERE snapshot_date = %s
            )
            SELECT c.ticker, p.prev_signal, c.curr_signal, p.prev_date, c.best_sharpe
              FROM curr c LEFT JOIN prev p ON p.ticker = c.ticker
             WHERE p.prev_signal IS DISTINCT FROM c.curr_signal
             ORDER BY c.best_sharpe DESC NULLS LAST LIMIT %s
            """,
            (snapshot_date, snapshot_date, limit),
        )
        rows = cur.fetchall()
    return [
        ChangeItem(
            ticker=r[0],
            snapshot_date=snapshot_date,
            prev_signal=r[1],
            curr_signal=r[2],
            prev_date=r[3],
            best_sharpe=float(r[4]) if r[4] is not None else None,
        )
        for r in rows
    ]


# ─── /metrics — saude do pipeline ML (Sprint V2, 21/abr) ──────────────────


class MLMetrics(BaseModel):
    config_count: int = Field(..., description="Linhas em ticker_ml_config")
    pickle_count: int = Field(..., description="Pickles MVP h21 disponiveis em models/")
    drift_count: int = Field(..., description="Tickers calibrados sem pickle (config - pickle)")
    drift_tickers: list[str] = Field(default_factory=list, description="Ate 10 tickers em drift")
    last_calibration_at: str | None = Field(
        None, description="ticker_ml_config.MAX(updated_at) ISO"
    )
    last_snapshot_at: str | None = Field(None, description="signal_history.MAX(snapshot_date) ISO")
    snapshot_age_days: int | None = Field(None, description="Dias desde o ultimo snapshot")
    latest_pickle_age_days: int | None = Field(
        None, description="Idade do pickle mais recente em models/"
    )
    signals_24h: dict[str, int] = Field(
        default_factory=dict, description="Contagem BUY/SELL/HOLD nas ultimas 24h em signal_history"
    )


@router.get("/metrics", response_model=MLMetrics)
async def ml_metrics() -> MLMetrics:
    """Saude do pipeline ML — drift de modelos, freshness de calibracao
    e snapshot, distribuicao de signals recentes.

    Util para Grafana (ml_calibration_age_days alertable),
    smoke test pos-deploy, e detectar regressoes silenciosas.
    """
    from datetime import datetime as _dt
    import os as _os

    dsn = (
        _os.environ.get("TIMESCALE_URL")
        or _os.environ.get("PROFIT_TIMESCALE_DSN")
        or "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
    ).replace("postgresql+asyncpg://", "postgresql://")

    # ── Pickles em disco ──
    pickle_files = sorted(MODELS_DIR.glob("petr4_mvp_*.pkl"))
    pickle_tickers = set()
    latest_mtime = None
    for p in pickle_files:
        # Nome: petr4_mvp_<TICKER>_<HORIZON>_<TIMESTAMP>.pkl
        parts = p.stem.split("_")
        if len(parts) >= 3:
            pickle_tickers.add(parts[2].upper())
        try:
            mt = p.stat().st_mtime
            if latest_mtime is None or mt > latest_mtime:
                latest_mtime = mt
        except OSError:
            continue
    pickle_count = len(pickle_tickers)
    latest_pickle_age_days = None
    if latest_mtime is not None:
        latest_pickle_age_days = max(0, int((_dt.now().timestamp() - latest_mtime) // 86400))

    # ── Config + signal history em DB ──
    try:
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT ticker FROM ticker_ml_config")
            config_tickers = {r[0].upper() for r in cur.fetchall()}

            cur.execute("SELECT MAX(calibrated_at) FROM ticker_ml_config")
            last_calib = cur.fetchone()[0]

            cur.execute("SELECT MAX(snapshot_date) FROM signal_history")
            last_snap = cur.fetchone()[0]

            # Distribuicao de signals do snapshot mais recente
            cur.execute(
                "SELECT signal, COUNT(*) FROM signal_history "
                "WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM signal_history) "
                "GROUP BY signal"
            )
            signals_24h = {r[0]: int(r[1]) for r in cur.fetchall()}
    except Exception as exc:
        log.warning("ml_metrics.db_error", error=str(exc))
        config_tickers = set()
        last_calib = None
        last_snap = None
        signals_24h = {}

    drift = sorted(config_tickers - pickle_tickers)
    snapshot_age = None
    if last_snap is not None:
        try:
            snapshot_age = (_dt.now(UTC).date() - last_snap).days
        except Exception:
            snapshot_age = None

    return MLMetrics(
        config_count=len(config_tickers),
        pickle_count=pickle_count,
        drift_count=len(drift),
        drift_tickers=drift[:10],
        last_calibration_at=last_calib.isoformat() if last_calib else None,
        last_snapshot_at=last_snap.isoformat() if last_snap else None,
        snapshot_age_days=snapshot_age,
        latest_pickle_age_days=latest_pickle_age_days,
        signals_24h=signals_24h,
    )


# ─── /predict_ensemble — multi-horizon ensemble (Sprint Z4, 21/abr) ──────


class EnsembleHorizonItem(BaseModel):
    horizon_days: int
    model_file: str
    predicted_log_return: float
    predicted_return_pct: float
    weight: float = Field(..., description="Peso usado no ensemble (sharpe-based ou uniform)")
    sharpe: float | None = None


class EnsembleResponse(BaseModel):
    ticker: str
    reference_date: date
    ensemble_log_return: float
    ensemble_return_pct: float
    weighting: str = Field(
        ..., description="'sharpe' se todos models tem sharpe; 'uniform' caso contrario"
    )
    horizons: list[EnsembleHorizonItem]
    signal: str | None = None
    signal_method: str | None = None
    calibration: CalibrationInfo | None = None


def _find_all_pickles(ticker: str) -> list[tuple[Path, dict]]:
    """Lista TODOS pickles do ticker (qualquer horizon), com meta valida."""
    if not MODELS_DIR.exists():
        return []
    out: list[tuple[Path, dict]] = []
    for pkl in sorted(MODELS_DIR.glob(f"*_{ticker}_*.pkl"), reverse=True):
        meta_path = pkl.with_suffix(".json")
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if meta.get("ticker", "").upper() != ticker.upper():
            continue
        out.append((pkl, meta))
    # Dedup por horizon — mantem o mais recente por horizon
    seen_horizon: set[int] = set()
    deduped: list[tuple[Path, dict]] = []
    for pkl, meta in out:
        h = int(meta.get("horizon_days", 1))
        if h in seen_horizon:
            continue
        seen_horizon.add(h)
        deduped.append((pkl, meta))
    return sorted(deduped, key=lambda x: int(x[1].get("horizon_days", 1)))


@router.get("/predict_ensemble/{ticker}", response_model=EnsembleResponse)
async def predict_ensemble(ticker: str) -> EnsembleResponse:
    """Ensemble multi-horizon (1d/3d/5d/21d/...) — averagem ponderada das
    predicoes de TODOS os pickles disponiveis para o ticker.

    Pesos: se TODOS os pickles tem `meta.metrics.test_sharpe`, normaliza
    para soma=1; caso contrario uniforme. Predicoes nao-1d sao
    annualizadas linearmente (`pred_log / horizon_days`) antes de ponderar.

    Signal usa thresholds de `ticker_ml_config` aplicado sobre o ensemble.

    Retorna 404 se nao ha pickle para o ticker.
    """
    ticker_u = ticker.upper()

    import os as _os

    dsn = (
        _os.environ.get("TIMESCALE_URL")
        or _os.environ.get("PROFIT_TIMESCALE_DSN")
        or "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
    ).replace("postgresql+asyncpg://", "postgresql://")

    pickles = _find_all_pickles(ticker_u)
    if not pickles:
        raise HTTPException(
            404,
            detail=f"No MVP models found for {ticker_u}. Train at least one: "
            f"python scripts/train_petr4_mvp.py --ticker {ticker_u}",
        )

    # Carrega features (assume primeiro pickle define schema)
    first_meta = pickles[0][1]
    features = list(first_meta.get("features") or FEATURES_DEFAULT)
    try:
        feats = _load_latest_features(ticker_u, dsn, features)
    except Exception as exc:
        log.error("predict_ensemble.features_error", ticker=ticker_u, error=str(exc))
        raise HTTPException(500, detail=f"features query failed: {exc}") from exc
    if feats is None:
        raise HTTPException(404, detail=f"No features_daily row for {ticker_u}")
    missing = [f for f in features if feats.get(f) is None]
    if missing:
        raise HTTPException(422, detail=f"features_daily nulls: {missing}")

    x_vec = np.array([[feats[f] for f in features]], dtype=float)

    # Predict em cada pickle
    items: list[dict[str, Any]] = []
    for pkl, meta in pickles:
        h = int(meta.get("horizon_days", 1))
        try:
            with pkl.open("rb") as fh:
                model = pickle.load(fh)
            pred_log = float(model.predict(x_vec)[0])
        except Exception as exc:
            log.warning("predict_ensemble.skip_pickle", file=pkl.name, error=str(exc))
            continue
        sharpe = None
        try:
            sharpe = float(
                ((meta.get("metrics") or {}).get("test_sharpe"))
                or ((meta.get("metrics") or {}).get("sharpe"))
            )
        except (TypeError, ValueError):
            sharpe = None
        items.append(
            {
                "horizon_days": h,
                "model_file": pkl.name,
                "pred_log": pred_log,
                "sharpe": sharpe,
                # Annualiza para 1d para agregar comparavel
                "pred_log_1d": pred_log / max(h, 1),
            }
        )

    if not items:
        raise HTTPException(500, detail="Todos pickles falharam na inferencia")

    # Pesos: sharpe-based se todos tem sharpe positivo, else uniforme
    sharpes = [it["sharpe"] for it in items if it.get("sharpe") is not None]
    use_sharpe = (len(sharpes) == len(items)) and all(s > 0 for s in sharpes)
    if use_sharpe:
        total = sum(sharpes)
        weights = [it["sharpe"] / total for it in items]
        weighting = "sharpe"
    else:
        n = len(items)
        weights = [1.0 / n] * n
        weighting = "uniform"

    ensemble_log_1d = sum(it["pred_log_1d"] * w for it, w in zip(items, weights))
    ensemble_pct = (float(np.exp(ensemble_log_1d)) - 1.0) * 100.0

    # Signal via cfg sobre ensemble (cfg horizon eh referencia, mas
    # ensemble esta em 1d-equiv -> usa scaled_linear)
    cfg = _load_calibration(ticker_u, dsn)
    signal = signal_method = None
    calibration_obj = None
    if cfg is not None:
        signal, signal_method = _signal_from_prediction(ensemble_log_1d, cfg, model_horizon=1)
        calibration_obj = CalibrationInfo(
            th_buy=cfg["th_buy"],
            th_sell=cfg["th_sell"],
            horizon_days=cfg["horizon_days"],
            best_sharpe=cfg["best_sharpe"],
            best_return_pct=cfg["best_return_pct"],
            best_trades=cfg["best_trades"],
            best_win_rate=cfg["best_win_rate"],
            calibrated_at=cfg["calibrated_at"],
        )

    return EnsembleResponse(
        ticker=ticker_u,
        reference_date=feats["dia"],
        ensemble_log_return=ensemble_log_1d,
        ensemble_return_pct=round(ensemble_pct, 4),
        weighting=weighting,
        horizons=[
            EnsembleHorizonItem(
                horizon_days=it["horizon_days"],
                model_file=it["model_file"],
                predicted_log_return=it["pred_log"],
                predicted_return_pct=round((float(np.exp(it["pred_log"])) - 1.0) * 100.0, 4),
                weight=round(w, 4),
                sharpe=it["sharpe"],
            )
            for it, w in zip(items, weights)
        ],
        signal=signal,
        signal_method=signal_method,
        calibration=calibration_obj,
    )
