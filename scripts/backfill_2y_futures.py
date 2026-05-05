"""
backfill_2y_futures.py — backfill 2 anos de WINFUT + WDOFUT via DLL Nelogica.

Validacao 05/mai/2026 confirmou que DLL retorna ticks ate 06/05/2024 (~559k
ticks/h pra WINFUT, ~110k pra WDOFUT). 504 dias uteis x 2 ativos x ~9h
trading/dia = ~25 dias 24/7 pra completar; iterativo+retomavel pra que
multiplas sessoes possam progredir.

Convencoes:
  - 1 dia por vez sequencial (DLL nao paraleliza GetHistoryTrades).
  - Skip via market_history_trades.COUNT > 0 (idempotente).
  - Itera DESC (dia mais recente -> mais antigo) — dados recentes tem mais
    valor pra modelos atuais; smoke amanha usa snapshot recente.
  - SIGINT/SIGTERM graceful: termina dia em andamento e sai.
  - Skip fim-de-semana via weekday(). Feriados B3 detectados via ticks=0.
  - Range default: 2024-05-06 (2y exato) -> 2026-04-30 (mais recente full).

Uso:
  python scripts/backfill_2y_futures.py
  python scripts/backfill_2y_futures.py --tickers WINFUT
  python scripts/backfill_2y_futures.py --start 2025-01-01 --end 2025-06-30
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import signal
import sys
import time
import urllib.error
import urllib.request

# Carrega .env (psycopg2 DSN, etc.)
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

AGENT_URL = os.getenv("PROFIT_AGENT_URL", "http://localhost:8002")
EXCHANGE = "F"
DEFAULT_TIMEOUT = 3600  # 60 min — folga sobre 56min worst-case WINFUT 1d
DB_DSN = os.getenv(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)

DEFAULT_START = date(2024, 5, 6)   # 2 anos exatos
DEFAULT_END = date(2026, 4, 30)    # ultimo full dia smoke 04/mai
DEFAULT_TICKERS = ("WINFUT", "WDOFUT")

_stop = False


def _on_signal(signum, _frame):
    global _stop
    _stop = True
    emit("SIGNAL_RECEIVED", signum=signum, will_exit_after_current_day=True)


def emit(tag: str, **kw) -> None:
    parts = [tag] + [f"{k}={v}" for k, v in kw.items()]
    print(" ".join(parts), flush=True)


def fmt_dt(d: date, hour: str) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year} {hour}"


def http_post(path: str, body: dict, timeout: int) -> dict:
    url = f"{AGENT_URL}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def already_in_db(ticker: str, day: date) -> bool:
    """Skip se DB ja' tem ticks pra (ticker, day). Conecta direto via psycopg2.

    Re-adicionado 05/mai apos discussao com user: backfill 20-30/abr ja'
    foi feito em sessoes anteriores; pular esses dias economiza tempo wall-
    clock significativo (~80min/dia WINFUT).

    DSN preferencial via env (config compativel com container e host). Em
    host Windows com firewall problematico, conexao pode travar — usamos
    connect_timeout=5s + tratar excecao como "nao consigo verificar, segue
    pra DLL" (ON CONFLICT do agent garante idempotencia mesmo sem skip).
    """
    try:
        import psycopg2

        with psycopg2.connect(DB_DSN, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM market_history_trades "
                "WHERE ticker = %s AND trade_date::date = %s LIMIT 1",
                (ticker, day),
            )
            return cur.fetchone() is not None
    except Exception as exc:
        emit("DB_CHECK_ERR", ticker=ticker, day=day.isoformat(), err=str(exc)[:80])
        return False


def collect_one_day(ticker: str, day: date, timeout: int) -> tuple[int, int, str, float]:
    t0 = time.time()
    try:
        r = http_post(
            "/collect_history",
            {
                "ticker": ticker,
                "exchange": EXCHANGE,
                "dt_start": fmt_dt(day, "09:00:00"),
                "dt_end": fmt_dt(day, "18:00:00"),
                "timeout": timeout,
            },
            timeout=timeout + 60,
        )
        elapsed = time.time() - t0
        return (
            int(r.get("ticks", 0)),
            int(r.get("inserted", 0)),
            str(r.get("status", "?")),
            elapsed,
        )
    except urllib.error.HTTPError as exc:
        return (0, 0, f"http_{exc.code}", time.time() - t0)
    except urllib.error.URLError as exc:
        return (0, 0, f"url_err_{str(exc.reason)[:40]}", time.time() - t0)
    except Exception as exc:
        return (0, 0, f"err_{str(exc)[:60]}", time.time() - t0)


def daterange_desc(start: date, end: date):
    """Yield dias uteis de end -> start (DESC)."""
    d = end
    while d >= start:
        if d.weekday() < 5:  # 0=segunda..4=sexta
            yield d
        d -= timedelta(days=1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tickers", nargs="+", default=list(DEFAULT_TICKERS), help="Tickers (default: WINFUT WDOFUT)"
    )
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument("--end", type=date.fromisoformat, default=DEFAULT_END)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    days = list(daterange_desc(args.start, args.end))
    total_calls = len(days) * len(args.tickers)

    # Progress file: skip dias ja processados em runs anteriores. Reset com rm.
    progress_file = Path(__file__).parent.parent / "logs" / "backfill" / "progress_2y.json"
    progress: set[str] = set()
    if progress_file.exists():
        try:
            progress = set(json.loads(progress_file.read_text(encoding="utf-8")))
        except Exception:
            progress = set()

    def mark_done(ticker: str, day: date) -> None:
        progress.add(f"{ticker}:{day.isoformat()}")
        try:
            progress_file.parent.mkdir(parents=True, exist_ok=True)
            progress_file.write_text(json.dumps(sorted(progress)), encoding="utf-8")
        except Exception as exc:
            emit("PROGRESS_WRITE_ERR", err=str(exc)[:80])

    emit(
        "START",
        tickers=",".join(args.tickers),
        start=args.start.isoformat(),
        end=args.end.isoformat(),
        days_per_ticker=len(days),
        total_calls=total_calls,
        already_done=len(progress),
        agent=AGENT_URL,
    )

    t0 = time.time()
    stats = {t: {"ticks": 0, "inserted": 0, "ok": 0, "skip": 0, "err": 0} for t in args.tickers}

    for d in days:
        if _stop:
            break
        for ticker in args.tickers:
            if _stop:
                break
            key = f"{ticker}:{d.isoformat()}"
            if key in progress:
                stats[ticker]["skip"] += 1
                emit("SKIP", ticker=ticker, day=d.isoformat(), reason="progress_file")
                continue
            # Skip secundario: DB ja tem dados (ex: backfill 20-30/abr previo).
            # Mais lento que progress_file mas pega dias coletados em sessoes
            # anteriores que nao chegaram a marcar progress.
            if already_in_db(ticker, d):
                stats[ticker]["skip"] += 1
                mark_done(ticker, d)
                emit("SKIP", ticker=ticker, day=d.isoformat(), reason="db_has_data")
                continue

            ticks, inserted, status, elapsed = collect_one_day(ticker, d, args.timeout)
            stats[ticker]["ticks"] += ticks
            stats[ticker]["inserted"] += inserted
            if status == "ok":
                stats[ticker]["ok"] += 1
                tag = "PROGRESS"
                mark_done(ticker, d)
            else:
                stats[ticker]["err"] += 1
                tag = "ERROR"
            emit(
                tag,
                ticker=ticker,
                day=d.isoformat(),
                ticks=ticks,
                inserted=inserted,
                status=status,
                elapsed_s=round(elapsed, 1),
            )

    duration_min = round((time.time() - t0) / 60, 1)
    for tk, s in stats.items():
        emit(
            "TICKER_DONE",
            ticker=tk,
            ok=s["ok"],
            skip=s["skip"],
            err=s["err"],
            ticks=s["ticks"],
            inserted=s["inserted"],
        )
    emit("DONE", duration_min=duration_min, stopped_by_signal=_stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
