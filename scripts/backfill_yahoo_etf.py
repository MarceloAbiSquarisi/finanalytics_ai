"""
backfill_yahoo_etf.py — backfill diário de ETFs brasileiros via Yahoo Finance.

Mesma estratégia do backfill_yahoo_fii.py: baixa OHLCV via yfinance,
reusa `compute_features_for_ticker` do builder, popula `features_daily`
com source='yahoo_etf'.

Uso:
    python scripts/backfill_yahoo_etf.py                   # 2 anos default
    python scripts/backfill_yahoo_etf.py --years 5
    python scripts/backfill_yahoo_etf.py --tickers BOVA11,IVVB11

Pós-backfill:
    python scripts/calibrate_ml_thresholds.py --tickers BOVA11,...
    python scripts/train_petr4_mvp_v2.py --ticker BOVA11 --no-rf --horizon 21 \\
        --train-start 2024-01-01 --train-end 2025-04-30 \\
        --val-start 2025-05-01 --val-end 2025-08-31 --test-start 2025-09-01
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import os
from pathlib import Path
import sys
import time

import psycopg2

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "src"))

# Reusa pipeline (compute features + upsert) e fetcher Yahoo do FII
from backfill_yahoo_fii import fetch_yahoo_daily
from features_daily_builder import (
    compute_features_for_ticker,
    upsert_features,
)

DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)


# ETFs B3 mais líquidos (verificar lista periodicamente em b3.com.br/listed/etfs)
ETFS_BR_TOP = [
    # IBOV (cuidado: BOVA11 ≈ IBOV — sinal pode ser redundante com IBOV uptrend/downtrend)
    "BOVA11", "BOVV11", "BOVB11",
    # Internacional
    "IVVB11", "USPD11", "NASD11",
    # Setoriais
    "SMAL11", "DIVO11", "FIND11", "MATB11", "GOVE11",
    # Commodities / Outros
    "GOLD11", "ECOO11",
    # Renda Fixa (alguns)
    "B5P211", "IMAB11",
]
# Dedup preservando ordem
_seen: set[str] = set()
ETFS_BR_TOP = [t for t in ETFS_BR_TOP if not (t in _seen or _seen.add(t))]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--years", type=int, default=2)
    p.add_argument("--tickers", type=str, default=None)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    tickers = (
        [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if args.tickers
        else ETFS_BR_TOP
    )
    end = date.today()
    start = end - timedelta(days=args.years * 365 + 30)

    print(f"[backfill_yahoo_etf] {len(tickers)} ETFs · {start} → {end}")

    conn = psycopg2.connect(DSN)
    try:
        for i, t in enumerate(tickers, 1):
            t0 = time.time()
            try:
                bars = fetch_yahoo_daily(t, start, end)
            except Exception as exc:
                print(f"  [{i:2d}/{len(tickers)}] {t}: ERRO yahoo {exc}")
                continue
            if len(bars) < 60:
                print(f"  [{i:2d}/{len(tickers)}] {t}: SKIP (<60 bars: {len(bars)})")
                continue
            # Source='yahoo_etf' (sobrescreve 'yahoo_fii' se houver overlap, ex: dual-listed)
            for b in bars:
                b.source = "yahoo_etf"
            rows = compute_features_for_ticker(bars)
            for r in rows:
                r["source"] = "yahoo_etf"
            if args.dry_run:
                print(f"  [{i:2d}/{len(tickers)}] {t}: bars={len(bars)} feats={len(rows)} (dry-run)")
                continue
            n = upsert_features(conn, t, rows)
            conn.commit()
            print(
                f"  [{i:2d}/{len(tickers)}] {t}: bars={len(bars)} "
                f"feats={n} ({time.time()-t0:.1f}s)"
            )
    finally:
        conn.close()
    print("[backfill_yahoo_etf] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
