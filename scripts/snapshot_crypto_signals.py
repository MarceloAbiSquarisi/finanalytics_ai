"""
snapshot_crypto_signals.py — N6 (28/abr/2026)

Snapshot diario do /api/v1/crypto/signal para uma lista de symbols. Persiste
em `crypto_signals_history` (PK symbol+snapshot_date+vs_currency, idempotente).

Permite analisar evolucao do score ao longo do tempo + filtros multi-horizon
(via janela de N dias do historico).

Uso:
    python scripts/snapshot_crypto_signals.py
    python scripts/snapshot_crypto_signals.py --symbols BTC,ETH
    python scripts/snapshot_crypto_signals.py --vs-currency brl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

import psycopg2

DSN = (
    os.environ.get("TIMESCALE_URL")
    or os.environ.get("PROFIT_TIMESCALE_DSN")
    or "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
)
API_BASE = os.environ.get("FINANALYTICS_API_BASE", "http://localhost:8000")

DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA"]


_SQL_UPSERT = """
INSERT INTO crypto_signals_history
    (symbol, snapshot_date, signal, score, current_price, rsi, macd_hist,
     ema9, ema21, bb_upper, bb_lower, vs_currency, snapshot_at)
VALUES (%s, CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
ON CONFLICT (symbol, snapshot_date, vs_currency) DO UPDATE SET
    signal=EXCLUDED.signal,
    score=EXCLUDED.score,
    current_price=EXCLUDED.current_price,
    rsi=EXCLUDED.rsi,
    macd_hist=EXCLUDED.macd_hist,
    ema9=EXCLUDED.ema9,
    ema21=EXCLUDED.ema21,
    bb_upper=EXCLUDED.bb_upper,
    bb_lower=EXCLUDED.bb_lower,
    snapshot_at=NOW()
"""


def fetch_signal(symbol: str, vs_currency: str) -> dict | None:
    url = f"{API_BASE}/api/v1/crypto/signal/{symbol}?vs_currency={vs_currency}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"  [{symbol}] http error: {exc}")
        return None


def upsert(conn, sym: str, vs_currency: str, data: dict) -> None:
    ind = data.get("indicators", {}) or {}
    cur = conn.cursor()
    cur.execute(
        _SQL_UPSERT,
        (
            sym,
            data.get("signal"),
            data.get("score"),
            data.get("current_price"),
            ind.get("rsi"),
            ind.get("macd_hist"),
            ind.get("ema9"),
            ind.get("ema21"),
            ind.get("bb_upper"),
            ind.get("bb_lower"),
            vs_currency,
        ),
    )
    conn.commit()
    cur.close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default=None, help="CSV (default = BTC,ETH,SOL,BNB,XRP,ADA)")
    p.add_argument("--vs-currency", default="usd", choices=["usd", "brl"])
    p.add_argument("--rate-limit", type=float, default=2.0,
                   help="Segundos entre requests (CoinGecko free=30 req/min)")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else DEFAULT_SYMBOLS
    )

    print(f"[snapshot_crypto_signals] {len(symbols)} symbols vs {args.vs_currency} dry={args.dry_run}")

    conn = None if args.dry_run else psycopg2.connect(DSN)
    try:
        ok = 0
        fail = 0
        for i, sym in enumerate(symbols, 1):
            t0 = time.time()
            data = fetch_signal(sym, args.vs_currency)
            if not data:
                fail += 1
                continue
            sig = data.get("signal")
            score = data.get("score")
            price = data.get("current_price")
            print(f"  [{i}/{len(symbols)}] {sym}: sig={sig} score={score} price={price} ({time.time()-t0:.2f}s)")
            if not args.dry_run:
                upsert(conn, sym, args.vs_currency, data)
            ok += 1
            if i < len(symbols):
                time.sleep(args.rate_limit)
    finally:
        if conn is not None:
            conn.close()

    print(f"[snapshot_crypto_signals] done | ok={ok} fail={fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
