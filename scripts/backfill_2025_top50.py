"""
backfill_2025_top50.py - Cenario B: top 50 tickers por liquidez 2025.

Backfill de 2025-01-02 a 2025-12-30 nos 50 ativos com maior volume diario
medio em 2025 (apurado via fintz_cotacoes_ts). Ranking fixo no script para
reproducibilidade.

Uso:
  python backfill_2025_top50.py
  python backfill_2025_top50.py --start 2025-06-01 --end 2025-12-30
  python backfill_2025_top50.py --delay 5 --dry-run
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

# Carrega .env
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

# Top 50 por volume_negociado medio em 2025 (fintz_cotacoes_ts), excl unidades 11
# Apurado em 16/abr/2026 sobre 2025-01-01..2025-12-31
TOP50_2025 = [
    "VALE3", "PETR4", "ITUB4", "BBAS3", "BBDC4", "B3SA3", "JBSS3", "EMBR3",
    "ABEV3", "WEGE3", "PRIO3", "PETR3", "SBSP3", "ELET3", "RENT3", "SUZB3",
    "ITSA4", "EQTL3", "MBRF3", "LREN3", "MGLU3", "RAIL3", "VBBR3", "BRFS3",
    "EMBJ3", "RADL3", "BBSE3", "HAPV3", "GGBR4", "CRFB3", "RDOR3", "BRAV3",
    "CSAN3", "MRFG3", "VIVT3", "CPLE6", "ASAI3", "ENEV3", "CYRE3", "NTCO3",
    "TOTS3", "CCRO3", "TIMS3", "CMIG4", "UGPA3", "MOTV3", "BBDC3", "MULT3",
    "PSSA3", "SMFT3",
]

HOLIDAYS_BR: set[date] = {
    # 2025
    date(2025, 1, 1), date(2025, 3, 3), date(2025, 3, 4), date(2025, 3, 5),
    date(2025, 4, 18), date(2025, 4, 21), date(2025, 5, 1), date(2025, 6, 19),
    date(2025, 9, 7), date(2025, 10, 12), date(2025, 11, 2), date(2025, 11, 15),
    date(2025, 11, 20), date(2025, 12, 24), date(2025, 12, 25), date(2025, 12, 31),
    # 2026
    date(2026, 1, 1), date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
    date(2026, 4, 3), date(2026, 4, 21), date(2026, 5, 1), date(2026, 6, 4),
    date(2026, 9, 7), date(2026, 10, 12), date(2026, 11, 2), date(2026, 11, 15),
    date(2026, 11, 20), date(2026, 12, 25),
}


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS_BR


def trading_days(start: date, end: date) -> list[date]:
    days, d = [], start
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


def get_collected_dates(ticker: str, start: date, end: date) -> set[date]:
    """Datas que ja tem ticks no banco no range."""
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


def backfill(start: date, end: date, delay: float, dry_run: bool) -> None:
    print(f"\n{'='*60}")
    print(f"BACKFILL 2025+2026 - TOP 50 (Cenario B)")
    print(f"  Periodo  : {start} -> {end}")
    print(f"  Tickers  : {len(TOP50_2025)} (ranking fintz_cotacoes_ts 2025)")
    print(f"  Delay    : {delay}s")
    print(f"  Dry-run  : {dry_run}")
    print(f"{'='*60}\n")

    try:
        status = http_get("/status")
        if not status.get("market_connected"):
            print("[ERRO] Agent nao conectado ao mercado.")
            sys.exit(1)
        print(f"[OK] Agent online - market={status['market_connected']} db={status['db_connected']}")
    except Exception as e:
        print(f"[ERRO] Agent inacessivel: {e}")
        sys.exit(1)

    all_days = trading_days(start, end)
    print(f"[OK] {len(all_days)} pregoes no periodo")

    plan: list[tuple[str, list[date]]] = []
    total_skip = 0
    for ticker in TOP50_2025:
        days = list(all_days)
        if not dry_run:
            ja = get_collected_dates(ticker, start, end)
            if ja:
                before = len(days)
                days = [d for d in days if d not in ja]
                total_skip += (before - len(days))
        if not days:
            print(f"[{ticker}] em dia - nada a fazer")
            continue
        plan.append((ticker, days))

    if not plan:
        print("\n[OK] Nada a coletar - todos em dia.\n")
        return

    total_calls = sum(len(d) for _, d in plan)
    print(f"\n[OK] Plano: {len(plan)} tickers, {total_calls} chamadas (puladas {total_skip} ja no banco)")
    eta_h = total_calls * 30 / 3600
    print(f"     ETA estimada: {eta_h:.1f} horas (assumindo 30s/call)\n")

    done_calls  = 0
    total_ticks = 0
    errors: list[tuple[str, date, str]] = []

    for ticker, days in plan:
        # Stocks da TOP50 sao todos exchange B
        exchange = "B"
        timeout_call = TIMEOUT_S
        print(f"\n[{ticker}] timeout={timeout_call}s | {len(days)} dias")
        ticker_ticks = 0

        for d in days:
            done_calls += 1
            pct = done_calls / total_calls * 100
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
        for tkr, d, err in errors[:50]:  # cap em 50 linhas
            print(f"    {tkr} {d}: {err}")
        if len(errors) > 50:
            print(f"    ... +{len(errors)-50} erros omitidos")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill 2025 Top 50")
    parser.add_argument("--start", default="2025-01-02",
                        help="Data inicial YYYY-MM-DD (default: 2025-01-02)")
    parser.add_argument("--end",   default=date.today().isoformat(),
                        help="Data final YYYY-MM-DD (default: hoje)")
    parser.add_argument("--delay", type=float, default=DELAY_S,
                        help=f"Delay entre chamadas (default: {DELAY_S}s)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simula sem chamadas reais")
    args = parser.parse_args()

    backfill(
        start   = date.fromisoformat(args.start),
        end     = date.fromisoformat(args.end),
        delay   = args.delay,
        dry_run = args.dry_run,
    )
