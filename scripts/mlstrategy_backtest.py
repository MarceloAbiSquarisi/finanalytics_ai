"""
mlstrategy_backtest.py — pipeline produção integrando:

  features_daily_full (cross-asset)
    → QuantileForecaster 21d (P10/P50/P90/prob_positive)
    → Score MLStrategy (prob_positive × p50 / vol_21d)
    → Signal BUY/SELL/HOLD com thresholds MLStrategy
    → domain/backtesting/engine.run_backtest
    → BacktestResult (Sharpe real, drawdown, win rate, profit factor)

Diferente de train_petr4_mvp_v2.py (que só mede IC/Sharpe LS naive),
aqui o backtest simula operações com commissão B3 (0.1%) e position sizing.

Uso:
    python scripts/mlstrategy_backtest.py --ticker PETR4
    python scripts/mlstrategy_backtest.py --ticker PETR4 --no-rf  # baseline
    python scripts/mlstrategy_backtest.py --ticker PETR4 --horizon 21 --commission 0.001
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timezone as tz
import os
import sys
from typing import Any

import numpy as np
import psycopg2

from finanalytics_ai.domain.backtesting.engine import Signal, run_backtest

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
    "breakeven_1y",
    "breakeven_2y",
    "breakeven_5y",
    "ns_level",
    "ns_slope",
    "ns_curvature",
    "vm_combo_2y",
    "vm_combo_5y",
]

QUANTILES = [0.10, 0.50, 0.90]
# Defaults alinhados com MLStrategy (IBOV-calibrado).
TH_STRONG_BUY, TH_BUY, TH_SELL, TH_STRONG_SELL = 0.30, 0.10, -0.10, -0.30


@dataclass
class Row:
    dia: date
    close: float
    features: dict[str, float]
    vol_21d: float | None
    target_forward: float | None  # log-return do dia t+horizon


def load_rows(ticker: str, include_rf: bool, horizon: int) -> list[Row]:
    features = FEATURES_EQUITY + (FEATURES_RF if include_rf else [])
    cols = ", ".join(features)
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT dia, {cols} FROM features_daily_full WHERE ticker=%s ORDER BY dia ASC",
            (ticker,),
        )
        raw = cur.fetchall()
    out: list[Row] = []
    closes = [float(r[1]) if r[1] is not None else None for r in raw]
    for i, r in enumerate(raw):
        dia = r[0]
        close = closes[i]
        feats = {
            f: float(r[j + 1]) if r[j + 1] is not None else None for j, f in enumerate(features)
        }
        vol = feats.get("vol_21d")
        # Forward log-return
        tgt = None
        if (
            i + horizon < len(raw)
            and close is not None
            and closes[i + horizon]
            and closes[i + horizon] > 0
            and close > 0
        ):
            tgt = float(np.log(closes[i + horizon] / close))
        out.append(Row(dia, close or 0.0, feats, vol, tgt))
    return out


def train_quantile_models(rows: list[Row], features: list[str], mask_train: np.ndarray) -> dict:
    import lightgbm as lgb

    X, y = [], []
    for i, r in enumerate(rows):
        if not mask_train[i] or r.target_forward is None:
            continue
        feats = [r.features.get(f) for f in features]
        if any(v is None for v in feats):
            continue
        X.append(feats)
        y.append(r.target_forward)
    if len(X) < 50:
        raise RuntimeError(f"train set muito pequeno ({len(X)})")
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    models: dict[float, Any] = {}
    for q in QUANTILES:
        m = lgb.LGBMRegressor(
            objective="quantile",
            alpha=q,
            n_estimators=400,
            learning_rate=0.03,
            num_leaves=31,
            min_child_samples=20,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            verbose=-1,
        )
        m.fit(X, y)
        models[q] = m
    return models


def predict_quantiles(models: dict, feats_vec: list[float]) -> tuple[float, float, float]:
    x = np.asarray([feats_vec], dtype=float)
    p10 = float(models[0.10].predict(x)[0])
    p50 = float(models[0.50].predict(x)[0])
    p90 = float(models[0.90].predict(x)[0])
    return p10, p50, p90


def prob_positive(p10: float, p50: float, p90: float) -> float:
    if p10 >= 0:
        return 0.95
    if p90 <= 0:
        return 0.05
    if p50 > 0:
        return max(0.01, min(0.99, 0.50 + 0.40 * (p50 / (p50 - p10 + 1e-9))))
    return max(0.01, min(0.99, 0.10 + 0.40 * (p90 / (p90 - p50 + 1e-9))))


def score_mlstrategy(prob_pos: float, p50: float, vol_21d: float) -> float:
    """Score análogo ao MLStrategy (prob_pos × p50 / var)."""
    var_c = max(vol_21d, 1e-4)
    return prob_pos * (p50 / var_c)


def classify(score: float, th_buy: float, th_sell: float) -> Signal:
    if score >= th_buy:
        return Signal.BUY
    if score <= th_sell:
        return Signal.SELL
    return Signal.HOLD


class MLBacktestStrategy:
    """Implementa Strategy Protocol de domain/backtesting/engine."""

    name = "MLQuantile_CrossAsset"

    def __init__(
        self, models: dict, features: list[str], rows: list[Row], th_buy: float, th_sell: float
    ):
        self.models = models
        self.features = features
        self.rows = rows
        self.th_buy = th_buy
        self.th_sell = th_sell
        self.scores_log: list[float] = []

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        """Para cada bar, computa o sinal com base nas features do dia."""
        signals: list[Signal] = []
        for i, bar in enumerate(bars):
            r = self.rows[i]
            feats = [r.features.get(f) for f in self.features]
            if any(v is None for v in feats) or r.vol_21d is None:
                signals.append(Signal.HOLD)
                continue
            p10, p50, p90 = predict_quantiles(self.models, feats)
            pp = prob_positive(p10, p50, p90)
            score = score_mlstrategy(pp, p50, r.vol_21d)
            self.scores_log.append(score)
            signals.append(classify(score, self.th_buy, self.th_sell))
        return signals


def rows_to_bars(rows: list[Row], mask_test: np.ndarray) -> tuple[list[dict[str, Any]], list[Row]]:
    bars: list[dict[str, Any]] = []
    kept: list[Row] = []
    for i, r in enumerate(rows):
        if not mask_test[i] or r.close <= 0:
            continue
        dt = datetime.combine(r.dia, datetime.min.time(), tzinfo=tz.utc)
        bars.append(
            {
                "time": int(dt.timestamp()),
                "open": r.close,
                "high": r.close,
                "low": r.close,
                "close": r.close,
                "volume": 0,
            }
        )
        kept.append(r)
    return bars, kept


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--ticker", default="PETR4")
    p.add_argument("--no-rf", action="store_true")
    p.add_argument("--horizon", type=int, default=21)
    p.add_argument("--commission", type=float, default=0.001)
    p.add_argument("--train-end", default="2023-12-31")
    p.add_argument("--th-buy", type=float, default=TH_BUY)
    p.add_argument("--th-sell", type=float, default=TH_SELL)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    features = FEATURES_EQUITY + ([] if args.no_rf else FEATURES_RF)

    print(
        f"MLStrategy backtest: ticker={args.ticker} features={len(features)} "
        f"horizon={args.horizon}d include_rf={not args.no_rf}"
    )
    rows = load_rows(args.ticker, include_rf=not args.no_rf, horizon=args.horizon)
    if not rows:
        print("Sem features — abort")
        return 2

    dates = np.array([r.dia for r in rows])
    train_end = date.fromisoformat(args.train_end)
    mask_train = np.array(
        [d <= train_end and rows[i].target_forward is not None for i, d in enumerate(dates)]
    )
    mask_test = np.array([d > train_end for d in dates])
    print(f"  total rows={len(rows)} train_mask={mask_train.sum()} test_mask={mask_test.sum()}")
    if mask_train.sum() < 50:
        print("train < 50 — abort")
        return 2

    print("  treinando QuantileForecaster (P10/P50/P90)...")
    models = train_quantile_models(rows, features, mask_train)

    bars, kept = rows_to_bars(rows, mask_test)
    if len(bars) < 20:
        print(f"  test bars={len(bars)} — insuficiente")
        return 2

    strategy = MLBacktestStrategy(models, features, kept, args.th_buy, args.th_sell)
    print(
        f"  rodando backtest (bars={len(bars)}, comissão={args.commission:.3%}, "
        f"th_buy={args.th_buy} th_sell={args.th_sell})..."
    )
    result = run_backtest(
        bars=bars,
        strategy=strategy,
        ticker=args.ticker,
        initial_capital=100_000.0,
        position_size=1.0,
        commission_pct=args.commission,
        range_period="test",
    )

    print("\n=== Backtest Metrics ===")
    m = result.metrics.to_dict()
    for k, v in m.items():
        print(f"  {k:>24} = {v}")
    print(f"\nTrades: {len(result.trades)}")
    if result.trades:
        winners = sum(1 for t in result.trades if t.is_winner)
        print(f"  winners={winners} losers={len(result.trades) - winners}")
        print(f"  avg pnl_pct={sum(t.pnl_pct for t in result.trades) / len(result.trades):.3f}%")
    if strategy.scores_log:
        arr = np.array(strategy.scores_log)
        print(
            f"\nScore stats (N={len(arr)}): "
            f"mean={arr.mean():.4f} std={arr.std():.4f} "
            f"min={arr.min():.4f} max={arr.max():.4f} "
            f"p5={np.percentile(arr, 5):.4f} p95={np.percentile(arr, 95):.4f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
