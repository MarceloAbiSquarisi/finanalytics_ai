"""
backfill_resume.py — Backfill incremental: cada ticker continua do ultimo dia coletado.

Diferente de backfill_history.py (que usa --start fixo p/ todos), este script:
  - Para cada ticker ativo, le MAX(trade_date) em market_history_trades
  - Coleta de (ultimo_dia + 1) ate --end (default: hoje)
  - Se ticker nunca foi coletado, usa --fallback-start (default: 2026-01-02)

Uso:
  python backfill_resume.py
  python backfill_resume.py --end 2026-04-15
  python backfill_resume.py --fallback-start 2026-03-01 --delay 5
  python backfill_resume.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from datetime import date, timedelta
from pathlib import Path

# Carrega .env para PROFIT_TIMESCALE_DSN
_env_file = Path(__file__).resolve().parents[1] / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            _k = _k.strip()
            _v = _v.strip().strip('"').strip("'")
            import os as _os2
            if _k not in _os2.environ:
                _os2.environ[_k] = _v

import os as _os

AGENT_URL        = "http://localhost:8002"
TIMEOUT_S        = 300
TIMEOUT_FUT      = 2400
FUTURES_EXCHANGE = {"F"}
DELAY_S          = 3

DB_DSN = _os.getenv(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
)

HOLIDAYS_BR: set[date] = {
    date(2026, 1, 1),
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
    date(2026, 4, 3),
    date(2026, 4, 21),
    date(2026, 5, 1),
    date(2026, 6, 4),
    date(2026, 9, 7),
    date(2026, 10, 12),
    date(2026, 11, 2),
    date(2026, 11, 15),
    date(2026, 11, 20),
    date(2026, 12, 25),
}


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


def http_get(path: str) -> dict:
    with urllib.request.urlopen(f"{AGENT_URL}{path}", timeout=30) as r:
        return json.loads(r.read())


def http_post(path: str, body: dict, timeout: int = 30) -> dict:
    data = json.dumps(body).encode("utf-8")
    req  = urllib.request.Request(
        f"{AGENT_URL}{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get_active_tickers_with_last_date() -> list[dict]:
    """
    Lista de tickers ativos com o ultimo dia ja coletado em
    market_history_trades. Sempre via banco direto.
    """
    import psycopg2  # type: ignore
    conn = psycopg2.connect(DB_DSN)
    cur  = conn.cursor()
    cur.execute("""
        SELECT t.ticker, t.exchange,
               (SELECT MAX(trade_date::date)
                  FROM market_history_trades h
                 WHERE h.ticker = t.ticker) AS last_date
          FROM profit_history_tickers t
         WHERE t.active = TRUE
         ORDER BY t.ticker
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {"ticker": r[0], "exchange": r[1], "last_date": r[2]}
        for r in rows
    ]


def get_collected_dates(ticker: str, start: date, end: date) -> set[date]:
    """Datas que ja tem ticks no banco no range (skip granular)."""
    try:
        import psycopg2  # type: ignore
        conn = psycopg2.connect(DB_DSN)
        cur  = conn.cursor()
        cur.execute("""
            SELECT DISTINCT trade_date::date
              FROM market_history_trades
             WHERE ticker = %s
               AND trade_date::date BETWEEN %s AND %s
        """, (ticker, start.isoformat(), end.isoformat()))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return {r[0] for r in rows}
    except Exception as e:
        print(f"  [AVISO] verificacao banco falhou: {e}")
        return set()


def format_dt(d: date, hour: str) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year} {hour}"


def backfill_resume(end: date, fallback_start: date, delay: float, dry_run: bool) -> None:
    print(f"\n{'='*60}")
    print(f"BACKFILL INCREMENTAL (resume per-ticker)")
    print(f"  End             : {end}")
    print(f"  Fallback start  : {fallback_start} (se nunca coletado)")
    print(f"  Delay           : {delay}s")
    print(f"  Dry-run         : {dry_run}")
    print(f"{'='*60}\n")

    try:
        status = http_get("/status")
        if not status.get("market_connected"):
            print("[ERRO] Agent nao conectado ao mercado.")
            sys.exit(1)
        print(f"[OK] Agent online — market={status['market_connected']} db={status['db_connected']}")
    except Exception as e:
        print(f"[ERRO] Agent inacessivel: {e}")
        sys.exit(1)

    try:
        tickers = get_active_tickers_with_last_date()
    except Exception as e:
        print(f"[ERRO] Falha ao consultar tickers: {e}")
        sys.exit(1)

    if not tickers:
        print("[ERRO] Nenhum ticker ativo em profit_history_tickers.")
        sys.exit(1)

    plan: list[tuple[str, str, list[date]]] = []
    for t in tickers:
        ticker, exchange, last_date = t["ticker"], t["exchange"], t["last_date"]
        if last_date is None:
            start  = fallback_start
            origem = f"nunca coletado -> fallback {fallback_start}"
        else:
            start  = last_date + timedelta(days=1)
            origem = f"ultimo coletado={last_date} -> resume em {start}"

        if start > end:
            print(f"[{ticker}] em dia ({origem}) — nada a fazer")
            continue

        days = trading_days(start, end)
        if not days:
            print(f"[{ticker}] sem pregoes em {start}..{end} — nada a fazer")
            continue

        if not dry_run:
            ja = get_collected_dates(ticker, start, end)
            if ja:
                before = len(days)
                days = [d for d in days if d not in ja]
                if before - len(days):
                    print(f"[{ticker}] {origem}; pulando {before - len(days)} dias ja no banco")

        if not days:
            print(f"[{ticker}] todos os dias ja coletados — nada a fazer")
            continue

        plan.append((ticker, exchange, days))
        print(f"[{ticker}:{exchange}] {origem} | {len(days)} pregoes p/ coletar")

    if not plan:
        print("\n[OK] Nada a coletar — todos os tickers em dia.\n")
        return

    total_calls = sum(len(d) for _, _, d in plan)
    done_calls  = 0
    total_ticks = 0
    errors: list[tuple[str, date, str]] = []

    print(f"\n{'-'*60}")
    print(f"Total de chamadas planejadas: {total_calls}")
    print(f"{'-'*60}\n")

    for ticker, exchange, days in plan:
        is_fut       = exchange in FUTURES_EXCHANGE
        timeout_call = TIMEOUT_FUT if is_fut else TIMEOUT_S
        print(f"\n[{ticker}:{exchange}] timeout={timeout_call}s | {len(days)} dias")
        ticker_ticks = 0

        for d in days:
            done_calls += 1
            pct      = done_calls / total_calls * 100
            dt_start = format_dt(d, "09:00:00")
            dt_end   = format_dt(d, "18:00:00")

            print(f"  [{pct:5.1f}%] {ticker} {d.strftime('%d/%m/%Y')} ... ",
                  end="", flush=True)

            if dry_run:
                print("(dry-run)")
                time.sleep(0.05)
                continue

            try:
                result = http_post("/collect_history", {
                    "ticker":   ticker,
                    "exchange": exchange,
                    "dt_start": dt_start,
                    "dt_end":   dt_end,
                    "timeout":  timeout_call,
                }, timeout=timeout_call + 60)

                ticks    = result.get("ticks", 0)
                inserted = result.get("inserted", 0)
                status_r = result.get("status", "?")
                print(f"{ticks:>7} ticks | {inserted:>7} inseridos | {status_r}")
                ticker_ticks += ticks
                total_ticks  += ticks

            except urllib.error.URLError as e:
                print(f"ERRO HTTP: {e}")
                errors.append((ticker, d, str(e)))
            except Exception as e:
                print(f"ERRO: {e}")
                errors.append((ticker, d, str(e)))

            time.sleep(delay)

        print(f"  [{ticker}] subtotal: {ticker_ticks:,} ticks")

    print(f"\n{'='*60}")
    print(f"RESUMO")
    print(f"  Ticks coletados : {total_ticks:,}")
    print(f"  Chamadas OK     : {done_calls - len(errors)}/{done_calls}")
    print(f"  Erros           : {len(errors)}")
    if errors:
        print(f"\n  Erros:")
        for tkr, d, err in errors:
            print(f"    {tkr} {d}: {err}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill incremental por ticker")
    parser.add_argument("--end", default=date.today().isoformat(),
                        help="Data final YYYY-MM-DD (default: hoje)")
    parser.add_argument("--fallback-start", default="2026-01-02",
                        help="Inicio caso ticker nunca tenha sido coletado")
    parser.add_argument("--delay", type=float, default=DELAY_S,
                        help=f"Delay entre chamadas (default: {DELAY_S}s)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simula sem chamadas reais")
    args = parser.parse_args()

    backfill_resume(
        end            = date.fromisoformat(args.end),
        fallback_start = date.fromisoformat(args.fallback_start),
        delay          = args.delay,
        dry_run        = args.dry_run,
    )
