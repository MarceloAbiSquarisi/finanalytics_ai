"""r5_rolling_origin.py — rolling-origin walk-forward com N folds.

Cada fold = 1 run completo do R5 harness com train_end diferente. Permite
testar se os top performers (CSMG3, BBSE3, BMEB4) são consistentes
across out-of-sample periods (skill) ou apenas sortudos no fold único
Jan/24-Apr/26 que rodamos até agora.

Config fixa: id=11 (h=10, retrain=63, vol=0.015) — deployment candidate
do sweep1 (sharpe=1.795, prob_trade=72%, n_trades=19).

Folds: cada um com train_end avançando ~6 meses, mantém features ≥2.5y
de train. Test windows overlap parcialmente (sliding-window vs strict
expanding-window — assumimos que features_daily não muda, ML modelo é
re-treinado a cada retrain_days dentro de cada fold).

  fold_2022_06: train_end=2022-06-30  test=2022-07 → 2026-04 (~3.8y)
  fold_2022_12: train_end=2022-12-31  test=2023-01 → 2026-04 (~3.3y)
  fold_2023_06: train_end=2023-06-30  test=2023-07 → 2026-04 (~2.8y)
  fold_2023_12: train_end=2023-12-31  test=2024-01 → 2026-04 (~2.3y)  ← já temos id=11
  fold_2024_06: train_end=2024-06-30  test=2024-07 → 2026-04 (~1.8y)

Pra cross-fold consistency, usar o test window comum (interseção =
2024-07 → 2026-04), filtrar trades dentro desse range. MVP simples:
deixar cada fold cobrir o que tem, agregador via median per-ticker.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

DEFAULTS = {
    "horizon": 10,
    "retrain-days": 63,
    "th-buy": 0.10,
    "th-sell": -0.10,
    "min-close": 1.0,
    "target-vol": 0.015,
}

FOLDS = [
    ("fold_2022_06", "2022-06-30"),
    ("fold_2022_12", "2022-12-31"),
    ("fold_2023_06", "2023-06-30"),
    # fold_2023_12 já existe como run id=11; vamos re-rodar pra padronizar
    ("fold_2023_12", "2023-12-31"),
    ("fold_2024_06", "2024-06-30"),
]

R5_PATH = "/tmp/r5.py"
INGEST_PATH = "/tmp/ingest.py"
OUT_DIR = Path("/tmp/backtest_runs")


def run_one(label: str, train_end: str) -> tuple[bool, float]:
    params = {**DEFAULTS, "train-end": train_end}
    cmd = ["python", "-u", R5_PATH, "--out-dir", str(OUT_DIR)]
    for k, v in params.items():
        cmd += [f"--{k}", str(v)]
    log_path = f"/tmp/rolling_{label}.log"
    print(f"\n=== [{label}] train_end={train_end} ===\n  log: {log_path}", flush=True)
    t0 = time.time()
    with open(log_path, "w") as f:
        cp = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    elapsed = time.time() - t0
    ok = cp.returncode == 0
    print(f"  {'OK' if ok else 'FAIL'} in {elapsed:.0f}s", flush=True)
    try:
        with open(log_path) as f:
            lines = f.readlines()
        agg_start = next((i for i, l in enumerate(lines) if "AGGREGATE" in l), -1)
        if agg_start >= 0:
            print("".join(lines[agg_start:agg_start + 18]), flush=True)
    except Exception:
        pass
    return ok, elapsed


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    print(f"R5 rolling-origin — {len(FOLDS)} folds (config: {DEFAULTS})", flush=True)
    pre = set(OUT_DIR.glob("r5_*.json"))
    t0 = time.time()
    for label, train_end in FOLDS:
        run_one(label, train_end)
    print(f"\n=== ROLLING DONE: {len(FOLDS)} folds in {(time.time()-t0)/60:.1f}min ===",
          flush=True)
    new_files = [f for f in sorted(OUT_DIR.glob("r5_*.json")) if f not in pre]
    print(f"\n=== Ingesting {len(new_files)} new runs ===", flush=True)
    if new_files:
        cp = subprocess.run(["python", INGEST_PATH] + [str(f) for f in new_files])
        print(f"  ingest rc={cp.returncode}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
