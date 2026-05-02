"""
backfill_subscribed_19abr.py — Backfill ticks dos 376 subscribed tickers.

Variante de backfill_history.py que lê de profit_subscribed_tickers
(escopo "ticks subscritos" pedido em 02/mai). Range hardcoded:
2026-04-19 → 2026-05-02 (9 pregões úteis: 21/abr Tiradentes + 01/mai
Trabalho excluídos automaticamente pela tabela HOLIDAYS_BR).

Output formatado pra streaming via Monitor:
  PROGRESS pct=0.5 ticker=PETR4 day=2026-04-20 ticks=78421 inserted=12345 status=ok
  PROGRESS pct=1.0 ticker=PETR4 day=2026-04-22 ...
  ERROR ticker=X day=Y err=...
  DONE total_ticks=N errors=K duration_min=M

Cada linha começa com tag canônica (PROGRESS|ERROR|DONE) — fácil grep
no Monitor pra emitir só os eventos relevantes.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

# Carrega .env
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
TIMEOUT_S = 300
TIMEOUT_FUT = 1200
FUTURES_EXCHANGE = {"F"}
DELAY_S = 2  # delay entre chamadas
START = date(2026, 4, 19)
END = date(2026, 5, 2)

HOLIDAYS_BR: set[date] = {
    date(2026, 4, 21),  # Tiradentes
    date(2026, 5, 1),   # Trabalho
    # outros feriados BR já estão fora do range
}

DB_DSN = os.getenv(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS_BR


def trading_days(start: date, end: date) -> list[date]:
    days = []
    d = start
    while d <= end:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
    return days


def http_post(path: str, body: dict, timeout: int = 30) -> dict:
    url = f"{AGENT_URL}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def http_get(path: str, timeout: int = 10) -> dict:
    with urllib.request.urlopen(f"{AGENT_URL}{path}", timeout=timeout) as r:
        return json.loads(r.read())


def get_subscribed_tickers() -> list[dict]:
    """376 tickers ativos de profit_subscribed_tickers."""
    import psycopg2

    with psycopg2.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT ticker, exchange FROM profit_subscribed_tickers
            WHERE active = TRUE AND ticker != 'XPTO'
            ORDER BY exchange DESC, ticker  -- futuros primeiro (timeout maior)
            """
        )
        return [{"ticker": r[0], "exchange": r[1]} for r in cur.fetchall()]


def get_collected_dates(ticker: str, start: date, end: date) -> set[date]:
    """Datas que já têm ticks pra ticker no range — pra skip."""
    try:
        import psycopg2

        with psycopg2.connect(DB_DSN) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT trade_date::date
                FROM market_history_trades
                WHERE ticker = %s
                  AND trade_date::date BETWEEN %s AND %s
                """,
                (ticker, start.isoformat(), end.isoformat()),
            )
            return {r[0] for r in cur.fetchall()}
    except Exception:
        return set()


def emit(tag: str, **kwargs) -> None:
    """Linha estruturada para Monitor grep."""
    parts = [tag] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" ".join(parts), flush=True)


def fmt_dt(d: date, hour: str) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year} {hour}"


def main() -> int:
    t0 = time.time()
    days = trading_days(START, END)
    tickers = get_subscribed_tickers()
    total_calls = len(tickers) * len(days)

    emit(
        "START",
        tickers=len(tickers),
        days=len(days),
        range=f"{START}_{END}",
        total_calls=total_calls,
    )

    # Health check
    try:
        s = http_get("/status", timeout=5)
        if not s.get("market_connected"):
            emit("ABORT", reason="market_not_connected")
            return 1
    except Exception as exc:
        emit("ABORT", reason=f"agent_unreachable: {exc}")
        return 2

    done = 0
    total_ticks = 0
    errors = 0

    for tinfo in tickers:
        ticker = tinfo["ticker"]
        exchange = tinfo["exchange"]
        timeout = TIMEOUT_FUT if exchange in FUTURES_EXCHANGE else TIMEOUT_S

        # Skip dias já coletados
        already = get_collected_dates(ticker, START, END)
        days_for_ticker = [d for d in days if d not in already]
        skipped = len(days) - len(days_for_ticker)
        if skipped:
            emit("SKIP", ticker=ticker, exchange=exchange, days_already=skipped)
            done += skipped

        if not days_for_ticker:
            continue

        for d in days_for_ticker:
            done += 1
            pct = round(done / total_calls * 100, 2)
            try:
                r = http_post(
                    "/collect_history",
                    {
                        "ticker": ticker,
                        "exchange": exchange,
                        "dt_start": fmt_dt(d, "09:00:00"),
                        "dt_end": fmt_dt(d, "18:00:00"),
                        "timeout": timeout,
                    },
                    timeout=timeout + 60,
                )
                ticks = r.get("ticks", 0)
                inserted = r.get("inserted", 0)
                status = r.get("status", "?")
                total_ticks += ticks
                emit(
                    "PROGRESS",
                    pct=pct,
                    done=done,
                    total=total_calls,
                    ticker=ticker,
                    day=d.isoformat(),
                    ticks=ticks,
                    inserted=inserted,
                    status=status,
                )
            except (urllib.error.URLError, Exception) as exc:
                errors += 1
                err_msg = str(exc)[:120].replace(" ", "_")
                emit(
                    "ERROR",
                    ticker=ticker,
                    day=d.isoformat(),
                    err=err_msg,
                )

            time.sleep(DELAY_S)

    duration_min = round((time.time() - t0) / 60, 1)
    emit(
        "DONE",
        total_ticks=total_ticks,
        errors=errors,
        duration_min=duration_min,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
