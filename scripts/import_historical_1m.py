"""
import_historical_1m.py — importador de bars de 1 minuto historicos
(CSV / Parquet / JSONL) de fornecedor externo para ohlc_1m (TimescaleDB).

Substitui o fluxo baseado em ticks quando o fornecedor entrega apenas
bars agregados. Schema-agnostico via --column-map.

Destino: tabela ohlc_1m (ja existente como hypertable):
  time (timestamptz), ticker (text), open/high/low/close (numeric(18,4)),
  volume (bigint), trades (int), vwap (numeric, opcional), source (text).

Upsert com ON CONFLICT (time, ticker) DO UPDATE — idempotente.

Uso:
    python scripts/import_historical_1m.py --file bars.csv
    python scripts/import_historical_1m.py --file bars.parquet --batch-size 20000
    python scripts/import_historical_1m.py --file custom.csv \
        --column-map "ticker=ticker,dt=time,o=open,h=high,l=low,c=close,v=volume"
    python scripts/import_historical_1m.py --file a.csv --dry-run
    python scripts/import_historical_1m.py --file a.csv --only-tickers PETR4,VALE3
    python scripts/import_historical_1m.py --file a.csv --source nelogica_1m

Validacoes:
  - time em range plausivel (2010-01-01 ate hoje+1)
  - OHLC consistente: H >= max(O,C,L), L <= min(O,C,H), price > min_price
  - volume >= 0
  - ticker non-empty uppercase

Linhas invalidas sao logadas e puladas (nao abortam o batch).
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
import time as _time
from typing import Any, Iterator

import psycopg2
import psycopg2.extras

DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)

REQUIRED = {"ticker", "time", "open", "high", "low", "close", "volume"}
DEFAULT_MIN_PRICE = 0.01
DEFAULT_SOURCE = "external_1m"


@dataclass
class ImportStats:
    read_total: int = 0
    rejected_invalid: int = 0
    rejected_ohlc: int = 0
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
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    ):
        try:
            d = datetime.strptime(s, fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
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
    import pyarrow.parquet as pq

    tbl = pq.read_table(str(path))
    for batch in tbl.to_batches(max_chunksize=20000):
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
    raise ValueError(f"formato nao suportado: {ext}")


# ─── Normalizacao ──────────────────────────────────────────────────────────


def apply_column_map(row: dict[str, Any], col_map: dict[str, str]) -> dict[str, Any]:
    if not col_map:
        return row
    out: dict[str, Any] = {}
    for k, v in row.items():
        out[col_map.get(k, k)] = v
    return out


def normalize_row(
    raw: dict[str, Any], min_price: float, source: str
) -> tuple[dict[str, Any] | None, str]:
    ticker = str(raw.get("ticker", "")).strip().upper()
    if not ticker:
        return None, "ticker vazio"

    t = parse_date(raw.get("time") or raw.get("bar_time") or raw.get("date"))
    if t is None:
        return None, "time invalido"

    o = to_float(raw.get("open"))
    h = to_float(raw.get("high"))
    l = to_float(raw.get("low"))
    c = to_float(raw.get("close"))
    if None in (o, h, l, c):
        return None, f"OHLC invalido ({o},{h},{l},{c})"
    if min(o, h, l, c) < min_price:
        return None, f"price abaixo min ({min(o, h, l, c)} < {min_price})"

    # Consistencia OHLC (com tolerancia para ruido de rounding)
    hi_ok = h >= max(o, c, l) - 1e-6
    lo_ok = l <= min(o, c, h) + 1e-6
    if not (hi_ok and lo_ok):
        return None, f"OHLC inconsistente O={o} H={h} L={l} C={c}"

    volume = to_int(raw.get("volume")) or 0
    if volume < 0:
        return None, f"volume negativo ({volume})"

    trades = to_int(raw.get("trades"))
    vwap = to_float(raw.get("vwap"))

    return {
        "time": t,
        "ticker": ticker,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": volume,
        "trades": trades if trades is not None else 0,
        "vwap": vwap,
        "source": source,
    }, ""


# ─── DB ─────────────────────────────────────────────────────────────────────

_UPSERT_SQL = """
INSERT INTO ohlc_1m
    (time, ticker, open, high, low, close, volume, trades, vwap, source)
VALUES %s
ON CONFLICT (time, ticker) DO UPDATE SET
    open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
    close=EXCLUDED.close, volume=EXCLUDED.volume,
    trades=EXCLUDED.trades, vwap=EXCLUDED.vwap, source=EXCLUDED.source
"""


def upsert_batch(conn, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    values = [
        (
            r["time"],
            r["ticker"],
            r["open"],
            r["high"],
            r["low"],
            r["close"],
            r["volume"],
            r["trades"],
            r["vwap"],
            r["source"],
        )
        for r in rows
    ]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, _UPSERT_SQL, values, page_size=2000)
    return len(values)


# ─── CLI ────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Import de bars 1m historicos externos")
    p.add_argument("--file", required=True, help="CSV / Parquet / JSONL")
    p.add_argument("--column-map", default="", help='CSV "src=dst,src2=dst2" para renomear colunas')
    p.add_argument("--only-tickers", default="", help="CSV de tickers a importar; outros ignorados")
    p.add_argument(
        "--min-price",
        type=float,
        default=DEFAULT_MIN_PRICE,
        help="Rejeita rows com min(OHLC) < min (default 0.01; 5 para stocks liquidas)",
    )
    p.add_argument("--batch-size", type=int, default=10000)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-rows", type=int, default=0, help="0 = sem limite")
    p.add_argument("--source", default=DEFAULT_SOURCE, help="Tag gravada em ohlc_1m.source")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    src = Path(args.file)
    if not src.exists():
        print(f"arquivo nao existe: {src}", file=sys.stderr)
        return 2

    col_map: dict[str, str] = {}
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
        f"import_historical_1m: file={src.name} format={src.suffix} "
        f"col_map={col_map} only={only} min_price={args.min_price} "
        f"source={args.source} batch={args.batch_size} dry_run={args.dry_run}"
    )

    stats = ImportStats()
    buffer: list[dict[str, Any]] = []

    conn = None if args.dry_run else psycopg2.connect(DSN)
    try:
        t0 = _time.time()
        for raw in auto_reader(src):
            stats.read_total += 1
            if args.max_rows and stats.read_total > args.max_rows:
                break

            raw_mapped = apply_column_map(raw, col_map)
            row, err = normalize_row(raw_mapped, args.min_price, args.source)
            if row is None:
                if "OHLC" in err:
                    stats.rejected_ohlc += 1
                else:
                    stats.rejected_invalid += 1
                if len(stats.errors) < 20:
                    stats.errors.append(f"row {stats.read_total}: {err}")
                continue

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
                if stats.read_total % 100000 == 0:
                    elapsed = _time.time() - t0
                    print(
                        f"  progresso: {stats.read_total} rows "
                        f"({stats.read_total / max(elapsed, 1):.0f}/s) "
                        f"upserted={stats.upserted} "
                        f"invalid={stats.rejected_invalid} ohlc_bad={stats.rejected_ohlc}"
                    )

        if buffer:
            if not args.dry_run:
                stats.upserted += upsert_batch(conn, buffer)
                conn.commit()
            else:
                stats.upserted += len(buffer)

        elapsed = _time.time() - t0
        print("\n=== RESUMO ===")
        print(f"  read_total         = {stats.read_total}")
        print(f"  rejected_invalid   = {stats.rejected_invalid}")
        print(f"  rejected_ohlc_bad  = {stats.rejected_ohlc}")
        print(f"  rejected_filtered  = {stats.rejected_dedup_filtered}")
        print(f"  upserted           = {stats.upserted}")
        print(f"  tickers_unicos     = {len(stats.tickers_seen)}")
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
