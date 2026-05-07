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

import numpy as np
import psycopg2

from finanalytics_ai.domain.backtesting.metrics import (
    deflated_sharpe,
    expected_max_sharpe,
    sample_skew_kurtosis,
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


def _load_test_returns_matrix(
    tickers: list[str], train_end: str = "2023-12-31"
) -> tuple[list[str], np.ndarray]:
    """Carrega retornos diários do test window (close-to-close pct change).

    Returns:
      (kept_tickers, R) — R shape (T, K) com K = #tickers que tiveram dados
      suficientes (>= 50 dias). Datas alinhadas via inner-join em `dia`.
    """
    sql = (
        "SELECT dia, close FROM features_daily_full "
        "WHERE ticker = %s AND dia > %s ORDER BY dia ASC"
    )
    series: dict[str, list[tuple]] = {}
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        for t in tickers:
            cur.execute(sql, (t, train_end))
            rows = cur.fetchall()
            if len(rows) >= 50:
                series[t] = rows

    if not series:
        return [], np.zeros((0, 0))

    common_days = None
    for rows in series.values():
        days = {r[0] for r in rows}
        common_days = days if common_days is None else (common_days & days)
    if not common_days:
        return [], np.zeros((0, 0))
    days_sorted = sorted(common_days)

    kept: list[str] = []
    cols: list[list[float]] = []
    for t, rows in series.items():
        by_day = {r[0]: float(r[1]) for r in rows if r[1] is not None}
        closes = [by_day.get(d) for d in days_sorted]
        if any(c is None or c <= 0 for c in closes):
            continue
        rets = [
            float(np.log(closes[i] / closes[i - 1])) for i in range(1, len(closes))
        ]
        kept.append(t)
        cols.append(rets)

    if not cols:
        return [], np.zeros((0, 0))
    R = np.asarray(cols, dtype=float).T
    return kept, R


def _compute_neff(R: np.ndarray) -> dict[str, float | int]:
    """Estima N_eff (numero efetivo de trials independentes) de uma matriz
    de retornos correlacionados.

    Dois estimadores complementares:

    1. **Variance-based (Mertens / Bailey):**
         N_eff = N / (1 + ρ̄ · (N-1))
       Usa correlação média off-diagonal. Simples, pessimista quando ρ é
       heterogêneo.

    2. **Participation ratio (eigenvalue-based, LdP):**
         N_eff_eig = (Σ λᵢ)² / Σ λᵢ²   (= rank efetivo da matriz)
       Robusto a distribuições não-uniformes de correlação.
    """
    K = R.shape[1]
    if K < 2:
        return {"mean_corr": 0.0, "n_eff_var": float(K), "n_eff_eig": float(K), "n_raw": K}

    C = np.corrcoef(R, rowvar=False)
    mask = ~np.eye(K, dtype=bool)
    rho_bar = float(np.mean(C[mask]))
    rho_bar_clamped = max(0.0, min(0.999, rho_bar))

    n_eff_var = K / (1.0 + rho_bar_clamped * (K - 1))

    eigvals = np.linalg.eigvalsh(C)
    eigvals = np.clip(eigvals, 0.0, None)
    s = float(np.sum(eigvals))
    s2 = float(np.sum(eigvals**2))
    n_eff_eig = (s * s) / s2 if s2 > 0 else float(K)

    return {
        "mean_corr": round(rho_bar, 4),
        "n_eff_var": round(n_eff_var, 2),
        "n_eff_eig": round(n_eff_eig, 2),
        "n_raw": K,
    }


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


def _aggregate(
    results: list[dict],
    train_end: str = "2023-12-31",
    ann_factor: float = 252.0,
) -> dict:
    """Agrega métricas global + DSR sobre os N trials, com correção Neff.

    Por que Neff:
      Os N tickers do universo NÃO são trials independentes — ações B3 têm
      correlação ~0.3-0.5. DSR cru com N=87 estima E[max SR | H0] ≈ 2.48,
      um benchmark inalcançável. Corrigindo para N_eff (≈ 6-15 quando ρ̄≈0.4)
      o teste passa a ter poder estatístico realista.
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

    sharpe_max = max(sharpes)
    sharpe_median = statistics.median(sharpes)
    sharpe_std = statistics.pstdev(sharpes) if n > 1 else 0.0
    best_idx = sharpes.index(sharpe_max)
    best = valid[best_idx]
    best_ticker = best["ticker"]

    # === Neff correction ===
    tickers = [r["ticker"] for r in valid]
    neff_info: dict[str, float | int] = {
        "mean_corr": 0.0, "n_eff_var": float(n), "n_eff_eig": float(n), "n_raw": n,
    }
    R: np.ndarray = np.zeros((0, 0))
    try:
        kept, R = _load_test_returns_matrix(tickers, train_end=train_end)
        if R.size > 0:
            neff_info = _compute_neff(R)
    except Exception as exc:
        neff_info["error"] = str(exc)[:120]

    n_eff = int(round(max(2.0, float(neff_info.get("n_eff_eig", n)))))

    # === DSR proxy raw (N) e corrigido (N_eff) ===
    e_max_raw = expected_max_sharpe(n)
    e_max_neff = expected_max_sharpe(n_eff)
    dsr_proxy_raw = max(0.0, sharpe_max - e_max_raw) if n >= 2 else sharpe_max
    dsr_proxy_neff = max(0.0, sharpe_max - e_max_neff) if n_eff >= 2 else sharpe_max

    # === DSR completo no melhor ticker (com skew/kurt do underlying) ===
    dsr_best: dict = {}
    if R.size > 0 and best_ticker in tickers:
        try:
            col = tickers.index(best_ticker)
            ret_series = R[:, col].tolist() if col < R.shape[1] else []
            if len(ret_series) >= 30:
                skew, kurt = sample_skew_kurtosis(ret_series)
                T = len(ret_series)
                dsr_full = deflated_sharpe(
                    observed_sharpe=sharpe_max,
                    num_trials=n_eff,
                    sample_size=T,
                    skew=skew,
                    kurtosis=kurt,
                    annualization_factor=ann_factor,
                )
                dsr_best = dsr_full.to_dict()
        except Exception as exc:
            dsr_best = {"error": str(exc)[:120]}

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
        "expected_max_sharpe_raw": round(e_max_raw, 4),
        "expected_max_sharpe_neff": round(e_max_neff, 4),
        "dsr_proxy_raw_N": round(dsr_proxy_raw, 4),
        "dsr_proxy_neff": round(dsr_proxy_neff, 4),
        "neff": neff_info,
        "dsr_best_ticker_full": dsr_best,
        "n_negative_sharpe": sum(1 for s in sharpes if s < 0),
        "best_ticker": best_ticker,
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

    aggregate = _aggregate(results, train_end=args.train_end)
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
