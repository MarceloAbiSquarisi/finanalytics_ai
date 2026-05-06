"""
ohlc_importer — importador in-process de bars 1m historicos
(CSV / Parquet / JSONL) para a tabela ohlc_1m.

Extraido de scripts/import_historical_1m.py para que a aba /admin → Backfill
possa reusar a logica via FastAPI (UploadFile -> tmp -> import_file()).

Funcoes:
  parse_date / to_float / to_int
  read_csv / read_jsonl / read_parquet / auto_reader
  apply_column_map / normalize_row
  upsert_batch
  import_file(path, ...) -> ImportStats   <- entry point principal

Schema destino: ohlc_1m (TimescaleDB hypertable)
  (time, ticker, open, high, low, close, volume, trades, vwap, source)
ON CONFLICT (time, ticker) DO UPDATE — idempotente.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time as _time
from typing import Any, Iterator

import psycopg2
import psycopg2.extras

def _resolve_dsn() -> str:
    raw = (
        os.environ.get("TIMESCALE_URL")
        or os.environ.get("PROFIT_TIMESCALE_DSN")
        or "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
    )
    # psycopg2 nao aceita postgresql+asyncpg://, normaliza pra postgresql://
    return raw.replace("postgresql+asyncpg://", "postgresql://")


DSN = _resolve_dsn()

DEFAULT_MIN_PRICE = 0.01
DEFAULT_SOURCE = "external_1m"


@dataclass
class ImportStats:
    file: str = ""
    read_total: int = 0
    rejected_invalid: int = 0
    rejected_ohlc: int = 0
    rejected_dedup_filtered: int = 0
    upserted: int = 0
    elapsed_s: float = 0.0
    tickers_seen: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "read_total": self.read_total,
            "rejected_invalid": self.rejected_invalid,
            "rejected_ohlc": self.rejected_ohlc,
            "rejected_dedup_filtered": self.rejected_dedup_filtered,
            "upserted": self.upserted,
            "elapsed_s": round(self.elapsed_s, 2),
            "tickers_seen": sorted(self.tickers_seen),
            "tickers_count": len(self.tickers_seen),
            "errors": self.errors[:20],
        }


# ─── parsing ────────────────────────────────────────────────────────────────


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


_TRUTHY = {"true", "1", "yes", "y", "sim", "s", "t", "verdadeiro", "v"}
_FALSY = {"false", "0", "no", "n", "nao", "não", "f", "falso"}


def parse_bool(v: Any) -> bool | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s in _TRUTHY:
        return True
    if s in _FALSY:
        return False
    return None


# ─── readers ────────────────────────────────────────────────────────────────


def read_csv(path: Path) -> Iterator[dict[str, Any]]:
    # utf-8-sig consome BOM ﻿ se presente (exports Excel/Notepad Windows
    # costumam ter — sem isso a 1ª coluna vira "﻿Ativo" e quebra mapping).
    # Tenta detectar separador (Nelogica costuma exportar com ';' em PT-BR).
    with path.open("r", encoding="utf-8-sig", errors="replace") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        for row in reader:
            yield row


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", errors="replace") as f:
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


# ─── normalizacao ───────────────────────────────────────────────────────────


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
        # Suporte a Data+Hora em colunas separadas (formato Nelogica). User
        # mapeia "Data"->time_date e "Hora"->time_time; concatenamos aqui pra
        # manter schema com 1 unica coluna `time`.
        date_part = raw.get("time_date")
        time_part = raw.get("time_time")
        if date_part and time_part:
            t = parse_date(f"{str(date_part).strip()} {str(time_part).strip()}")
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

    hi_ok = h >= max(o, c, l) - 1e-6
    lo_ok = l <= min(o, c, h) + 1e-6
    if not (hi_ok and lo_ok):
        return None, f"OHLC inconsistente O={o} H={h} L={l} C={c}"

    volume = to_int(raw.get("volume")) or 0
    if volume < 0:
        return None, f"volume negativo ({volume})"

    trades = to_int(raw.get("trades"))
    vwap = to_float(raw.get("vwap"))
    aftermarket = parse_bool(raw.get("aftermarket"))
    quantidade = to_int(raw.get("quantidade"))
    if quantidade is not None and quantidade < 0:
        return None, f"quantidade negativa ({quantidade})"

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
        "aftermarket": aftermarket,
        "quantidade": quantidade,
    }, ""


# ─── DB ─────────────────────────────────────────────────────────────────────

_UPSERT_SQL = """
INSERT INTO ohlc_1m
    (time, ticker, open, high, low, close, volume, trades, vwap, source,
     aftermarket, quantidade)
VALUES %s
ON CONFLICT (time, ticker) DO UPDATE SET
    open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
    close=EXCLUDED.close, volume=EXCLUDED.volume,
    trades=EXCLUDED.trades, vwap=EXCLUDED.vwap, source=EXCLUDED.source,
    aftermarket=COALESCE(EXCLUDED.aftermarket, ohlc_1m.aftermarket),
    quantidade=COALESCE(EXCLUDED.quantidade, ohlc_1m.quantidade)
"""


def upsert_batch(conn, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    values = [
        (
            r["time"], r["ticker"], r["open"], r["high"], r["low"], r["close"],
            r["volume"], r["trades"], r["vwap"], r["source"],
            r.get("aftermarket"), r.get("quantidade"),
        )
        for r in rows
    ]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, _UPSERT_SQL, values, page_size=2000)
    return len(values)


# ─── entry point ────────────────────────────────────────────────────────────


def import_file(
    path: Path | str,
    *,
    column_map: dict[str, str] | None = None,
    only_tickers: set[str] | None = None,
    min_price: float = DEFAULT_MIN_PRICE,
    source: str = DEFAULT_SOURCE,
    batch_size: int = 10000,
    max_rows: int = 0,
    dry_run: bool = False,
) -> ImportStats:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))

    stats = ImportStats(file=p.name)
    buffer: list[dict[str, Any]] = []
    col_map = column_map or {}
    only = {t.upper() for t in only_tickers} if only_tickers else None

    conn = None if dry_run else psycopg2.connect(DSN)
    t0 = _time.time()
    try:
        for raw in auto_reader(p):
            stats.read_total += 1
            if max_rows and stats.read_total > max_rows:
                break

            mapped = apply_column_map(raw, col_map)
            row, err = normalize_row(mapped, min_price, source)
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

            if len(buffer) >= batch_size:
                if not dry_run:
                    stats.upserted += upsert_batch(conn, buffer)
                    conn.commit()
                else:
                    stats.upserted += len(buffer)
                buffer.clear()

        if buffer:
            if not dry_run:
                stats.upserted += upsert_batch(conn, buffer)
                conn.commit()
            else:
                stats.upserted += len(buffer)
    finally:
        if conn:
            conn.close()
        stats.elapsed_s = _time.time() - t0

    return stats


def parse_column_map_str(s: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in (s or "").split(","):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k.strip()] = v.strip()
    return out
