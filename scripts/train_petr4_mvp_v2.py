"""
train_petr4_mvp_v2.py — MVP v2 com features cross-asset de renda fixa.

Diferenças vs v1 (train_petr4_mvp.py):
  - Usa view features_daily_full (JOIN features_daily + rates_features_daily).
  - Feature set estendido com 23 features RF (slope, TSMOM, carry, value,
    breakeven, Nelson-Siegel, V+M, FRA).
  - Hypótese: IC e Sharpe devem subir com cross-asset.

Uso:
    python scripts/train_petr4_mvp_v2.py --ticker PETR4
    python scripts/train_petr4_mvp_v2.py --ticker PETR4 --no-rf   # baseline v1
"""

from __future__ import annotations

import argparse
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

FEATURES_EQUITY = [
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

FEATURES_RF = [
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
    "value_ntnb_5y_z",
    "breakeven_1y",
    "breakeven_2y",
    "breakeven_5y",
    "ns_level",
    "ns_slope",
    "ns_curvature",
    "vm_combo_2y",
    "vm_combo_5y",
]


def load_features(ticker: str, include_rf: bool = True) -> list[dict]:
    features = FEATURES_EQUITY + (FEATURES_RF if include_rf else [])
    cols = ", ".join(features)
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT dia, {cols} FROM features_daily_full WHERE ticker = %s ORDER BY dia ASC",
            (ticker,),
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        d: dict = {"dia": r[0]}
        for i, f in enumerate(features, 1):
            v = r[i]
            d[f] = float(v) if v is not None else None
        out.append(d)
    return out


def build_target(rows: list[dict], horizon: int = 1) -> list[float | None]:
    closes = [r["close"] for r in rows]
    out: list[float | None] = []
    for i in range(len(closes)):
        if (
            i + horizon >= len(closes)
            or closes[i] is None
            or closes[i + horizon] is None
            or closes[i] <= 0
            or closes[i + horizon] <= 0
        ):
            out.append(None)
        else:
            out.append(float(np.log(closes[i + horizon] / closes[i])))
    return out


def to_xy(rows: list[dict], features: list[str], target: list[float | None]):
    X, y, dates = [], [], []
    for i, r in enumerate(rows):
        tgt = target[i]
        if tgt is None:
            continue
        row_vals = [r.get(f) for f in features]
        if any(v is None for v in row_vals):
            continue
        X.append(row_vals)
        y.append(tgt)
        dates.append(r["dia"])
    return np.array(X, dtype=float), np.array(y, dtype=float), dates


def split_dates(dates, ranges: dict | None = None):
    """Splits train/val/test por períodos de data.

    Default (ações com histórico longo, 2020+):
        train: 2020-01-01 → 2023-12-31
        val:   2024-01-01 → 2024-12-31
        test:  2025-01-01 →

    Para FIIs (histórico Yahoo de 2 anos), passar ranges customizados.
    """
    r = ranges or {
        "train": (date(2020, 1, 1), date(2023, 12, 31)),
        "val": (date(2024, 1, 1), date(2024, 12, 31)),
        "test": (date(2025, 1, 1), date(2099, 12, 31)),
    }
    mask_train = np.array([(r["train"][0] <= d <= r["train"][1]) for d in dates])
    mask_val = np.array([(r["val"][0] <= d <= r["val"][1]) for d in dates])
    mask_test = np.array([(r["test"][0] <= d <= r["test"][1]) for d in dates])
    return mask_train, mask_val, mask_test


def ic_spearman(pred, true):
    if len(pred) < 5:
        return 0.0
    from scipy.stats import spearmanr

    r, _ = spearmanr(pred, true)
    return float(r) if not np.isnan(r) else 0.0


def hit_rate(pred, true):
    return float(np.mean(np.sign(pred) == np.sign(true))) if len(pred) else 0.0


def sharpe_ls(pred, true):
    if len(pred) < 20:
        return 0.0
    pnl = np.where(pred > 0, true, -true)
    s = pnl.std()
    return float((pnl.mean() / s) * np.sqrt(252)) if s > 0 else 0.0


def train_and_eval(X_tr, y_tr, X_val, y_val, X_te, y_te):
    import lightgbm as lgb

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
    if len(X_val) > 0:
        model.fit(
            X_tr, y_tr, eval_set=[(X_val, y_val)], callbacks=[lgb.early_stopping(30, verbose=False)]
        )
    else:
        model.fit(X_tr, y_tr)
    p_val = model.predict(X_val) if len(X_val) else np.array([])
    p_te = model.predict(X_te) if len(X_te) else np.array([])
    return model, {
        "train_size": int(len(X_tr)),
        "val_size": int(len(X_val)),
        "test_size": int(len(X_te)),
        "val_ic": ic_spearman(p_val, y_val) if len(y_val) else None,
        "val_hit": hit_rate(p_val, y_val) if len(y_val) else None,
        "val_sharpe": sharpe_ls(p_val, y_val) if len(y_val) else None,
        "test_ic": ic_spearman(p_te, y_te) if len(y_te) else None,
        "test_hit": hit_rate(p_te, y_te) if len(y_te) else None,
        "test_sharpe": sharpe_ls(p_te, y_te) if len(y_te) else None,
    }


def serialize(
    model, ticker, metrics, features, horizon: int = 1, out_dir_override: str | None = None
):
    out_dir = (
        Path(out_dir_override)
        if out_dir_override
        else (Path(__file__).resolve().parent.parent / "models")
    )
    out_dir.mkdir(exist_ok=True, parents=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    suffix = f"_h{horizon}" if horizon != 1 else ""
    stem = out_dir / f"mvp_v2{suffix}_{ticker}_{ts}"
    with stem.with_suffix(".pkl").open("wb") as f:
        pickle.dump(model, f)
    meta = {
        "ticker": ticker,
        "trained_at_utc": ts,
        "version": f"v2_cross_asset_h{horizon}",
        "horizon_days": horizon,
        "features": features,
        "n_features": len(features),
        "model": "lightgbm.LGBMRegressor",
        "metrics": metrics,
        "file": stem.with_suffix(".pkl").name,
    }
    with stem.with_suffix(".json").open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, default=str)
    return stem.with_suffix(".pkl")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--ticker", default="PETR4")
    p.add_argument("--no-rf", action="store_true", help="baseline sem RF features")
    p.add_argument(
        "--horizon",
        type=int,
        default=1,
        help="target: log(close[i+h]/close[i]). 1=1d, 21=21d (bate com calibracao)",
    )
    p.add_argument("--train-start", default=None, help="Override train start (YYYY-MM-DD)")
    p.add_argument("--train-end", default=None, help="Override train end")
    p.add_argument("--val-start", default=None, help="Override val start")
    p.add_argument("--val-end", default=None, help="Override val end")
    p.add_argument("--test-start", default=None, help="Override test start")
    p.add_argument(
        "--out-dir", default=None, help="Override output dir for pickle (default: ../models)"
    )
    return p.parse_args()


def main():
    args = parse_args()
    include_rf = not args.no_rf
    features = FEATURES_EQUITY + (FEATURES_RF if include_rf else [])

    rows = load_features(args.ticker, include_rf=include_rf)
    if not rows:
        print(f"Sem features para {args.ticker}")
        return 2

    target = build_target(rows, horizon=args.horizon)
    X, y, dates = to_xy(rows, features, target)
    # Permite override dos splits via CLI (necessário p/ tickers com histórico curto, ex: FIIs Yahoo 2y)
    custom = None
    if args.train_start or args.train_end or args.val_start or args.val_end or args.test_start:
        custom = {
            "train": (
                date.fromisoformat(args.train_start) if args.train_start else date(2020, 1, 1),
                date.fromisoformat(args.train_end) if args.train_end else date(2023, 12, 31),
            ),
            "val": (
                date.fromisoformat(args.val_start) if args.val_start else date(2024, 1, 1),
                date.fromisoformat(args.val_end) if args.val_end else date(2024, 12, 31),
            ),
            "test": (
                date.fromisoformat(args.test_start) if args.test_start else date(2025, 1, 1),
                date(2099, 12, 31),
            ),
        }
    m_tr, m_val, m_te = split_dates(dates, custom)

    print(
        f"Ticker={args.ticker}  include_rf={include_rf}  horizon={args.horizon}d  "
        f"features={len(features)}  rows_uteis={len(X)}"
    )
    print(f"  train={int(m_tr.sum())} val={int(m_val.sum())} test={int(m_te.sum())}")
    if int(m_tr.sum()) < 50:
        print("train < 50 rows -> abort")
        return 2

    model, metrics = train_and_eval(X[m_tr], y[m_tr], X[m_val], y[m_val], X[m_te], y[m_te])
    print("Metricas:")
    for k, v in metrics.items():
        print(f"  {k:>12} = {v}")

    pkl = serialize(
        model, args.ticker, metrics, features, horizon=args.horizon, out_dir_override=args.out_dir
    )
    print(f"modelo: {pkl}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
