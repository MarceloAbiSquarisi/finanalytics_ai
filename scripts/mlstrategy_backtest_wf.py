"""
mlstrategy_backtest_wf.py — walk-forward do mlstrategy_backtest.

Diferente do mlstrategy_backtest.py (fit-once), aqui o modelo é re-treinado
a cada `retrain_days` dias no test window. Reduz staleness e reflete
pipeline de produção real (model drift + regime change).

Mecânica:
  1. Fit inicial: train window (default até 2023-12-31).
  2. Para cada bar no test window:
     a. Se (idx_no_test % retrain_days == 0) e idx > 0: re-treinar com
        toda a história até o dia anterior.
     b. Gerar sinal com modelo atual.
  3. Executa backtest (run_backtest) com sinais resultantes.

Uso:
    python scripts/mlstrategy_backtest_wf.py --ticker WEGE3
    python scripts/mlstrategy_backtest_wf.py --ticker WEGE3 --retrain-days 63
    python scripts/mlstrategy_backtest_wf.py --ticker WEGE3 --no-rf
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timezone as tz
import os
from pathlib import Path
import sys
from typing import Any

import numpy as np
import psycopg2

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

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


@dataclass
class Row:
    dia: date
    close: float
    features: dict[str, float | None]
    vol_21d: float | None
    target_forward: float | None


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


def train_models(rows: list[Row], features: list[str], mask: np.ndarray) -> dict:
    """Treina 3 LightGBM quantile (P10/P50/P90) usando rows onde mask=True."""
    import lightgbm as lgb

    X, y = [], []
    for i, r in enumerate(rows):
        if not mask[i] or r.target_forward is None:
            continue
        feats = [r.features.get(f) for f in features]
        if any(v is None for v in feats):
            continue
        X.append(feats)
        y.append(r.target_forward)
    if len(X) < 50:
        return {}
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    models: dict[float, Any] = {}
    for q in QUANTILES:
        m = lgb.LGBMRegressor(
            objective="quantile",
            alpha=q,
            n_estimators=300,
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


def predict_q(models: dict, feats_vec: list[float]) -> tuple[float, float, float]:
    x = np.asarray([feats_vec], dtype=float)
    return (
        float(models[0.10].predict(x)[0]),
        float(models[0.50].predict(x)[0]),
        float(models[0.90].predict(x)[0]),
    )


def prob_positive(p10: float, p50: float, p90: float) -> float:
    if p10 >= 0:
        return 0.95
    if p90 <= 0:
        return 0.05
    if p50 > 0:
        return max(0.01, min(0.99, 0.50 + 0.40 * (p50 / (p50 - p10 + 1e-9))))
    return max(0.01, min(0.99, 0.10 + 0.40 * (p90 / (p90 - p50 + 1e-9))))


def score_sig(prob_pos: float, p50: float, vol_21d: float) -> float:
    var_c = max(vol_21d, 1e-4)
    return prob_pos * (p50 / var_c)


def classify(score: float, th_buy: float, th_sell: float) -> Signal:
    if score >= th_buy:
        return Signal.BUY
    if score <= th_sell:
        return Signal.SELL
    return Signal.HOLD


class WalkForwardStrategy:
    name = "MLQuantile_WalkForward"

    def __init__(
        self,
        features: list[str],
        rows: list[Row],
        th_buy: float,
        th_sell: float,
        test_start_idx: int,
        retrain_days: int,
    ):
        self.features = features
        self.rows = rows
        self.th_buy = th_buy
        self.th_sell = th_sell
        self.test_start_idx = test_start_idx
        self.retrain_days = retrain_days
        self.models: dict[float, Any] = {}
        self.scores_log: list[float] = []
        self.retrains = 0

    def _train_until(self, end_idx_excl: int) -> None:
        mask = np.zeros(len(self.rows), dtype=bool)
        mask[:end_idx_excl] = True
        self.models = train_models(self.rows, self.features, mask)
        self.retrains += 1

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        """bars são só os dias do test window."""
        # Primeiro fit
        self._train_until(self.test_start_idx)
        signals: list[Signal] = []
        for j, bar in enumerate(bars):
            abs_idx = self.test_start_idx + j
            if j > 0 and (j % self.retrain_days == 0):
                self._train_until(abs_idx)
            r = self.rows[abs_idx]
            feats = [r.features.get(f) for f in self.features]
            if not self.models or any(v is None for v in feats) or r.vol_21d is None:
                signals.append(Signal.HOLD)
                continue
            p10, p50, p90 = predict_q(self.models, feats)
            pp = prob_positive(p10, p50, p90)
            sc = score_sig(pp, p50, r.vol_21d)
            self.scores_log.append(sc)
            signals.append(classify(sc, self.th_buy, self.th_sell))
        return signals


def rows_to_bars(rows: list[Row], start_idx: int) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    for i in range(start_idx, len(rows)):
        r = rows[i]
        if r.close <= 0:
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
    return bars


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--ticker", default="PETR4")
    p.add_argument("--no-rf", action="store_true")
    p.add_argument("--horizon", type=int, default=21)
    p.add_argument("--commission", type=float, default=0.001)
    p.add_argument("--train-end", default="2023-12-31")
    p.add_argument(
        "--retrain-days", type=int, default=63, help="re-treino a cada N dias úteis no test"
    )
    p.add_argument("--th-buy", type=float, default=0.10)
    p.add_argument("--th-sell", type=float, default=-0.10)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    features = FEATURES_EQUITY + ([] if args.no_rf else FEATURES_RF)
    print(
        f"WF backtest {args.ticker}  feat={len(features)}  h={args.horizon}d  retrain={args.retrain_days}d  include_rf={not args.no_rf}"
    )

    rows = load_rows(args.ticker, include_rf=not args.no_rf, horizon=args.horizon)
    dates = [r.dia for r in rows]
    train_end = date.fromisoformat(args.train_end)
    # test_start_idx: primeiro row com dia > train_end
    test_start_idx = next((i for i, d in enumerate(dates) if d > train_end), len(rows))
    if test_start_idx >= len(rows) - 20:
        print(f"test window muito curta ({len(rows) - test_start_idx}) — abort")
        return 2
    print(
        f"  total rows={len(rows)}  test_start_idx={test_start_idx}  test_len={len(rows) - test_start_idx}"
    )

    strategy = WalkForwardStrategy(
        features=features,
        rows=rows,
        th_buy=args.th_buy,
        th_sell=args.th_sell,
        test_start_idx=test_start_idx,
        retrain_days=args.retrain_days,
    )

    bars = rows_to_bars(rows, test_start_idx)
    # Match bars com rows[test_start_idx:] (filtrando close <= 0)
    print(f"  bars={len(bars)}  rodando walk-forward (retrain_days={args.retrain_days})...")
    result = run_backtest(
        bars=bars,
        strategy=strategy,
        ticker=args.ticker,
        initial_capital=100_000.0,
        position_size=1.0,
        commission_pct=args.commission,
        range_period="test_wf",
    )

    print(f"\n=== WF Backtest Metrics ({strategy.retrains} re-treinos) ===")
    for k, v in result.metrics.to_dict().items():
        print(f"  {k:>24} = {v}")
    print(f"Trades={len(result.trades)}  winners={sum(1 for t in result.trades if t.is_winner)}")
    if strategy.scores_log:
        arr = np.array(strategy.scores_log)
        print(
            f"Scores: mean={arr.mean():.4f}  std={arr.std():.4f}  "
            f"min={arr.min():.4f}  max={arr.max():.4f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
