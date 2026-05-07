"""r5_sweep.py — varre grid de hyperparams, gera N runs, ingesta no DB.

Cada run é um one-axis perturbation do baseline filter (th=0.10, retrain=63d,
h=21, target_vol=0.02). Permite analisar sensitivity: qual param mais
muda sharpe_max? Onde está o ótimo?

Uso:
  docker exec -e PROFIT_TIMESCALE_DSN=... -e OMP_NUM_THREADS=4 \
    finanalytics_api python /tmp/sweep.py

Sequencial — cada run é 1 thread-pool inteiro do LGBM. Paralelizar
levaria a contention pesada (ver feedback_zombie_python_container.md).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

# Defaults (baseline filter run id=2)
DEFAULTS = {
    "th-buy": 0.10,
    "th-sell": -0.10,
    "retrain-days": 63,
    "horizon": 21,
    "min-close": 1.0,
    "target-vol": 0.02,
    "train-end": "2023-12-31",
}

# Grid: cada entrada é (label, override_dict). Override merge sobre DEFAULTS.
GRID = [
    ("th_005",       {"th-buy": 0.05,  "th-sell": -0.05}),
    ("th_015",       {"th-buy": 0.15,  "th-sell": -0.15}),
    ("retrain_42",   {"retrain-days": 42}),
    ("retrain_126",  {"retrain-days": 126}),
    ("horizon_10",   {"horizon": 10}),
    ("horizon_42",   {"horizon": 42}),
    ("vol_015",      {"target-vol": 0.015}),
]

R5_PATH = "/tmp/r5.py"
INGEST_PATH = "/tmp/ingest.py"
OUT_DIR = Path("/tmp/backtest_runs")


def run_one(label: str, override: dict) -> tuple[bool, float, str]:
    params = {**DEFAULTS, **override}
    cmd = ["python", "-u", R5_PATH, "--out-dir", str(OUT_DIR)]
    for k, v in params.items():
        cmd += [f"--{k}", str(v)]

    log_path = f"/tmp/sweep_{label}.log"
    print(f"\n=== [{label}] ===", flush=True)
    print(f"  override: {override}", flush=True)
    print(f"  log: {log_path}", flush=True)

    t0 = time.time()
    with open(log_path, "w") as f:
        cp = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    elapsed = time.time() - t0

    # Pega aggregate do log
    last_lines = ""
    try:
        with open(log_path) as f:
            lines = f.readlines()
        # Pega da AGGREGATE até o final
        agg_start = next((i for i, l in enumerate(lines) if "AGGREGATE" in l), -1)
        if agg_start >= 0:
            last_lines = "".join(lines[agg_start:agg_start + 30])
    except Exception:
        pass

    ok = cp.returncode == 0
    print(f"  {'OK' if ok else 'FAIL'} in {elapsed:.0f}s (rc={cp.returncode})", flush=True)
    if last_lines:
        print(last_lines, flush=True)

    return ok, elapsed, log_path


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    print(f"R5 sweep — {len(GRID)} variants, defaults={DEFAULTS}", flush=True)
    t_start = time.time()

    pre_files = set(OUT_DIR.glob("r5_*.json"))

    results = []
    for label, override in GRID:
        ok, elapsed, log_path = run_one(label, override)
        results.append({"label": label, "ok": ok, "elapsed": elapsed, "log": log_path})

    elapsed_total = time.time() - t_start
    print(f"\n=== SWEEP DONE: {len(GRID)} runs in {elapsed_total/60:.1f}min ===", flush=True)
    print(f"  {sum(1 for r in results if r['ok'])} OK / {sum(1 for r in results if not r['ok'])} FAIL", flush=True)

    # Ingest novos JSONs
    new_files = sorted(OUT_DIR.glob("r5_*.json"))
    new_files_only = [f for f in new_files if f not in pre_files]
    print(f"\n=== Ingesting {len(new_files_only)} new runs ===", flush=True)
    if new_files_only:
        cmd = ["python", INGEST_PATH] + [str(f) for f in new_files_only]
        cp = subprocess.run(cmd)
        print(f"  ingest rc={cp.returncode}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
