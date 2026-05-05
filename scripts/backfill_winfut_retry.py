"""
backfill_winfut_retry.py — coleta os 5 dias faltantes do WINFUT.

Backfill principal (backfill_subscribed_19abr.py) processou WINFUT como
'pulado' porque cada dia tem 5-7M ticks e excede o timeout 1200s do
cliente HTTP. Os 3 primeiros dias (20, 22, 23/abr) entraram no DB
assincronamente antes do skip; faltam 24, 27, 28, 29, 30/abr.

Diferenças do backfill principal:
- Timeout 3600s (60min) — mais que suficiente pra 7M ticks @ ~2500/s
- Skip via consulta DB (não confia em last_collected_to)
- 1 dia por vez sequencial — sem paralelismo p/ não congestionar DLL
"""

from __future__ import annotations

from datetime import date, datetime
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

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

AGENT_URL = "http://localhost:8002"
TICKER = "WINFUT"
EXCHANGE = "F"
TIMEOUT = 3600  # 60min cliente
DB_DSN = os.getenv(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)

DAYS_TO_RETRY = [
    date(2026, 4, 24),
    date(2026, 4, 27),
    date(2026, 4, 28),
    date(2026, 4, 29),
    date(2026, 4, 30),
]


def emit(tag, **kw):
    print(" ".join([tag] + [f"{k}={v}" for k, v in kw.items()]), flush=True)


def fmt_dt(d, hour):
    return f"{d.day:02d}/{d.month:02d}/{d.year} {hour}"


def http_post(path, body, timeout):
    url = f"{AGENT_URL}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def already_in_db(ticker, day):
    try:
        import psycopg2
        with psycopg2.connect(DB_DSN) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM market_history_trades "
                "WHERE ticker = %s AND trade_date::date = %s",
                (ticker, day),
            )
            return cur.fetchone()[0] > 0
    except Exception:
        return False


def main():
    t0 = time.time()
    emit("START", ticker=TICKER, days=len(DAYS_TO_RETRY), timeout_s=TIMEOUT)
    total_ticks = 0
    errors = 0

    for d in DAYS_TO_RETRY:
        if already_in_db(TICKER, d):
            emit("SKIP", ticker=TICKER, day=d.isoformat(), reason="already_in_db")
            continue

        try:
            day_t0 = time.time()
            r = http_post(
                "/collect_history",
                {
                    "ticker": TICKER,
                    "exchange": EXCHANGE,
                    "dt_start": fmt_dt(d, "09:00:00"),
                    "dt_end": fmt_dt(d, "18:00:00"),
                    "timeout": TIMEOUT,
                },
                timeout=TIMEOUT + 60,
            )
            ticks = r.get("ticks", 0)
            inserted = r.get("inserted", 0)
            elapsed = round(time.time() - day_t0)
            total_ticks += ticks
            emit(
                "PROGRESS",
                ticker=TICKER,
                day=d.isoformat(),
                ticks=ticks,
                inserted=inserted,
                elapsed_s=elapsed,
                status=r.get("status", "?"),
            )
        except (urllib.error.URLError, Exception) as exc:
            errors += 1
            err_msg = str(exc)[:120].replace(" ", "_")
            emit("ERROR", ticker=TICKER, day=d.isoformat(), err=err_msg)

    duration_min = round((time.time() - t0) / 60, 1)
    emit("DONE", total_ticks=total_ticks, errors=errors, duration_min=duration_min)
    return 0


if __name__ == "__main__":
    sys.exit(main())
