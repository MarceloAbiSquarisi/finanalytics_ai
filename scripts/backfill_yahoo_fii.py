"""
backfill_yahoo_fii.py — backfill diário de FIIs via Yahoo Finance
+ computa features e popula `features_daily` com source='yahoo_fii'.

Motivo: FIIs reais (KNRI11, MXRF11, HGLG11, etc) NÃO estão em
`fintz_cotacoes_ts` (Fintz só cobre units de empresas) nem em
`profit_daily_bars` (DLL Profit não subscrita). Yahoo (KNRI11.SA)
é a fonte mais robusta — Decisão 20 já permite Yahoo como camada 2.

Estratégia:
  1. Pra cada FII em IFIX_TOP_30, baixa daily Yahoo via yfinance.
  2. Reusa `compute_features_for_ticker` do features_daily_builder
     (mesmas técnicas dos demais tickers — atr_14, sma_50, rsi_14, etc).
  3. UPSERT em `features_daily` com source='yahoo_fii'.

Uso:
    python scripts/backfill_yahoo_fii.py                  # 1 ano default
    python scripts/backfill_yahoo_fii.py --years 3
    python scripts/backfill_yahoo_fii.py --tickers KNRI11,MXRF11

Após rodar:
    python scripts/calibrate_ml_thresholds.py --tickers KNRI11,...
    (calibrate já lê features_daily_full; vai pegar os FIIs novos)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg2
import psycopg2.extras

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "src"))

# Reusa pipeline de features e upsert do builder existente.
from features_daily_builder import (  # noqa: E402
    Bar,
    compute_features_for_ticker,
    upsert_features,
)


DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)


# Top 30 FIIs IFIX (por liquidez histórica — verificar periodicamente em b3.com.br)
IFIX_TOP_30 = [
    # Logística
    "HGLG11", "BTLG11", "VILG11", "XPLG11", "BRCO11",
    # Shoppings
    "XPML11", "VISC11", "HGBS11", "MALL11", "VRTA11",
    # Lajes / Corp
    "KNRI11", "HGRE11", "PVBI11", "RCRB11", "BRCR11",
    # Híbridos / TIJOLO
    "HGRU11", "RECT11", "RBRR11",
    # Papel / CRI
    "MXRF11", "BCFF11", "RBRF11", "HCTR11", "VGIR11",
    "VGIP11", "RBRY11", "KNCR11", "KNHY11",
    # Fundos de fundos
    "BCFF11", "RBRF11", "HFOF11",
]
# remove duplicados preservando ordem
_seen = set()
IFIX_TOP_30 = [t for t in IFIX_TOP_30 if not (t in _seen or _seen.add(t))]


def fetch_yahoo_daily(ticker: str, start: date, end: date) -> list[Bar]:
    """Baixa daily OHLCV via yfinance. Retorna list[Bar] do builder."""
    import yfinance as yf

    yf_sym = f"{ticker}.SA"
    df = yf.download(
        yf_sym,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        progress=False,
        auto_adjust=False,  # mantém close não ajustado (consistente com fintz)
        threads=False,
    )
    if df is None or df.empty:
        return []
    # yfinance retorna MultiIndex em colunas em algumas versões
    if hasattr(df.columns, "levels"):
        df.columns = [c[0] for c in df.columns]
    bars: list[Bar] = []
    for idx, row in df.iterrows():
        try:
            d = idx.date() if hasattr(idx, "date") else idx
            close = float(row["Close"])
            high = float(row["High"])
            low = float(row["Low"])
            vol = float(row.get("Volume", 0) or 0)
        except (KeyError, ValueError, TypeError):
            continue
        if close <= 0 or high <= 0 or low <= 0:
            continue
        bars.append(Bar(d, close, high, low, vol, "yahoo_fii"))
    return sorted(bars, key=lambda b: b.dia)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--years", type=int, default=2,
                   help="Histórico em anos (default 2). MVP-h21 precisa ≥1y.")
    p.add_argument("--tickers", type=str, default=None,
                   help="CSV de tickers (default = IFIX_TOP_30 inteiro)")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    tickers = (
        [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if args.tickers
        else IFIX_TOP_30
    )
    end = date.today()
    start = end - timedelta(days=args.years * 365 + 30)

    print(f"[backfill_yahoo_fii] {len(tickers)} FIIs · {start} → {end}")

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
            rows = compute_features_for_ticker(bars)
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
    print("[backfill_yahoo_fii] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
