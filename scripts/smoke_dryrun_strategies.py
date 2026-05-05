"""
smoke_dryrun_strategies.py — invoca cada strategy.evaluate() pros tickers
configurados e imprime a decisão (BUY/SELL/HOLD/SKIP) + payload.

Sem touch em container, sem grava DB. Pure isolation. Útil pra detectar
gaps de dado / SKIP por insufficient_bars antes de smoke live.

Uso:
    python scripts/smoke_dryrun_strategies.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

_env_file = ROOT / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            _k = _k.strip()
            _v = _v.strip().strip('"').strip("'")
            if _k not in os.environ:
                os.environ[_k] = _v

import psycopg2

from finanalytics_ai.workers.auto_trader_worker import STRATEGY_REGISTRY

DSN = os.getenv(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)


def fetch_enabled_strategies():
    rows = []
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, enabled, account_id, config_json
            FROM robot_strategies
            WHERE enabled = TRUE
            ORDER BY name
            """
        )
        for r in cur.fetchall():
            rows.append(
                {
                    "id": r[0],
                    "name": r[1],
                    "enabled": r[2],
                    "account_id": r[3],
                    "config": r[4] or {},
                }
            )
    return rows


def main() -> int:
    enabled = fetch_enabled_strategies()
    if not enabled:
        print("[!] Nenhuma strategy enabled em robot_strategies")
        return 1

    print(f"\n{'=' * 80}")
    print(f"DRY-RUN SMOKE: {len(enabled)} strategies enabled")
    print(f"{'=' * 80}\n")

    summary = {"BUY": 0, "SELL": 0, "HOLD": 0, "SKIP": 0, "ERROR": 0}

    for strat in enabled:
        impl = STRATEGY_REGISTRY.get(strat["name"])
        if impl is None:
            print(f"[X] {strat['name']}: NOT registered in STRATEGY_REGISTRY")
            summary["ERROR"] += 1
            continue

        tickers = strat["config"].get("tickers", [])
        print(f"--- {strat['name']} ({len(tickers)} tickers) ---")

        for ticker in tickers:
            try:
                result = impl.evaluate(ticker, strat["config"])
                action = result.get("action", "SKIP")
                payload = result.get("payload", {})
                summary[action] = summary.get(action, 0) + 1

                # Compact display: action + reason if SKIP, action + key fields if trade
                if action == "SKIP":
                    reason = payload.get("reason", "no_reason")
                    print(f"  [{action:5}] {ticker:8} reason={reason}")
                elif action in ("BUY", "SELL"):
                    qty = payload.get("quantity", "?")
                    price = payload.get("price", "market")
                    tp = payload.get("take_profit")
                    sl = payload.get("stop_loss")
                    print(
                        f"  [{action:5}] {ticker:8} qty={qty} "
                        f"px={price} tp={tp} sl={sl}"
                    )
                else:  # HOLD
                    print(f"  [{action:5}] {ticker:8}")
            except Exception as exc:
                summary["ERROR"] += 1
                print(f"  [ERROR] {ticker:8} exc={str(exc)[:120]}")

    print(f"\n{'=' * 80}")
    print(f"SUMMARY: {json.dumps(summary)}")
    print(f"{'=' * 80}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
