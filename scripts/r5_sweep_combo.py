"""r5_sweep_combo.py — combina dimensões vencedoras do primeiro sweep.

Rodas:
  - retrain=42 + vol=0.015      (combina os 2 winners)
  - horizon=10 + vol=0.015       (h curto + defensive)
  - horizon=42 + vol=0.015       (h longo + defensive — verifica se dd cai)

Uso:
  docker exec -e PROFIT_TIMESCALE_DSN=... -e OMP_NUM_THREADS=4 \
    finanalytics_api python /tmp/sweep2.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

DEFAULTS = {
    "th-buy": 0.10,
    "th-sell": -0.10,
    "retrain-days": 63,
    "horizon": 21,
    "min-close": 1.0,
    "target-vol": 0.02,
    "train-end": "2023-12-31",
}

GRID = [
    ("retr42_vol015",   {"retrain-days": 42, "target-vol": 0.015}),
    ("h10_vol015",      {"horizon": 10,      "target-vol": 0.015}),
    ("h42_vol015",      {"horizon": 42,      "target-vol": 0.015}),
]

R5_PATH = "/tmp/r5.py"
INGEST_PATH = "/tmp/ingest.py"
OUT_DIR = Path("/tmp/backtest_runs")


def run_one(label: str, override: dict) -> tuple[bool, float]:
    params = {**DEFAULTS, **override}
    cmd = ["python", "-u", R5_PATH, "--out-dir", str(OUT_DIR)]
    for k, v in params.items():
        cmd += [f"--{k}", str(v)]
    log_path = f"/tmp/sweep2_{label}.log"
    print(f"\n=== [{label}] ===\n  override: {override}\n  log: {log_path}", flush=True)
    t0 = time.time()
    with open(log_path, "w") as f:
        cp = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    elapsed = time.time() - t0
    ok = cp.returncode == 0
    print(f"  {'OK' if ok else 'FAIL'} in {elapsed:.0f}s", flush=True)

    # Print aggregate
    try:
        with open(log_path) as f:
            lines = f.readlines()
        agg_start = next((i for i, l in enumerate(lines) if "AGGREGATE" in l), -1)
        if agg_start >= 0:
            print("".join(lines[agg_start:agg_start + 25]), flush=True)
    except Exception:
        pass
    return ok, elapsed


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    print(f"R5 sweep #2 (combo) — {len(GRID)} variants", flush=True)
    pre = set(OUT_DIR.glob("r5_*.json"))
    t0 = time.time()
    for label, override in GRID:
        run_one(label, override)
    print(f"\n=== SWEEP2 DONE: {len(GRID)} runs in {(time.time()-t0)/60:.1f}min ===", flush=True)
    new_files = [f for f in sorted(OUT_DIR.glob("r5_*.json")) if f not in pre]
    print(f"\n=== Ingesting {len(new_files)} new runs ===", flush=True)
    if new_files:
        cp = subprocess.run(["python", INGEST_PATH] + [str(f) for f in new_files])
        print(f"  ingest rc={cp.returncode}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
