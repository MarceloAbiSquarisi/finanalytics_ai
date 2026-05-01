"""subscribe_watchlist_verde.py — subscreve todos os tickers VERDE da
watchlist no profit_agent. Idempotente.

Uso:
    python scripts/subscribe_watchlist_verde.py
    python scripts/subscribe_watchlist_verde.py --dry-run
    python scripts/subscribe_watchlist_verde.py --include-amarelo
    python scripts/subscribe_watchlist_verde.py --extra-futuros DI1F30,WDOFUT
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
import urllib.request

import psycopg2

DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)
AGENT_URL = os.environ.get("PROFIT_AGENT_URL", "http://localhost:8002")


def list_watchlist(include_amarelo: bool) -> list[str]:
    statuses = ["VERDE"] + (["AMARELO_parada_recente", "AMARELO"] if include_amarelo else [])
    statuses_csv = ",".join(f"'{s}'" for s in statuses)
    sql = f"""
        SELECT ticker FROM watchlist_tickers
         WHERE status IN ({statuses_csv})
         ORDER BY mediana_vol_brl DESC NULLS LAST
    """
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(sql)
        return [r[0] for r in cur.fetchall()]


def get_currently_subscribed() -> set[str]:
    try:
        with urllib.request.urlopen(f"{AGENT_URL}/status", timeout=10) as r:
            data = json.loads(r.read())
        return set(data.get("subscribed_tickers", []))
    except (URLError, HTTPError) as exc:
        print(f"FAIL /status: {exc}", file=sys.stderr)
        return set()


def subscribe(ticker: str, exchange: str) -> tuple[bool, str]:
    body = json.dumps({"ticker": ticker, "exchange": exchange}).encode("utf-8")
    req = urllib.request.Request(
        f"{AGENT_URL}/subscribe", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        return bool(data.get("ok")), str(data)[:120]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:80]}"


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--include-amarelo", action="store_true")
    p.add_argument("--extra-futuros", default="WINFUT,WDOFUT,DI1F27,DI1F28,DI1F29",
                   help="CSV de futuros (exchange F)")
    p.add_argument("--delay", type=float, default=0.3,
                   help="Delay entre subscribes (rate-limit)")
    p.add_argument("--max", type=int, default=0, help="0 = sem limite")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    stocks = list_watchlist(args.include_amarelo)
    if args.max:
        stocks = stocks[:args.max]
    futuros = [t.strip().upper() for t in args.extra_futuros.split(",") if t.strip()]

    plan = [(t, "B") for t in stocks] + [(t, "F") for t in futuros]
    print(f"Plano: {len(stocks)} stocks (B) + {len(futuros)} futuros (F) = {len(plan)} subscribes")

    current = get_currently_subscribed()
    print(f"Ja subscrito agora: {len(current)} tickers")

    pending = [(t, ex) for (t, ex) in plan if f"{t}:{ex}" not in current]
    print(f"Pendentes: {len(pending)}")

    if args.dry_run:
        print("[dry-run]")
        for t, ex in pending[:30]:
            print(f"  + {t}:{ex}")
        if len(pending) > 30:
            print(f"  ... +{len(pending) - 30}")
        return 0

    ok = fail = 0
    failures: list[tuple[str, str]] = []
    for i, (t, ex) in enumerate(pending, 1):
        success, msg = subscribe(t, ex)
        if success:
            ok += 1
            if i % 20 == 0:
                print(f"  [{i}/{len(pending)}] ok={ok} fail={fail}")
        else:
            fail += 1
            failures.append((t, msg))
            print(f"  FAIL {t}:{ex} -> {msg}")
        time.sleep(args.delay)

    print(f"\n=== RESUMO === ok={ok} fail={fail}")
    if failures:
        print("Falhas:")
        for t, m in failures[:20]:
            print(f"  {t}: {m}")

    final = get_currently_subscribed()
    print(f"\nTotal subscrito apos run: {len(final)}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
