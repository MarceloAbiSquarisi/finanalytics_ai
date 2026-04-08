"""
sync_brapi_to_ohlc.py
Sincroniza dados OHLCV dos Parquets BRAPI (/data/ohlcv) direto para ohlc_prices.
Usa asyncpg — compatível com o container finanalytics_scheduler.
"""
from __future__ import annotations

import asyncio
import glob
import os
import time

import asyncpg
import pandas as pd

OHLCV_DIR = os.environ.get("DATA_DIR", "/data") + "/ohlcv"
DSN = (
    os.environ.get("DATABASE_DSN", "")
    .replace("postgresql+asyncpg://", "postgresql://")
    .replace("postgresql+psycopg2://", "postgresql://")
)

SQL_UPSERT = """
    INSERT INTO ohlc_prices (ticker, date, open, high, low, close, adj_close, volume)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    ON CONFLICT (ticker, date) DO UPDATE SET
        open      = EXCLUDED.open,
        high      = EXCLUDED.high,
        low       = EXCLUDED.low,
        close     = EXCLUDED.close,
        adj_close = EXCLUDED.adj_close,
        volume    = EXCLUDED.volume
"""


async def sync() -> None:
    t0 = time.perf_counter()

    conn = await asyncpg.connect(DSN)

    # MAX(date) por ticker existente
    rows_max = await conn.fetch("SELECT ticker, MAX(date) FROM ohlc_prices GROUP BY ticker")
    max_dates: dict = {r[0]: r[1] for r in rows_max}
    print(f"Tickers já em ohlc_prices: {len(max_dates)}")

    tickers = sorted(
        d for d in os.listdir(OHLCV_DIR)
        if os.path.isdir(os.path.join(OHLCV_DIR, d))
    )
    print(f"Tickers no data lake: {len(tickers)}")

    total_rows = total_tickers = total_errors = 0
    BATCH = 2000

    for ticker in tickers:
        files = glob.glob(f"{OHLCV_DIR}/{ticker}/*.parquet")
        if not files:
            continue

        try:
            df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
            df["date"] = pd.to_datetime(df["date"]).dt.date

            max_date = max_dates.get(ticker)
            if max_date:
                df = df[df["date"] > max_date]

            df = df[df["close"] > 0]

            if df.empty:
                continue

            records = [
                (
                    ticker,
                    row["date"],
                    float(row["open"])   if pd.notna(row["open"])   else None,
                    float(row["high"])   if pd.notna(row["high"])   else None,
                    float(row["low"])    if pd.notna(row["low"])    else None,
                    float(row["close"])  if pd.notna(row["close"])  else None,
                    None,  # adj_close não disponível na BRAPI
                    float(row["volume"]) if pd.notna(row["volume"]) else None,
                )
                for _, row in df.iterrows()
            ]

            for i in range(0, len(records), BATCH):
                await conn.executemany(SQL_UPSERT, records[i:i + BATCH])

            print(f"  {ticker}: +{len(records)} linhas")
            total_rows += len(records)
            total_tickers += 1

        except Exception as e:
            print(f"  ERRO {ticker}: {e}")
            total_errors += 1

    await conn.close()
    elapsed = time.perf_counter() - t0
    print(f"\nConcluído em {elapsed:.0f}s")
    print(f"  Tickers atualizados : {total_tickers}")
    print(f"  Linhas inseridas    : {total_rows:,}")
    print(f"  Erros               : {total_errors}")


if __name__ == "__main__":
    if not DSN:
        raise SystemExit("DATABASE_DSN não configurado")
    asyncio.run(sync())
