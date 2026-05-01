"""
train_petr4_mvp.py — MVP end-to-end de treino/avaliação LightGBM para PETR4.

Pipeline:
  1. Le features_daily para PETR4
  2. Target: r_1d_futuro (log-return 1d ahead)
  3. Split: 2020-2023 treino / 2024 val / 2025 teste (até 2025-11-03)
  4. Modelo: LightGBMRegressor (point prediction)
  5. Metricas: IC (Spearman), hit rate, Sharpe long-short simples
  6. Serializa pickle + metadata JSON em models/

Uso:
    python scripts/train_petr4_mvp.py
    python scripts/train_petr4_mvp.py --ticker VALE3  # outros
    python scripts/train_petr4_mvp.py --dry-run       # só imprime splits

Pos-Sprint 1 completo (6 anos de historico), re-executar para validar KPIs
(IC > 0.05, Sharpe > 0 no teste OOS).
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, datetime
import json
import os
from pathlib import Path
import pickle
import sys

import numpy as np
import psycopg2

DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)

FEATURES = [
    "close",
    "r_1d", "r_5d", "r_21d",
    "atr_14", "vol_21d", "vol_rel_20",
    "sma_50", "sma_200", "rsi_14",
]


# ─── Data loading ──────────────────────────────────────────────────────────────

def load_features(ticker: str) -> list[dict]:
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cols = ", ".join(FEATURES)
        cur.execute(
            f"SELECT dia, {cols} FROM features_daily "
            f"WHERE ticker = %s ORDER BY dia ASC",
            (ticker,),
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        d: dict = {"dia": r[0]}
        for i, f in enumerate(FEATURES, 1):
            v = r[i]
            d[f] = float(v) if v is not None else None
        out.append(d)
    return out


def build_target_r1d_futuro(rows: list[dict]) -> list[float | None]:
    """Target: log(close[t+1]/close[t]). Ultimo bar fica None."""
    closes = [r["close"] for r in rows]
    out: list[float | None] = []
    for i in range(len(closes)):
        if i + 1 >= len(closes) or closes[i] is None or closes[i + 1] is None or closes[i] <= 0:
            out.append(None)
        else:
            out.append(float(np.log(closes[i + 1] / closes[i])))
    return out


def to_xy(rows: list[dict], target: list[float | None]) -> tuple[np.ndarray, np.ndarray, list[date]]:
    X, y, dates = [], [], []
    for i, r in enumerate(rows):
        tgt = target[i]
        if tgt is None:
            continue
        row_vals = [r.get(f) for f in FEATURES]
        if any(v is None for v in row_vals):
            continue
        X.append(row_vals)
        y.append(tgt)
        dates.append(r["dia"])
    return np.array(X, dtype=float), np.array(y, dtype=float), dates


# ─── Split ─────────────────────────────────────────────────────────────────────

def split_dates(dates: list[date]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mask_train = np.array([(date(2020, 1, 1) <= d <= date(2023, 12, 31)) for d in dates])
    mask_val   = np.array([(date(2024, 1, 1) <= d <= date(2024, 12, 31)) for d in dates])
    mask_test  = np.array([(date(2025, 1, 1) <= d) for d in dates])
    return mask_train, mask_val, mask_test


# ─── Metricas ──────────────────────────────────────────────────────────────────

def ic_spearman(y_pred: np.ndarray, y_true: np.ndarray) -> float:
    """Information Coefficient (correlação de Spearman)."""
    if len(y_pred) < 5:
        return 0.0
    from scipy.stats import spearmanr  # type: ignore
    r, _ = spearmanr(y_pred, y_true)
    return float(r) if not np.isnan(r) else 0.0


def hit_rate(y_pred: np.ndarray, y_true: np.ndarray) -> float:
    if len(y_pred) == 0:
        return 0.0
    return float(np.mean(np.sign(y_pred) == np.sign(y_true)))


def sharpe_long_short(y_pred: np.ndarray, y_true: np.ndarray) -> float:
    """Sharpe da estratégia long se pred>0 senão short. 252 anualizações."""
    if len(y_pred) < 20:
        return 0.0
    pnl = np.where(y_pred > 0, y_true, -y_true)
    std = pnl.std()
    if std == 0:
        return 0.0
    return float((pnl.mean() / std) * np.sqrt(252))


# ─── Train ─────────────────────────────────────────────────────────────────────

def train_and_evaluate(X_tr, y_tr, X_val, y_val, X_te, y_te) -> tuple:
    import lightgbm as lgb  # type: ignore

    model = lgb.LGBMRegressor(
        n_estimators=400,
        learning_rate=0.03,
        max_depth=6,
        num_leaves=31,
        min_child_samples=20,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        verbose=-1,
    )
    callbacks = [lgb.early_stopping(30, verbose=False)] if len(X_val) > 0 else []
    if len(X_val) > 0:
        model.fit(X_tr, y_tr, eval_set=[(X_val, y_val)], callbacks=callbacks)
    else:
        model.fit(X_tr, y_tr)

    p_val = model.predict(X_val) if len(X_val) > 0 else np.array([])
    p_te = model.predict(X_te) if len(X_te) > 0 else np.array([])

    metrics = {
        "train_size": int(len(X_tr)),
        "val_size": int(len(X_val)),
        "test_size": int(len(X_te)),
        "val_ic_spearman":  ic_spearman(p_val, y_val) if len(y_val) else None,
        "val_hit_rate":     hit_rate(p_val, y_val) if len(y_val) else None,
        "val_sharpe_ls":    sharpe_long_short(p_val, y_val) if len(y_val) else None,
        "test_ic_spearman": ic_spearman(p_te, y_te) if len(y_te) else None,
        "test_hit_rate":    hit_rate(p_te, y_te) if len(y_te) else None,
        "test_sharpe_ls":   sharpe_long_short(p_te, y_te) if len(y_te) else None,
    }
    return model, metrics, p_te


# ─── Serialização ──────────────────────────────────────────────────────────────

def serialize(model, ticker: str, metrics: dict, features: list[str]) -> Path:
    out_dir = Path(__file__).resolve().parent.parent / "models"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    stem = out_dir / f"petr4_mvp_{ticker}_{ts}"
    pkl_path = stem.with_suffix(".pkl")
    json_path = stem.with_suffix(".json")
    with pkl_path.open("wb") as f:
        pickle.dump(model, f)
    meta = {
        "ticker": ticker,
        "trained_at_utc": ts,
        "features": features,
        "target": "r_1d_futuro (log return 1d ahead)",
        "model": "lightgbm.LGBMRegressor",
        "metrics": metrics,
        "file": str(pkl_path.name),
    }
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, default=str)
    return pkl_path


# ─── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="MVP treino LightGBM para 1 ticker")
    p.add_argument("--ticker", default="PETR4")
    p.add_argument("--dry-run", action="store_true", help="só imprime splits e sai")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    rows = load_features(args.ticker)
    if not rows:
        print(f"Sem features para {args.ticker}. Rode features_daily_builder antes.")
        return 2
    target = build_target_r1d_futuro(rows)
    X, y, dates = to_xy(rows, target)
    m_tr, m_val, m_te = split_dates(dates)

    print(f"Ticker={args.ticker} features={len(FEATURES)} rows_utilizaveis={len(X)}")
    print(f"  train (2020-2023): {int(m_tr.sum()):>4}")
    print(f"  val   (2024):       {int(m_val.sum()):>4}")
    print(f"  test  (2025+):      {int(m_te.sum()):>4}")

    if args.dry_run:
        return 0

    if int(m_tr.sum()) < 50:
        print("ERRO: train set < 50 rows. Popule features_daily antes.")
        return 2

    model, metrics, _ = train_and_evaluate(
        X[m_tr], y[m_tr], X[m_val], y[m_val], X[m_te], y[m_te]
    )
    print("\nMétricas:")
    for k, v in metrics.items():
        if isinstance(v, float):
            print(f"  {k:>22} = {v:.4f}")
        else:
            print(f"  {k:>22} = {v}")

    pkl = serialize(model, args.ticker, metrics, FEATURES)
    print(f"\nmodelo salvo em: {pkl}")
    print(f"metadata:        {pkl.with_suffix('.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
