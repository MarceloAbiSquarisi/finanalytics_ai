"""r5_ingest.py — carrega JSON do r5_harness em r5_runs + r5_ticker_results.

Idempotência: se um registro com mesmo `generated_at` (timestamp UTC do
ts gerado pelo harness, com precisão de segundo) já existir, faz UPDATE;
senão INSERT.

Uso:
  python scripts/r5_ingest.py backtest_runs/r5_20260507_180057.json
  python scripts/r5_ingest.py backtest_runs/*.json   # ingest em batch
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import glob
import json
import os
import sys

import psycopg2
import psycopg2.extras

DSN = os.environ.get(
    "FINANALYTICS_DSN",
    "postgresql://finanalytics:secret@localhost:5432/finanalytics",
)


def _parse_generated_at(s: str) -> datetime:
    """'20260507_180057' → datetime(UTC)."""
    return datetime.strptime(s, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)


def ingest(path: str, conn) -> int:
    """Retorna run_id (existente ou novo)."""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)

    generated_at = _parse_generated_at(d["generated_at"])
    p = d.get("params", {})
    a = d.get("aggregate", {})
    neff = a.get("neff", {})
    dsr = a.get("dsr_best_ticker_full", {}) or {}

    headers = {
        "generated_at": generated_at,
        "version": d.get("version"),
        "elapsed_total_s": d.get("elapsed_total_s"),
        "horizon": p.get("horizon"),
        "retrain_days": p.get("retrain_days"),
        "commission": p.get("commission"),
        "th_buy": p.get("th_buy"),
        "th_sell": p.get("th_sell"),
        "train_end": p.get("train_end"),
        "min_close": p.get("min_close"),
        "target_vol": p.get("target_vol"),
        "vol_pos_floor": p.get("vol_pos_floor"),
        "vol_pos_cap": p.get("vol_pos_cap"),
        "n_valid": a.get("n_valid"),
        "n_total": a.get("n_total"),
        "n_trades_total": a.get("n_trades_total"),
        "sharpe_avg": a.get("sharpe_avg"),
        "sharpe_median": a.get("sharpe_median"),
        "sharpe_std": a.get("sharpe_std"),
        "sharpe_max": a.get("sharpe_max"),
        "sharpe_min": a.get("sharpe_min"),
        "drawdown_avg": a.get("drawdown_avg"),
        "drawdown_max": a.get("drawdown_max"),
        "win_rate_avg": a.get("win_rate_avg"),
        "return_avg": a.get("return_avg"),
        "return_median": a.get("return_median"),
        "return_total_sum": a.get("return_total_sum"),
        "n_negative_sharpe": a.get("n_negative_sharpe"),
        "best_ticker": a.get("best_ticker"),
        "worst_ticker": a.get("worst_ticker"),
        "mean_corr": neff.get("mean_corr"),
        "n_eff_var": neff.get("n_eff_var"),
        "n_eff_eig": neff.get("n_eff_eig"),
        "n_raw": neff.get("n_raw"),
        "expected_max_sharpe_raw": (
            a.get("expected_max_sharpe_raw")
            or a.get("expected_max_sharpe_under_null")  # baseline schema
        ),
        "expected_max_sharpe_neff": a.get("expected_max_sharpe_neff"),
        "dsr_proxy_raw_n": a.get("dsr_proxy_raw_N") or a.get("dsr_top1_proxy"),
        "dsr_proxy_neff": a.get("dsr_proxy_neff"),
        "dsr_full_observed_sharpe": dsr.get("observed_sharpe"),
        "dsr_full_z": dsr.get("deflated_sharpe"),
        "dsr_full_prob_real": dsr.get("prob_real"),
        "dsr_full_e_max": dsr.get("e_max_sharpe"),
        "dsr_full_num_trials": dsr.get("num_trials"),
        "dsr_full_sample_size": dsr.get("sample_size"),
        "dsr_full_skew": dsr.get("skew"),
        "dsr_full_kurtosis": dsr.get("kurtosis"),
        "raw_payload": json.dumps({"universe": d.get("universe"), "aggregate": a}),
    }

    cols = list(headers.keys())
    placeholders = ", ".join(f"%({c})s" for c in cols)
    update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "generated_at")

    sql = (
        f"INSERT INTO r5_runs ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT (generated_at) DO UPDATE SET {update_set} "
        f"RETURNING id"
    )

    # generated_at não é UNIQUE no schema; precisa fallback manual
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM r5_runs WHERE generated_at = %s",
            (generated_at,),
        )
        row = cur.fetchone()
        if row:
            run_id = row[0]
            update_cols = [c for c in cols if c != "generated_at"]
            set_clause = ", ".join(f"{c} = %({c})s" for c in update_cols)
            cur.execute(
                f"UPDATE r5_runs SET {set_clause} WHERE id = {run_id}",
                headers,
            )
            cur.execute("DELETE FROM r5_ticker_results WHERE run_id = %s", (run_id,))
            print(f"[update] run_id={run_id} ({path})")
        else:
            cols_no_id = list(headers.keys())
            ph = ", ".join(f"%({c})s" for c in cols_no_id)
            cur.execute(
                f"INSERT INTO r5_runs ({', '.join(cols_no_id)}) VALUES ({ph}) RETURNING id",
                headers,
            )
            run_id = cur.fetchone()[0]
            print(f"[insert] run_id={run_id} ({path})")

    # Per-ticker batch insert
    rows: list[dict] = []
    for r in d.get("per_ticker", []):
        m = r.get("metrics") or {}
        rows.append({
            "run_id": run_id,
            "ticker": r["ticker"],
            "ok": r.get("ok", True),
            "error": r.get("error"),
            "trades": r.get("trades"),
            "winners": r.get("winners"),
            "test_len": r.get("test_len"),
            "retrains": r.get("retrains"),
            "horizon": r.get("horizon"),
            "elapsed_s": r.get("elapsed_s"),
            "sharpe_ratio": m.get("sharpe_ratio"),
            "total_return_pct": m.get("total_return_pct"),
            "max_drawdown_pct": m.get("max_drawdown_pct"),
            "win_rate_pct": m.get("win_rate_pct"),
            "profit_factor": m.get("profit_factor"),
            "calmar_ratio": m.get("calmar_ratio"),
            "avg_win_pct": m.get("avg_win_pct"),
            "avg_loss_pct": m.get("avg_loss_pct"),
            "avg_duration_days": m.get("avg_duration_days"),
            "final_equity": m.get("final_equity"),
            "position_size": r.get("position_size"),
            "train_median_close": r.get("train_median_close"),
            "train_mean_vol_21d": r.get("train_mean_vol_21d"),
            "raw_metrics": json.dumps(m) if m else None,
        })

    if rows:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO r5_ticker_results (
                    run_id, ticker, ok, error, trades, winners, test_len, retrains, horizon,
                    elapsed_s, sharpe_ratio, total_return_pct, max_drawdown_pct, win_rate_pct,
                    profit_factor, calmar_ratio, avg_win_pct, avg_loss_pct, avg_duration_days,
                    final_equity, position_size, train_median_close, train_mean_vol_21d, raw_metrics
                ) VALUES %s""",
                [
                    (
                        r["run_id"], r["ticker"], r["ok"], r["error"], r["trades"], r["winners"],
                        r["test_len"], r["retrains"], r["horizon"], r["elapsed_s"], r["sharpe_ratio"],
                        r["total_return_pct"], r["max_drawdown_pct"], r["win_rate_pct"],
                        r["profit_factor"], r["calmar_ratio"], r["avg_win_pct"], r["avg_loss_pct"],
                        r["avg_duration_days"], r["final_equity"], r["position_size"],
                        r["train_median_close"], r["train_mean_vol_21d"], r["raw_metrics"],
                    )
                    for r in rows
                ],
            )

    print(f"  -> {len(rows)} ticker rows")
    return run_id


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", help="JSON files (glob OK)")
    args = ap.parse_args()

    expanded: list[str] = []
    for p in args.paths:
        matches = glob.glob(p)
        if matches:
            expanded.extend(matches)
        else:
            expanded.append(p)

    with psycopg2.connect(DSN) as conn:
        for path in expanded:
            try:
                ingest(path, conn)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                print(f"FAIL {path}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
