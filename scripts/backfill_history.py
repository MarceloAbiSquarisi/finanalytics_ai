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
from datetime import date, timedelta
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

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

# ── Configuração ──────────────────────────────────────────────────────────────
AGENT_URL  = "http://localhost:8002"
TIMEOUT_S    = 300   # timeout por coleta — ações
TIMEOUT_FUT  = 2400  # timeout futuros (WINFUT/WDOFUT ~500k ticks/dia)
FUTURES_EXCHANGE = {"F"}  # exchanges de futuros
DELAY_S    = 3          # delay entre chamadas (segundos) — evita sobrecarga DLL
CHUNK_DAYS = 1          # dias por chamada (DLL aceita max ~1-2 dias)

# Feriados B3 2026 (adicione conforme necessário)
HOLIDAYS_BR: set[date] = {
    date(2026, 1, 1),   # Confraternização Universal
    date(2026, 2, 16),  # Carnaval (segunda)
    date(2026, 2, 17),  # Carnaval (terça)
    date(2026, 2, 18),  # Quarta de Cinzas (até o meio-dia)
    date(2026, 4, 3),   # Sexta-feira Santa
    date(2026, 4, 21),  # Tiradentes
    date(2026, 5, 1),   # Dia do Trabalhador
    date(2026, 6, 4),   # Corpus Christi
    date(2026, 9, 7),   # Independência
    date(2026, 10, 12), # Nossa Sra Aparecida
    date(2026, 11, 2),  # Finados
    date(2026, 11, 15), # Proclamação da República
    date(2026, 11, 20), # Consciência Negra
    date(2026, 12, 25), # Natal
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


# Configuração do banco (mesmo DSN do profit_agent)
import os as _os

DB_DSN = _os.getenv(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
)


def get_collected_dates(ticker: str, exchange: str,
                        start: date, end: date) -> set[date]:
    """
    Consulta market_history_trades diretamente via psycopg2
    para saber quais datas já têm ticks.
    """
    try:
        import psycopg2  # type: ignore
        conn = psycopg2.connect(DB_DSN)
        cur  = conn.cursor()
        cur.execute("""
            SELECT DISTINCT trade_date::date
            FROM market_history_trades
            WHERE ticker = %s
              AND trade_date::date BETWEEN %s AND %s
            ORDER BY 1
        """, (ticker, start.isoformat(), end.isoformat()))
        rows  = cur.fetchall()
        cur.close()
        conn.close()
        return {row[0] for row in rows}
    except Exception as e:
        print(f"  [AVISO] Não foi possível verificar datas no banco: {e}")
        return set()


def http_post(path: str, body: dict, timeout: int = 30) -> dict:
    url  = f"{AGENT_URL}{path}"
    data = json.dumps(body).encode("utf-8")
    req  = urllib.request.Request(url, data=data,
                                   headers={"Content-Type": "application/json"},
                                   method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get_active_tickers() -> list[dict]:
    """Retorna tickers com active=True direto do banco (não depende do agent)."""
    # Tenta primeiro via banco direto (mais confiável)
    try:
        import psycopg2  # type: ignore
        conn = psycopg2.connect(DB_DSN)
        cur  = conn.cursor()
        cur.execute("""
            SELECT ticker, exchange, collect_from,
                   last_collected_to, last_tick_count, notes
            FROM profit_history_tickers
            WHERE active = TRUE
            ORDER BY ticker
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        result = []
        for r in rows:
            result.append({
                "ticker":           r[0],
                "exchange":         r[1],
                "collect_from":     r[2],
                "last_collected_to": str(r[3]) if r[3] else None,
                "last_tick_count":  r[4],
                "notes":            r[5],
                "active":           True,
            })
        print(f"[OK] {len(result)} ticker(s) ativos via banco direto")
        return result
    except Exception as e:
        print(f"[AVISO] Banco direto falhou ({e}), tentando via agent HTTP...")

    # Fallback: via agent HTTP
    try:
        data = http_get("/history/tickers")
        return [t for t in data.get("tickers", []) if t.get("active")]
    except Exception as e2:
        print(f"[ERRO] Falha ao buscar tickers: {e2}")
        return []


def format_dt(d: date, hour: str) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year} {hour}"


# ── Coleta principal ──────────────────────────────────────────────────────────
def backfill(start: date, end: date, delay: float, dry_run: bool) -> None:
    print(f"\n{'='*60}")
    print("BACKFILL HISTÓRICO")
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

        # Consulta banco para ver datas que já têm dados reais
        if not dry_run:
            print("  Verificando datas já coletadas no banco...", flush=True)
            collected = get_collected_dates(ticker, exchange, start, end)
            if collected:
                before = len(days_for_ticker)
                days_for_ticker = [d for d in days_for_ticker if d not in collected]
                skipped = before - len(days_for_ticker)
                if skipped:
                    print(f"  Pulando {skipped} dias já no banco")

        if not days_for_ticker:
            print(f"[{ticker}] Todos os dias já coletados — pulando\n")
            done_calls += len(all_days)
            continue

        _t_label = f"{TIMEOUT_FUT}s" if exchange in FUTURES_EXCHANGE else f"{TIMEOUT_S}s"
        print(f"\n[{ticker}:{exchange}] {len(days_for_ticker)} pregões para coletar (timeout={_t_label})")
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
                # Futuros têm muito mais ticks — timeout maior
                _t = TIMEOUT_FUT if exchange in FUTURES_EXCHANGE else TIMEOUT_S
                result = http_post("/collect_history", {
                    "ticker":   ticker,
                    "exchange": exchange,
                    "dt_start": dt_start,
                    "dt_end":   dt_end,
                    "timeout":  _t,
                }, timeout=_t + 60)

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
    print("RESUMO FINAL")
    print(f"  Total de ticks : {total_ticks:,}")
    print(f"  Chamadas OK    : {done_calls - len(errors)}/{done_calls}")
    print(f"  Erros          : {len(errors)}")
    if errors:
        print("\n  Erros detalhados:")
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


