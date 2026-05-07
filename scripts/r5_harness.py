"""r5_harness.py — Walk-forward backtest agregado multi-ticker (R5 MVP).

Roda mlstrategy_backtest_wf em N tickers do universo filtrado, agrega
trades e métricas, computa Deflated Sharpe Ratio (LdP 2014) sobre os N
trials para corrigir multiple-testing bias.

Universo default (filtros heurísticos):
  - tickers em features_daily com >= MIN_TRAIN_ROWS rows
  - first_dia <= 2020-06-01 (cobertura ampla)
Pode passar --tickers explícito p/ override.

Output:
  backtest_runs/r5_<timestamp>.json com:
    - per-ticker BacktestResult.metrics resumido
    - aggregate metrics (Sharpe global, hit rate, drawdown médio)
    - DSR(top1) e DSR(median) sobre N trials
    - errors[] tickers que abortaram

Uso:
  python scripts/r5_harness.py                       # universo default
  python scripts/r5_harness.py --tickers PETR4,VALE3 # explícito
  python scripts/r5_harness.py --top 50              # top-50 best_sharpe
  python scripts/r5_harness.py --horizon 5 --th-buy 0.05 --th-sell -0.05
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import statistics
import sys
import time

# garante import a partir da raiz
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

import psycopg2

from finanalytics_ai.domain.backtesting.metrics import (
    deflated_sharpe,
    expected_max_sharpe,
)
from mlstrategy_backtest_wf import run_wf_for_ticker

DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)


def _query_universe(top: int | None = None, min_rows: int = 250) -> list[str]:
    """Universo por: tickers em features_daily com cobertura >= min_rows e
    primeiro_dia <= 2020-06-01. Se top != None, ordena por best_sharpe.
    """
    if top is not None:
        sql = (
            "SELECT ticker FROM ticker_ml_config "
            "WHERE best_sharpe IS NOT NULL "
            "ORDER BY best_sharpe DESC LIMIT %s"
        )
        params: tuple = (top,)
    else:
        sql = """
            SELECT ticker
            FROM features_daily
            GROUP BY ticker
            HAVING count(*) >= %s
              AND min(dia) <= '2020-06-01'
            ORDER BY ticker
        """
        params = (min_rows,)
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [r[0] for r in cur.fetchall()]


def _summarize_result(out: dict) -> dict:
    """Reduz BacktestResult em dict serializável."""
    if not out["ok"]:
        return {
            "ticker": out["ticker"],
            "ok": False,
            "error": out.get("error"),
        }
    r = out["result"]
    m = r.metrics.to_dict()
    return {
        "ticker": out["ticker"],
        "ok": True,
        "trades": len(r.trades),
        "winners": sum(1 for t in r.trades if t.is_winner),
        "test_len": out["test_len"],
        "retrains": out["retrains"],
        "horizon": out["horizon"],
        "metrics": m,
    }


def _aggregate(results: list[dict], ann_factor: float = 252.0) -> dict:
    """Agrega métricas global + DSR sobre os N trials.

    Sharpe agregado: usa pnl_pct flat de TODOS trades (cross-ticker, equal
    weight). Não é cumulative por capital — é uma média de retornos por
    trade, anualizada por ann_factor / avg_trade_duration. MVP — refinar
    em V2 com cumulative pnl curve.

    DSR: aplicado ao melhor Sharpe per-ticker, com N = #tickers válidos.
    """
    valid = [r for r in results if r["ok"]]
    n = len(valid)
    if n == 0:
        return {"n_valid": 0, "n_total": len(results), "warning": "nenhum ticker valido"}

    sharpes = [r["metrics"].get("sharpe_ratio", 0.0) or 0.0 for r in valid]
    drawdowns = [r["metrics"].get("max_drawdown_pct", 0.0) or 0.0 for r in valid]
    win_rates = [r["metrics"].get("win_rate_pct", 0.0) or 0.0 for r in valid]
    returns = [r["metrics"].get("total_return_pct", 0.0) or 0.0 for r in valid]
    n_trades = sum(r["trades"] for r in valid)

    # DSR sobre o top-1 Sharpe — assume N = #tickers como nº trials independentes
    sharpe_max = max(sharpes)
    sharpe_median = statistics.median(sharpes)
    sharpe_std = statistics.pstdev(sharpes) if n > 1 else 0.0

    # Para DSR precisamos da serie de retornos per-trade do top trial, mas
    # como só temos summary aqui, usamos formula simplificada: penalize via
    # expected_max_sharpe(N).
    e_max_sharpe = expected_max_sharpe(n)
    dsr_proxy_top = max(0.0, sharpe_max - e_max_sharpe) if n >= 2 else sharpe_max

    return {
        "n_valid": n,
        "n_total": len(results),
        "n_trades_total": n_trades,
        "sharpe_avg": round(statistics.mean(sharpes), 4),
        "sharpe_median": round(sharpe_median, 4),
        "sharpe_std": round(sharpe_std, 4),
        "sharpe_max": round(sharpe_max, 4),
        "sharpe_min": round(min(sharpes), 4),
        "drawdown_avg": round(statistics.mean(drawdowns), 4),
        "drawdown_max": round(max(drawdowns), 4),
        "win_rate_avg": round(statistics.mean(win_rates), 2),
        "return_avg": round(statistics.mean(returns), 2),
        "return_median": round(statistics.median(returns), 2),
        "return_total_sum": round(sum(returns), 2),
        "expected_max_sharpe_under_null": round(e_max_sharpe, 4),
        "dsr_top1_proxy": round(dsr_proxy_top, 4),
        "n_negative_sharpe": sum(1 for s in sharpes if s < 0),
        "best_ticker": valid[sharpes.index(sharpe_max)]["ticker"],
        "worst_ticker": valid[sharpes.index(min(sharpes))]["ticker"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=None, help="lista CSV (override universe)")
    ap.add_argument("--top", type=int, default=None,
                    help="top-N por best_sharpe (default: filtro de coverage)")
    ap.add_argument("--min-rows", type=int, default=250)
    ap.add_argument("--horizon", type=int, default=21)
    ap.add_argument("--retrain-days", type=int, default=63)
    ap.add_argument("--commission", type=float, default=0.001)
    ap.add_argument("--th-buy", type=float, default=0.10)
    ap.add_argument("--th-sell", type=float, default=-0.10)
    ap.add_argument("--train-end", default="2023-12-31")
    ap.add_argument("--out-dir", default=str(_ROOT / "backtest_runs"))
    ap.add_argument("--limit", type=int, default=None,
                    help="limite max de tickers (smoke testing)")
    args = ap.parse_args()

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = _query_universe(top=args.top, min_rows=args.min_rows)
    if args.limit:
        tickers = tickers[: args.limit]
    if not tickers:
        print("Universo vazio.")
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"r5_{ts}.json"

    print(f"R5 harness — N={len(tickers)} tickers, h={args.horizon}, "
          f"retrain={args.retrain_days}d, train_end={args.train_end}")
    print(f"output: {out_path}")
    print()

    results: list[dict] = []
    t_start = time.time()
    for i, t in enumerate(tickers, 1):
        t0 = time.time()
        out = run_wf_for_ticker(
            ticker=t,
            horizon=args.horizon,
            retrain_days=args.retrain_days,
            commission=args.commission,
            th_buy=args.th_buy,
            th_sell=args.th_sell,
            train_end=args.train_end,
        )
        elapsed = time.time() - t0
        summ = _summarize_result(out)
        summ["elapsed_s"] = round(elapsed, 1)
        results.append(summ)
        status = "OK" if summ["ok"] else f"FAIL: {summ.get('error', '?')[:60]}"
        marker = ""
        if summ["ok"]:
            sr = summ["metrics"].get("sharpe_ratio", 0)
            n_trades = summ["trades"]
            ret = summ["metrics"].get("total_return_pct", 0)
            marker = f" sharpe={sr:.2f} trades={n_trades} ret={ret:.1f}%"
        print(f"[{i:>3}/{len(tickers)}] {t:<8} {elapsed:>5.1f}s {status}{marker}")

    aggregate = _aggregate(results)
    elapsed_total = round(time.time() - t_start, 1)

    output = {
        "version": "r5_harness_mvp_v1",
        "generated_at": ts,
        "elapsed_total_s": elapsed_total,
        "params": {
            "horizon": args.horizon,
            "retrain_days": args.retrain_days,
            "commission": args.commission,
            "th_buy": args.th_buy,
            "th_sell": args.th_sell,
            "train_end": args.train_end,
        },
        "universe": {
            "n_tickers": len(tickers),
            "tickers": tickers,
            "source": "explicit" if args.tickers else (
                f"top_{args.top}" if args.top else f"coverage_min_{args.min_rows}"
            ),
        },
        "aggregate": aggregate,
        "per_ticker": results,
    }
    out_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")

    print()
    print("=== AGGREGATE ===")
    for k, v in aggregate.items():
        print(f"  {k:>30} = {v}")
    print()
    print(f"output saved: {out_path}")
    print(f"total elapsed: {elapsed_total}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
