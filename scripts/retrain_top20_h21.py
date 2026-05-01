"""retrain_top20_h21.py — re-treina top-N MVPs no horizonte 21d (bate com calibracao).

Seleciona top-N por best_sharpe em ticker_ml_config e chama
train_petr4_mvp_v2.py --horizon 21 para cada um. Imprime resumo.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

import psycopg2

DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)


def top_tickers(n: int) -> list[str]:
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ticker FROM ticker_ml_config WHERE best_sharpe IS NOT NULL "
            "ORDER BY best_sharpe DESC LIMIT %s",
            (n,),
        )
        return [r[0] for r in cur.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--horizon", type=int, default=21)
    args = ap.parse_args()

    tickers = top_tickers(args.top)
    print(f"Re-treino top-{args.top} no horizonte {args.horizon}d: {tickers}")

    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "train_petr4_mvp_v2.py"
    python = Path(sys.executable)

    results: list[dict] = []
    for i, t in enumerate(tickers, 1):
        t0 = time.time()
        print(f"\n[{i}/{len(tickers)}] {t}")
        cmd = [str(python), str(script), "--ticker", t, "--horizon", str(args.horizon)]
        cp = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True)
        ok = cp.returncode == 0
        print(cp.stdout[-800:])
        if cp.stderr:
            print("  STDERR:", cp.stderr[-400:])
        results.append({"ticker": t, "ok": ok, "elapsed": round(time.time() - t0, 1)})

    print("\n=== RESUMO ===")
    ok_n = sum(1 for r in results if r["ok"])
    print(f"ok={ok_n}/{len(results)}")
    for r in results:
        print(f"  {r['ticker']:<7} {'OK' if r['ok'] else 'FAIL':<5} {r['elapsed']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
