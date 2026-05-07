"""r5_rolling_analyze.py — agrega resultados de N folds rolling-origin.

Métricas que importam pra confiabilidade out-of-sample:

  1. Per-ticker MEDIAN sharpe across folds — robusto a 1 fold sortudo
  2. Per-ticker COUNT folds com sharpe > 0 — consistência
  3. Per-ticker COUNT folds em top-10 — winners persistentes
  4. Aggregate: stability of best_ticker, sharpe_max, drawdown across folds

Filtra runs com mesma config (target_vol, horizon, retrain_days), só
mudança é train_end.

Uso:
  python scripts/r5_rolling_analyze.py
"""
from __future__ import annotations

import os
import statistics
import sys

import psycopg2

DSN = os.environ.get(
    "FINANALYTICS_DSN",
    "postgresql://finanalytics:secret@postgres:5432/finanalytics",
)


def main() -> int:
    with psycopg2.connect(DSN) as c:
        cur = c.cursor()

        # Identifica grupo rolling: target_vol=0.015, horizon=10, retrain=63
        cur.execute("""
            SELECT id, train_end, sharpe_max, sharpe_avg, drawdown_avg,
                   drawdown_max, return_total_sum, best_ticker,
                   dsr_full_prob_real, n_eff_eig
            FROM r5_runs
            WHERE target_vol = 0.015 AND horizon = 10 AND retrain_days = 63
            ORDER BY train_end ASC
        """)
        runs = cur.fetchall()

    if not runs:
        print("Nenhum run rolling encontrado (filtro: tvol=0.015 h=10 retr=63).")
        return 1

    print(f"=== {len(runs)} folds rolling-origin ===\n")
    print(f"{'fold_id':<8} {'train_end':<12} {'sh_max':<7} {'sh_avg':<7} "
          f"{'dd_avg':<7} {'best':<8} {'prob':<6}")
    print("-" * 75)
    fold_ids = []
    for r in runs:
        rid, te, smax, sa, da, dm, rs, bt, pr, neff = r
        pr_s = f"{float(pr):.2f}" if pr else "—"
        print(f"{rid:<8} {str(te):<12} {float(smax):>6.3f}  {float(sa):>6.3f}  "
              f"{float(da):>6.2f}  {bt or '—':<8} {pr_s}")
        fold_ids.append(rid)

    # Per-ticker cross-fold
    with psycopg2.connect(DSN) as c:
        cur = c.cursor()
        cur.execute(f"""
            SELECT ticker, run_id, sharpe_ratio, total_return_pct,
                   max_drawdown_pct, win_rate_pct, trades
            FROM r5_ticker_results
            WHERE run_id = ANY(%s) AND ok
        """, (fold_ids,))
        rows = cur.fetchall()

    by_ticker: dict[str, list[dict]] = {}
    for r in rows:
        t = r[0]
        by_ticker.setdefault(t, []).append({
            "run": r[1], "sharpe": float(r[2]) if r[2] else 0,
            "ret": float(r[3]) if r[3] else 0,
            "dd": float(r[4]) if r[4] else 0,
            "win": float(r[5]) if r[5] else 0,
            "trades": int(r[6]) if r[6] else 0,
        })

    n_folds = len(fold_ids)

    # Score cada ticker
    tickers_score = []
    for t, runs_list in by_ticker.items():
        if len(runs_list) < n_folds:
            continue  # ticker não passou em todos os folds
        sharpes = [r["sharpe"] for r in runs_list]
        rets = [r["ret"] for r in runs_list]
        dds = [r["dd"] for r in runs_list]
        n_pos = sum(1 for s in sharpes if s > 0)
        n_strong = sum(1 for s in sharpes if s > 0.5)
        tickers_score.append({
            "ticker": t,
            "median_sharpe": round(statistics.median(sharpes), 3),
            "min_sharpe": round(min(sharpes), 3),
            "max_sharpe": round(max(sharpes), 3),
            "n_pos": n_pos,
            "n_strong": n_strong,
            "median_ret": round(statistics.median(rets), 1),
            "median_dd": round(statistics.median(dds), 1),
            "consistency_score": n_strong / n_folds,
        })

    # Top consistentes
    print(f"\n=== TOP 15 CONSISTENT (median sharpe DESC) ===")
    print(f"{'ticker':<8} {'med_sh':<7} {'min':<6} {'max':<6} "
          f"{'n_pos':<6} {'n_strong':<8} {'med_ret':<8} {'med_dd':<7}")
    print("-" * 80)
    by_med = sorted(tickers_score, key=lambda x: x["median_sharpe"], reverse=True)
    for s in by_med[:15]:
        print(f"{s['ticker']:<8} {s['median_sharpe']:>+6.3f}  "
              f"{s['min_sharpe']:>+5.2f}  {s['max_sharpe']:>+5.2f}  "
              f"{s['n_pos']:>2}/{n_folds}    {s['n_strong']:>2}/{n_folds}      "
              f"{s['median_ret']:>+7.1f}  {s['median_dd']:>6.1f}")

    print(f"\n=== TICKERS WITH ALL {n_folds} FOLDS sharpe > 0.5 (strong winners) ===")
    strong_all = [s for s in tickers_score if s["n_strong"] == n_folds]
    if strong_all:
        for s in sorted(strong_all, key=lambda x: x["median_sharpe"], reverse=True):
            print(f"  {s['ticker']:<8} med_sharpe={s['median_sharpe']:+.3f}  "
                  f"range=[{s['min_sharpe']:+.2f}, {s['max_sharpe']:+.2f}]  "
                  f"med_ret={s['median_ret']:+.1f}%")
    else:
        print("  (nenhum)")

    print(f"\n=== TOP 10 PER FOLD (intersection) ===")
    by_fold_top: dict[int, set] = {}
    for r in rows:
        run_id, ticker, sharpe = r[1], r[0], float(r[2]) if r[2] else 0
        by_fold_top.setdefault(run_id, set())
    # Re-fetch para top-10
    for fid in fold_ids:
        with psycopg2.connect(DSN) as c:
            cur = c.cursor()
            cur.execute("""SELECT ticker FROM r5_ticker_results
                           WHERE run_id=%s AND ok
                           ORDER BY sharpe_ratio DESC NULLS LAST LIMIT 10""", (fid,))
            by_fold_top[fid] = set(r[0] for r in cur.fetchall())
    intersection = set.intersection(*by_fold_top.values()) if len(by_fold_top) >= 2 else set()
    print(f"  presentes no top-10 de TODOS os {n_folds} folds: {sorted(intersection) or '(nenhum)'}")
    union = set().union(*by_fold_top.values())
    print(f"  total tickers únicos no top-10 (qualquer fold): {len(union)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
