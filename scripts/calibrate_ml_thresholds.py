"""
calibrate_ml_thresholds.py — grid search th_buy/th_sell por ticker com
walk-forward. Persiste configuração ótima em ticker_ml_config.

Por ticker:
  1. Carrega rows de features_daily_full.
  2. Walk-forward UMA vez (treino retrain_days=63): gera scores cached.
  3. Para cada (th_buy, th_sell) no grid: gera signals do score cached +
     roda run_backtest → métricas.
  4. Seleciona combinação com melhor Sharpe (tiebreaker: total_return).
  5. UPSERT em ticker_ml_config.

Uso:
    python scripts/calibrate_ml_thresholds.py --top 20
    python scripts/calibrate_ml_thresholds.py --tickers PETR4,VALE3
    python scripts/calibrate_ml_thresholds.py --all        # watchlist VERDE
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone as tz
import os
from pathlib import Path
import sys
import time
from typing import Any

import numpy as np
import psycopg2
import psycopg2.extras

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

# Grid de thresholds (calibrado para scores em escala "Brasil 2020-2025")
GRID_BUY = [-0.5, -0.3, -0.1, 0.0, 0.1, 0.3]
GRID_SELL = [-0.5, -0.3, -0.1, 0.0]


def load_rows(conn, ticker: str, include_rf: bool, horizon: int):
    features = FEATURES_EQUITY + (FEATURES_RF if include_rf else [])
    cols = ", ".join(features)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT dia, {cols} FROM features_daily_full WHERE ticker=%s ORDER BY dia ASC",
            (ticker,),
        )
        raw = cur.fetchall()
    closes = [float(r[1]) if r[1] is not None else None for r in raw]
    rows = []
    for i, r in enumerate(raw):
        feats = {
            f: float(r[j + 1]) if r[j + 1] is not None else None for j, f in enumerate(features)
        }
        close = closes[i]
        tgt = None
        if (
            i + horizon < len(raw)
            and close
            and closes[i + horizon]
            and close > 0
            and closes[i + horizon] > 0
        ):
            tgt = float(np.log(closes[i + horizon] / close))
        rows.append(
            {
                "dia": r[0],
                "close": close or 0.0,
                "features": feats,
                "vol_21d": feats.get("vol_21d"),
                "target_forward": tgt,
            }
        )
    return rows, features


def train_q(rows, features, mask):
    import lightgbm as lgb

    X, y = [], []
    for i, r in enumerate(rows):
        if not mask[i] or r["target_forward"] is None:
            continue
        feats = [r["features"].get(f) for f in features]
        if any(v is None for v in feats):
            continue
        X.append(feats)
        y.append(r["target_forward"])
    if len(X) < 50:
        return {}
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    models = {}
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


def _prob_pos(p10, p50, p90):
    if p10 >= 0:
        return 0.95
    if p90 <= 0:
        return 0.05
    if p50 > 0:
        return max(0.01, min(0.99, 0.50 + 0.40 * (p50 / (p50 - p10 + 1e-9))))
    return max(0.01, min(0.99, 0.10 + 0.40 * (p90 / (p90 - p50 + 1e-9))))


def compute_scores(
    rows, features, test_start_idx: int, retrain_days: int
) -> tuple[list[float | None], list[int]]:
    """
    Walk-forward: retorna lista de scores alinhada com rows[test_start_idx:].
    None onde features insuficientes.
    """
    scores: list[float | None] = []
    retrains = 0
    models = None

    def _train(end_idx: int):
        nonlocal models, retrains
        mask = np.zeros(len(rows), dtype=bool)
        mask[:end_idx] = True
        models = train_q(rows, features, mask)
        retrains += 1

    _train(test_start_idx)

    for j, i in enumerate(range(test_start_idx, len(rows))):
        if j > 0 and j % retrain_days == 0:
            _train(i)
        r = rows[i]
        feats_vec = [r["features"].get(f) for f in features]
        if not models or any(v is None for v in feats_vec) or r["vol_21d"] is None:
            scores.append(None)
            continue
        x = np.asarray([feats_vec], dtype=float)
        p10 = float(models[0.10].predict(x)[0])
        p50 = float(models[0.50].predict(x)[0])
        p90 = float(models[0.90].predict(x)[0])
        pp = _prob_pos(p10, p50, p90)
        var_c = max(r["vol_21d"], 1e-4)
        scores.append(pp * (p50 / var_c))
    return scores, retrains


def rows_to_bars(rows, start_idx):
    bars = []
    for i in range(start_idx, len(rows)):
        r = rows[i]
        if r["close"] <= 0:
            continue
        dt = datetime.combine(r["dia"], datetime.min.time(), tzinfo=tz.utc)
        bars.append(
            {
                "time": int(dt.timestamp()),
                "open": r["close"],
                "high": r["close"],
                "low": r["close"],
                "close": r["close"],
                "volume": 0,
            }
        )
    return bars


class CachedStrategy:
    name = "Cached"

    def __init__(self, signals: list[Signal]):
        self._signals = signals

    def generate_signals(self, bars):
        return self._signals


def evaluate(scores, test_rows, bars, th_buy, th_sell, ticker, commission) -> dict:
    signals = []
    for j, r in enumerate(test_rows):
        s = scores[j] if j < len(scores) else None
        if s is None or r["close"] <= 0:
            continue
        if s >= th_buy:
            signals.append(Signal.BUY)
        elif s <= th_sell:
            signals.append(Signal.SELL)
        else:
            signals.append(Signal.HOLD)
    if len(signals) != len(bars):
        return {}
    strat = CachedStrategy(signals)
    r = run_backtest(
        bars=bars,
        strategy=strat,
        ticker=ticker,
        initial_capital=100_000.0,
        position_size=1.0,
        commission_pct=commission,
        range_period="cal",
    )
    m = r.metrics.to_dict()
    m["trades"] = len(r.trades)
    m["wins"] = sum(1 for t in r.trades if t.is_winner)
    return m


def calibrate_one(
    conn,
    ticker: str,
    include_rf: bool,
    horizon: int,
    retrain_days: int,
    train_end: date,
    commission: float,
) -> dict | None:
    t0 = time.time()
    rows, features = load_rows(conn, ticker, include_rf, horizon)
    if len(rows) < 100:
        return None
    dates = [r["dia"] for r in rows]
    test_start = next((i for i, d in enumerate(dates) if d > train_end), len(rows))
    if test_start >= len(rows) - 20:
        return None

    scores, retrains = compute_scores(rows, features, test_start, retrain_days)
    test_rows = rows[test_start:]
    bars = []
    score_aligned = []
    for j, r in enumerate(test_rows):
        if r["close"] <= 0:
            continue
        bars.append(
            {
                "time": int(
                    datetime.combine(r["dia"], datetime.min.time(), tzinfo=tz.utc).timestamp()
                ),
                "open": r["close"],
                "high": r["close"],
                "low": r["close"],
                "close": r["close"],
                "volume": 0,
            }
        )
        score_aligned.append(scores[j] if j < len(scores) else None)

    best = None
    for tb in GRID_BUY:
        for ts in GRID_SELL:
            if ts >= tb:  # sell precisa ser menor que buy
                continue
            signals = [
                Signal.BUY
                if (sc is not None and sc >= tb)
                else (Signal.SELL if (sc is not None and sc <= ts) else Signal.HOLD)
                for sc in score_aligned
            ]
            strat = CachedStrategy(signals)
            try:
                r = run_backtest(
                    bars=bars,
                    strategy=strat,
                    ticker=ticker,
                    initial_capital=100_000.0,
                    position_size=1.0,
                    commission_pct=commission,
                    range_period="cal",
                )
            except Exception:
                continue
            m = r.metrics.to_dict()
            cand = {
                "th_buy": tb,
                "th_sell": ts,
                "sharpe": float(m.get("sharpe_ratio", 0) or 0),
                "return_pct": float(m.get("total_return_pct", 0) or 0),
                "max_dd": float(m.get("max_drawdown_pct", 0) or 0),
                "trades": len(r.trades),
                "win_rate": float(m.get("win_rate_pct", 0) or 0),
            }
            if cand["trades"] < 3:  # filtro: precisa pelo menos 3 trades
                continue
            if best is None or (cand["sharpe"], cand["return_pct"]) > (
                best["sharpe"],
                best["return_pct"],
            ):
                best = cand

    if best is None:
        return None
    best["ticker"] = ticker
    best["include_rf"] = include_rf
    best["horizon_days"] = horizon
    best["retrain_days"] = retrain_days
    best["elapsed"] = time.time() - t0
    best["retrains"] = retrains
    return best


def upsert_config(conn, cfg: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ticker_ml_config
                (ticker, include_rf, horizon_days, retrain_days, th_buy, th_sell,
                 best_sharpe, best_return_pct, best_trades, best_win_rate, best_max_dd)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (ticker) DO UPDATE SET
                include_rf=EXCLUDED.include_rf, horizon_days=EXCLUDED.horizon_days,
                retrain_days=EXCLUDED.retrain_days, th_buy=EXCLUDED.th_buy,
                th_sell=EXCLUDED.th_sell, best_sharpe=EXCLUDED.best_sharpe,
                best_return_pct=EXCLUDED.best_return_pct,
                best_trades=EXCLUDED.best_trades, best_win_rate=EXCLUDED.best_win_rate,
                best_max_dd=EXCLUDED.best_max_dd, calibrated_at=now()
            """,
            (
                cfg["ticker"],
                cfg["include_rf"],
                cfg["horizon_days"],
                cfg["retrain_days"],
                cfg["th_buy"],
                cfg["th_sell"],
                cfg["sharpe"],
                cfg["return_pct"],
                cfg["trades"],
                cfg["win_rate"],
                cfg["max_dd"],
            ),
        )


def list_tickers(conn, args) -> list[str]:
    if args.tickers:
        return [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    with conn.cursor() as cur:
        if args.all:
            cur.execute(
                "SELECT ticker FROM watchlist_tickers WHERE status='VERDE' ORDER BY mediana_vol_brl DESC"
            )
        else:
            cur.execute(
                "SELECT ticker FROM watchlist_tickers WHERE status='VERDE' ORDER BY mediana_vol_brl DESC LIMIT %s",
                (args.top,),
            )
        return [r[0] for r in cur.fetchall()]


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--tickers")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--all", action="store_true")
    p.add_argument("--no-rf", action="store_true")
    p.add_argument("--horizon", type=int, default=21)
    p.add_argument("--retrain-days", type=int, default=63)
    p.add_argument("--train-end", default="2023-12-31")
    p.add_argument("--commission", type=float, default=0.001)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    include_rf = not args.no_rf
    train_end = date.fromisoformat(args.train_end)
    conn = psycopg2.connect(DSN)
    try:
        tickers = list_tickers(conn, args)
        print(
            f"Calibrando {len(tickers)} tickers  rf={include_rf}  horizon={args.horizon}  retrain={args.retrain_days}  dry={args.dry_run}"
        )
        results = []
        for i, t in enumerate(tickers, 1):
            cfg = calibrate_one(
                conn, t, include_rf, args.horizon, args.retrain_days, train_end, args.commission
            )
            if cfg is None:
                print(f"[{i}/{len(tickers)}] {t}: skip (insufficient data / no valid combo)")
                continue
            results.append(cfg)
            print(
                f"[{i}/{len(tickers)}] {t:<7} th=[{cfg['th_buy']:+.2f},{cfg['th_sell']:+.2f}] "
                f"sharpe={cfg['sharpe']:+.3f} ret={cfg['return_pct']:+.1f}% "
                f"trades={cfg['trades']} win={cfg['win_rate']:.0f}% dd={cfg['max_dd']:.1f}% ({cfg['elapsed']:.1f}s)"
            )
            if not args.dry_run:
                upsert_config(conn, cfg)
                conn.commit()
        print(f"\nDone: {len(results)} calibrated")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
