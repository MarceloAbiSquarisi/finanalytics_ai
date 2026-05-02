"""
import_historical_ticks.py — importador de ticks históricos comprados/externos.

Aceita CSV, Parquet ou JSON-lines. Valida schema, normaliza colunas e faz
upsert em `market_history_trades` com `ON CONFLICT (ticker, trade_date,
trade_number) DO NOTHING` (idempotente).

Colunas esperadas (mapeamento flexível via --column-map):
  ticker         -> ticker (text)
  trade_date     -> trade_date (timestamptz, aceita ISO / datetime)
  trade_number   -> trade_number (bigint; gerado se ausente — sequencial)
  price          -> price (double)
  quantity       -> quantity (bigint)
  volume         -> volume (double; default price*quantity)
  trade_type     -> trade_type (int; default 0)
  buy_agent      -> buy_agent (int; default 0)
  sell_agent     -> sell_agent (int; default 0)

Uso:
    python scripts/import_historical_ticks.py --file dados.csv
    python scripts/import_historical_ticks.py --file ticks.parquet --batch-size 10000
    python scripts/import_historical_ticks.py --file custom.csv \
        --column-map "symbol=ticker,dt=trade_date,p=price,q=quantity"
    python scripts/import_historical_ticks.py --file a.csv --dry-run
    python scripts/import_historical_ticks.py --file a.csv --only-tickers PETR4,VALE3

Validações automáticas:
  - trade_date em range plausível (2010-01-01 até hoje+1)
  - price > 0 (rejeita ÷100 bug anterior — ou via --min-price)
  - quantity > 0
  - ticker non-empty uppercase
Linhas inválidas são logadas e puladas (não abortam o batch).

Após importação, opcionalmente roda:
  - populate_daily_bars.py (para os tickers importados)
  - features_daily_builder.py --only <tickers>
  - pop_profit_daily_cov para refresh
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Iterator

import psycopg2
import psycopg2.extras

DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)

COLUMNS_DB = [
    "ticker",
    "trade_date",
    "trade_number",
    "price",
    "quantity",
    "volume",
    "trade_type",
    "buy_agent",
    "sell_agent",
]

REQUIRED = {"ticker", "trade_date", "price", "quantity"}

DEFAULT_MIN_PRICE = 0.01  # rejeita ÷100 se combinar com --min-price 5


@dataclass
class ImportStats:
    read_total: int = 0
    rejected_invalid: int = 0
    rejected_dedup_filtered: int = 0
    upserted: int = 0
    tickers_seen: set = None
    errors: list = None

    def __post_init__(self):
        if self.tickers_seen is None:
            self.tickers_seen = set()
        if self.errors is None:
            self.errors = []


# ─── Parsing ────────────────────────────────────────────────────────────────


def parse_date(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).strip()
    # ISO 8601
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
    ):
        try:
            d = datetime.strptime(s, fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # fromisoformat modernos
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def to_int(v: Any) -> int | None:
    f = to_float(v)
    return int(f) if f is not None else None


# ─── Readers ────────────────────────────────────────────────────────────────


def read_csv(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def read_parquet(path: Path) -> Iterator[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ImportError:
        print(
            "pyarrow não instalado — converta parquet para csv/jsonl ou rode: uv pip install pyarrow",
            file=sys.stderr,
        )
        raise
    tbl = pq.read_table(str(path))
    for batch in tbl.to_batches(max_chunksize=10000):
        for row in batch.to_pylist():
            yield row


def auto_reader(path: Path) -> Iterator[dict[str, Any]]:
    ext = path.suffix.lower()
    if ext in (".csv", ".txt"):
        return read_csv(path)
    if ext in (".jsonl", ".json"):
        return read_jsonl(path)
    if ext in (".parquet", ".pq"):
        return read_parquet(path)
    raise ValueError(f"formato não suportado: {ext}")


# ─── Normalização ──────────────────────────────────────────────────────────


def apply_column_map(row: dict[str, Any], col_map: dict[str, str]) -> dict[str, Any]:
    if not col_map:
        return row
    out: dict[str, Any] = {}
    for k, v in row.items():
        out[col_map.get(k, k)] = v
    return out


def normalize_row(
    raw: dict[str, Any], min_price: float, auto_tn: int
) -> tuple[dict[str, Any] | None, str]:
    """Retorna (row_normalizado, err_msg). row=None se inválida."""
    ticker = str(raw.get("ticker", "")).strip().upper()
    if not ticker:
        return None, "ticker vazio"

    td = parse_date(raw.get("trade_date"))
    if td is None:
        return None, "trade_date inválido"

    price = to_float(raw.get("price"))
    if price is None or price < min_price:
        return None, f"price inválido ({price})"

    quantity = to_int(raw.get("quantity"))
    if quantity is None or quantity <= 0:
        return None, f"quantity inválido ({quantity})"

    tn = to_int(raw.get("trade_number"))
    if tn is None:
        tn = auto_tn  # caller preencher sequencial

    volume = to_float(raw.get("volume"))
    if volume is None:
        volume = price * quantity

    return {
        "ticker": ticker,
        "trade_date": td,
        "trade_number": tn,
        "price": price,
        "quantity": quantity,
        "volume": volume,
        "trade_type": to_int(raw.get("trade_type")) or 0,
        "buy_agent": to_int(raw.get("buy_agent")) or 0,
        "sell_agent": to_int(raw.get("sell_agent")) or 0,
    }, ""


# ─── DB ─────────────────────────────────────────────────────────────────────


def upsert_batch(conn, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    sql = """
        INSERT INTO market_history_trades
            (ticker, trade_date, trade_number, price, quantity, volume,
             trade_type, buy_agent, sell_agent)
        VALUES %s
        ON CONFLICT (ticker, trade_date, trade_number) DO NOTHING
    """
    values = [
        (
            r["ticker"],
            r["trade_date"],
            r["trade_number"],
            r["price"],
            r["quantity"],
            r["volume"],
            r["trade_type"],
            r["buy_agent"],
            r["sell_agent"],
        )
        for r in rows
    ]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, values, page_size=1000)
    return len(values)


# ─── CLI ────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Import de ticks históricos externos")
    p.add_argument("--file", required=True, help="CSV / Parquet / JSONL")
    p.add_argument("--column-map", default="", help='CSV "src=dst,src2=dst2" para renomear colunas')
    p.add_argument("--only-tickers", default="", help="CSV de tickers a importar; outros ignorados")
    p.add_argument(
        "--min-price",
        type=float,
        default=DEFAULT_MIN_PRICE,
        help="Rejeita rows com price < min (default 0.01; use 5 para blindar contra bug ÷100)",
    )
    p.add_argument("--batch-size", type=int, default=5000)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-rows", type=int, default=0, help="0 = sem limite")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    src = Path(args.file)
    if not src.exists():
        print(f"arquivo não existe: {src}", file=sys.stderr)
        return 2

    col_map = {}
    for pair in args.column_map.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            col_map[k.strip()] = v.strip()

    only = (
        {t.strip().upper() for t in args.only_tickers.split(",") if t.strip()}
        if args.only_tickers
        else None
    )

    print(
        f"import_historical_ticks: file={src.name} format={src.suffix} "
        f"col_map={col_map} only={only} min_price={args.min_price} "
        f"batch={args.batch_size} dry_run={args.dry_run}"
    )

    stats = ImportStats()
    buffer: list[dict[str, Any]] = []
    auto_tn_counter = int(time.time() * 1000) * 10  # base para trade_number sintético

    conn = None if args.dry_run else psycopg2.connect(DSN)
    try:
        t0 = time.time()
        for raw in auto_reader(src):
            stats.read_total += 1
            if args.max_rows and stats.read_total > args.max_rows:
                break

            raw_mapped = apply_column_map(raw, col_map)
            row, err = normalize_row(raw_mapped, args.min_price, auto_tn_counter)
            if row is None:
                stats.rejected_invalid += 1
                if len(stats.errors) < 20:
                    stats.errors.append(f"row {stats.read_total}: {err}")
                continue
            auto_tn_counter += 1

            if only and row["ticker"] not in only:
                stats.rejected_dedup_filtered += 1
                continue

            stats.tickers_seen.add(row["ticker"])
            buffer.append(row)

            if len(buffer) >= args.batch_size:
                if not args.dry_run:
                    stats.upserted += upsert_batch(conn, buffer)
                    conn.commit()
                else:
                    stats.upserted += len(buffer)
                buffer.clear()

                if stats.read_total % 50000 == 0:
                    elapsed = time.time() - t0
                    print(
                        f"  progresso: {stats.read_total} rows ({stats.read_total / max(elapsed, 1):.0f}/s) "
                        f"upserted={stats.upserted} rejected={stats.rejected_invalid}"
                    )

        if buffer:
            if not args.dry_run:
                stats.upserted += upsert_batch(conn, buffer)
                conn.commit()
            else:
                stats.upserted += len(buffer)

        elapsed = time.time() - t0
        print("\n=== RESUMO ===")
        print(f"  read_total         = {stats.read_total}")
        print(f"  rejected_invalid   = {stats.rejected_invalid}")
        print(f"  rejected_filtered  = {stats.rejected_dedup_filtered}")
        print(f"  upserted           = {stats.upserted}")
        print(f"  tickers_únicos     = {len(stats.tickers_seen)}")
        print(
            f"  elapsed            = {elapsed:.1f}s ({stats.read_total / max(elapsed, 1):.0f} rows/s)"
        )
        if stats.errors:
            print("\n  Primeiros erros:")
            for e in stats.errors[:10]:
                print(f"    {e}")
    finally:
        if conn:
            conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
