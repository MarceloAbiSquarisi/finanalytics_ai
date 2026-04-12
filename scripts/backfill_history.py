"""
backfill_history.py — Coleta histórica de 3 meses via profit_agent HTTP API

Funciona como job standalone: chama POST /collect_history para cada
(ticker, dia) dos tickers com active=True em profit_history_tickers.

Configuração via args ou constantes abaixo:
  python backfill_history.py
  python backfill_history.py --start 2026-01-01 --end 2026-03-31
  python backfill_history.py --start 2026-01-01 --end 2026-03-31 --delay 5

Feriados BR configurados em HOLIDAYS_BR.
Resume automaticamente a partir do last_collected_to de cada ticker.
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

# ── Configuração ──────────────────────────────────────────────────────────────
AGENT_URL  = "http://localhost:8002"
TIMEOUT_S  = 150        # timeout por coleta (segundos)
DELAY_S    = 3          # delay entre chamadas (segundos) — evita sobrecarga DLL
CHUNK_DAYS = 1          # dias por chamada (DLL aceita max ~1-2 dias)

# Feriados B3 Jan-Mar 2026 (adicione conforme necessário)
HOLIDAYS_BR: set[date] = {
    date(2026, 1, 1),   # Confraternização Universal
    date(2026, 2, 16),  # Carnaval (segunda)
    date(2026, 2, 17),  # Carnaval (terça)
    date(2026, 2, 18),  # Quarta de Cinzas (até o meio-dia)
}


# ── Funções auxiliares ────────────────────────────────────────────────────────
def is_trading_day(d: date) -> bool:
    """Retorna True se é dia útil B3 (não é fds nem feriado)."""
    return d.weekday() < 5 and d not in HOLIDAYS_BR


def trading_days(start: date, end: date) -> list[date]:
    """Lista todos os pregões entre start e end (inclusive)."""
    days = []
    d = start
    while d <= end:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
    return days


def http_get(path: str) -> dict:
    url = f"{AGENT_URL}{path}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def http_post(path: str, body: dict, timeout: int = 30) -> dict:
    url  = f"{AGENT_URL}{path}"
    data = json.dumps(body).encode("utf-8")
    req  = urllib.request.Request(url, data=data,
                                   headers={"Content-Type": "application/json"},
                                   method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get_active_tickers() -> list[dict]:
    """Retorna tickers com active=True de profit_history_tickers."""
    data = http_get("/history/tickers")
    return [t for t in data.get("tickers", []) if t.get("active")]


def format_dt(d: date, hour: str) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year} {hour}"


# ── Coleta principal ──────────────────────────────────────────────────────────
def backfill(start: date, end: date, delay: float, dry_run: bool) -> None:
    print(f"\n{'='*60}")
    print(f"BACKFILL HISTÓRICO")
    print(f"  Período : {start} → {end}")
    print(f"  Delay   : {delay}s entre chamadas")
    print(f"  Dry-run : {dry_run}")
    print(f"{'='*60}\n")

    # Verifica agent
    try:
        status = http_get("/status")
        if not status.get("market_connected"):
            print("[ERRO] Agent não conectado ao mercado. Verifique profit_agent.")
            sys.exit(1)
        if not status.get("db_connected"):
            print("[AVISO] DB não conectado — last_collected_at não será atualizado.")
        print(f"[OK] Agent online — market={status['market_connected']} db={status['db_connected']}")
    except Exception as e:
        print(f"[ERRO] Agent inacessível: {e}")
        sys.exit(1)

    tickers = get_active_tickers()
    if not tickers:
        print("[ERRO] Nenhum ticker ativo em profit_history_tickers.")
        sys.exit(1)

    print(f"[OK] {len(tickers)} ticker(s) ativos: {[t['ticker'] for t in tickers]}\n")

    all_days = trading_days(start, end)
    print(f"[OK] {len(all_days)} pregões no período\n")

    total_calls  = len(tickers) * len(all_days)
    done_calls   = 0
    total_ticks  = 0
    errors       = []

    for ticker_info in tickers:
        ticker   = ticker_info["ticker"]
        exchange = ticker_info["exchange"]

        # Resume a partir do last_collected_to se disponível E dentro do range
        last_to = ticker_info.get("last_collected_to")
        resume_from = start
        if last_to:
            try:
                # Formato: "YYYY-MM-DD HH:MM:SS+00:00"
                last_date = date.fromisoformat(str(last_to)[:10])
                # Só resume se last_date está DENTRO do range [start, end]
                # Se last_date é posterior ao end, significa que foi coletado
                # fora do range atual — deve coletar o range inteiro
                if start <= last_date <= end:
                    resume_from = last_date + timedelta(days=1)
                    skipped = len([d for d in all_days if d < resume_from])
                    print(f"[{ticker}] Resume a partir de {resume_from} "
                          f"(pulando {skipped} dias já coletados no range)")
                elif last_date > end:
                    print(f"[{ticker}] last_collected_to={last_date} fora do range "
                          f"— coletando range completo")
            except Exception:
                pass

        days_for_ticker = [d for d in all_days if d >= resume_from]
        if not days_for_ticker:
            print(f"[{ticker}] Já coletado até {end} — pulando\n")
            done_calls += len(all_days)
            continue

        print(f"\n[{ticker}:{exchange}] {len(days_for_ticker)} pregões para coletar")
        ticker_ticks = 0

        for d in days_for_ticker:
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
                    "timeout":  TIMEOUT_S,
                }, timeout=TIMEOUT_S + 30)

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

        print(f"  [{ticker}] Total: {ticker_ticks:,} ticks coletados")

    # ── Resumo final ─────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"RESUMO FINAL")
    print(f"  Total de ticks : {total_ticks:,}")
    print(f"  Chamadas OK    : {done_calls - len(errors)}/{done_calls}")
    print(f"  Erros          : {len(errors)}")
    if errors:
        print(f"\n  Erros detalhados:")
        for tkr, d, err in errors:
            print(f"    {tkr} {d}: {err}")
    print(f"{'='*60}\n")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill histórico de ticks")
    parser.add_argument("--start",   default="2026-01-02",
                        help="Data inicial YYYY-MM-DD (default: 2026-01-02)")
    parser.add_argument("--end",     default="2026-03-31",
                        help="Data final YYYY-MM-DD (default: 2026-03-31)")
    parser.add_argument("--delay",   type=float, default=DELAY_S,
                        help=f"Delay entre chamadas em segundos (default: {DELAY_S})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simula sem fazer chamadas reais")
    args = parser.parse_args()

    backfill(
        start   = date.fromisoformat(args.start),
        end     = date.fromisoformat(args.end),
        delay   = args.delay,
        dry_run = args.dry_run,
    )
