"""apply_backfill_migration.py — aplica init_timescale/007_backfill_jobs.sql
contra o TimescaleDB ja' rodando (containers existentes onde init_timescale/
nao roda no boot porque o volume ja' foi populado).

Idempotente — pode rodar varias vezes sem problema.

Uso:
    python scripts/apply_backfill_migration.py
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

import psycopg2

_env_file = Path(__file__).resolve().parents[1] / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            _k = _k.strip()
            _v = _v.strip().strip('"').strip("'")
            if _k not in os.environ:
                os.environ[_k] = _v

DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)
SQL_FILE = Path(__file__).resolve().parents[1] / "init_timescale" / "007_backfill_jobs.sql"


def main() -> int:
    if not SQL_FILE.exists():
        print(f"sql nao encontrado: {SQL_FILE}", file=sys.stderr)
        return 2
    sql = SQL_FILE.read_text(encoding="utf-8")
    print(f"aplicando {SQL_FILE.name} em {DSN.split('@')[-1]}")
    with psycopg2.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    print("ok — backfill_jobs + backfill_job_items prontas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
