"""
snapshot_signals.py — captura snapshot diario de /api/v1/ml/signals e
persiste em signal_history. Detecta tickers que mudaram de signal vs
snapshot anterior.

Uso:
    python scripts/snapshot_signals.py
    python scripts/snapshot_signals.py --min-sharpe 1.0
    python scripts/snapshot_signals.py --date 2026-04-20  # forca data
    python scripts/snapshot_signals.py --dry-run

Saida:
    [OK] inserted=N changed=M new=K (vs snapshot anterior)
    Lista de mudancas: ticker | prev -> curr | sharpe
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
import json
import os
import sys
from typing import Any
import urllib.request

import psycopg2

DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)
API_URL = os.environ.get("FINANALYTICS_API_URL", "http://localhost:8000")


def fetch_signals(min_sharpe: float | None, limit: int = 500) -> dict[str, Any]:
    qs = f"limit={limit}"
    if min_sharpe is not None:
        qs += f"&min_sharpe={min_sharpe}"
    url = f"{API_URL}/api/v1/ml/signals?{qs}"
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.loads(r.read())


def upsert_snapshot(conn, snap_date: date, items: list[dict]) -> int:
    sql = """
    INSERT INTO signal_history
        (snapshot_date, ticker, signal, predicted_log_return,
         predicted_return_pct, reference_date, th_buy, th_sell,
         horizon_days, best_sharpe, signal_method, model_file)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT (snapshot_date, ticker) DO UPDATE SET
        signal=EXCLUDED.signal,
        predicted_log_return=EXCLUDED.predicted_log_return,
        predicted_return_pct=EXCLUDED.predicted_return_pct,
        reference_date=EXCLUDED.reference_date,
        th_buy=EXCLUDED.th_buy, th_sell=EXCLUDED.th_sell,
        horizon_days=EXCLUDED.horizon_days, best_sharpe=EXCLUDED.best_sharpe,
        signal_method=EXCLUDED.signal_method, model_file=EXCLUDED.model_file,
        captured_at=now()
    """
    n = 0
    with conn.cursor() as cur:
        for it in items:
            if not it.get("signal"):
                continue
            ref_date = it.get("reference_date")
            if isinstance(ref_date, str):
                try:
                    ref_date = date.fromisoformat(ref_date)
                except ValueError:
                    ref_date = None
            cur.execute(
                sql,
                (
                    snap_date,
                    it["ticker"],
                    it["signal"],
                    it.get("predicted_log_return"),
                    it.get("predicted_return_pct"),
                    ref_date,
                    it.get("th_buy"),
                    it.get("th_sell"),
                    it.get("horizon_days"),
                    it.get("best_sharpe"),
                    it.get("signal_method"),
                    it.get("model_file"),
                ),
            )
            n += 1
    return n


def detect_changes(conn, snap_date: date) -> list[dict]:
    """Compara snapshot atual vs snapshot anterior mais recente."""
    sql = """
    WITH prev AS (
      SELECT DISTINCT ON (ticker) ticker, signal AS prev_signal,
             snapshot_date AS prev_date
        FROM signal_history
       WHERE snapshot_date < %s
       ORDER BY ticker, snapshot_date DESC
    ),
    curr AS (
      SELECT ticker, signal AS curr_signal, best_sharpe
        FROM signal_history
       WHERE snapshot_date = %s
    )
    SELECT c.ticker, p.prev_signal, c.curr_signal, c.best_sharpe, p.prev_date
      FROM curr c LEFT JOIN prev p ON p.ticker = c.ticker
     WHERE p.prev_signal IS DISTINCT FROM c.curr_signal
     ORDER BY c.best_sharpe DESC NULLS LAST
    """
    with conn.cursor() as cur:
        cur.execute(sql, (snap_date, snap_date))
        rows = cur.fetchall()
    return [
        {
            "ticker": r[0],
            "prev": r[1],
            "curr": r[2],
            "sharpe": float(r[3]) if r[3] is not None else None,
            "prev_date": r[4],
        }
        for r in rows
    ]


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--min-sharpe", type=float, default=None)
    p.add_argument("--date", default=None, help="YYYY-MM-DD; default hoje")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=500)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    snap_date = date.fromisoformat(args.date) if args.date else date.today()

    print(f"snapshot_signals: date={snap_date} min_sharpe={args.min_sharpe} dry={args.dry_run}")
    try:
        data = fetch_signals(args.min_sharpe, args.limit)
    except Exception as exc:
        print(f"FAIL fetch /signals: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    counts = {k: data.get(k) for k in ["count", "buy", "sell", "hold", "errors"]}
    print(f"  api: {counts}")
    items = [i for i in data.get("items", []) if i.get("signal")]
    print(f"  items com signal: {len(items)}")

    if args.dry_run:
        print("[dry-run] nao gravando.")
        return 0

    conn = psycopg2.connect(DSN)
    try:
        n = upsert_snapshot(conn, snap_date, items)
        conn.commit()
        changes = detect_changes(conn, snap_date)
        print(f"[OK] inserted={n}  changes={len(changes)}")
        if changes:
            print("\n  Mudancas vs snapshot anterior:")
            for c in changes[:30]:
                prev = c["prev"] or "NEW"
                s = f"  {c['ticker']:<7} {prev:>4} -> {c['curr']:<4}"
                if c["sharpe"] is not None:
                    s += f"  sharpe={c['sharpe']:+.2f}"
                if c["prev_date"]:
                    s += f"  (anterior {c['prev_date']})"
                print(s)
            if len(changes) > 30:
                print(f"  ... +{len(changes) - 30} mudancas")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
