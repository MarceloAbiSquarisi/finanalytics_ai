"""
backfill_historico_watchlist.py - Backfill historico completo (2020-hoje).

Universo:
  - Stocks: toda watchlist_tickers com status VERDE ou AMARELO_* (~135 tickers),
    carregados dinamicamente do DB na inicializacao, ordenados por liquidez
    (mediana_vol_brl DESC). Os mais importantes sao processados primeiro.
  - Futuros: WINFUT e WDOFUT por padrao (exchange F, timeout maior).
    Ajustavel via --futures / --no-futures.

Periodo padrao: 2020-01-02 ate hoje (~1500 pregoes, ~40 dias wall-time).

Idempotencia:
  - ON CONFLICT (ticker, trade_date, trade_number) DO NOTHING no INSERT do agent
  - get_collected_dates pula dias ja presentes no banco ao montar o plano
  - Crash + restart retoma graciosamente (feito para rodar sob nssm)

Resiliencia:
  - probe_with_retry: 3 tentativas com backoff 10/20/30s em erros transitorios
  - SIGINT/SIGTERM/SIGBREAK: graceful shutdown entre tickers (para nssm stop)
  - Validacao anti-contaminacao: flag CONT_ticker se first/last != ticker pedido

Uso:
  python backfill_historico_watchlist.py
  python backfill_historico_watchlist.py --start 2022-01-03 --end 2025-12-30
  python backfill_historico_watchlist.py --from-ticker PETR4
  python backfill_historico_watchlist.py --futures WINFUT,WDOFUT,INDFUT,DOLFUT
  python backfill_historico_watchlist.py --no-futures --delay 1
  python backfill_historico_watchlist.py --only "PETR4,VALE3" --dry-run
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import signal
import sys
import time
import urllib.error
import urllib.request

# ----------------------------------------------------------------------------
# .env loader
# ----------------------------------------------------------------------------
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

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
AGENT_URL         = "http://localhost:8002"
TIMEOUT_STOCK_S   = 300     # 5 min por probe de acao
TIMEOUT_FUT_S     = 2400    # 40 min por probe de futuro (volume muito maior)
DELAY_S           = 2
RETRY_MAX         = 3
RETRY_DELAY_BASE  = 10      # segundos, escala por tentativa

DB_DSN = _os.getenv(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)

DEFAULT_FUTURES = ["WINFUT", "WDOFUT"]

# ----------------------------------------------------------------------------
# Feriados B3 2020-2026 (pregao fechado)
# ----------------------------------------------------------------------------
HOLIDAYS_BR: set[date] = {
    # 2020
    date(2020, 1, 1), date(2020, 2, 24), date(2020, 2, 25),
    date(2020, 4, 10), date(2020, 4, 21), date(2020, 5, 1),
    date(2020, 6, 11), date(2020, 7, 9), date(2020, 9, 7),
    date(2020, 10, 12), date(2020, 11, 2), date(2020, 11, 15),
    date(2020, 12, 24), date(2020, 12, 25), date(2020, 12, 31),
    # 2021
    date(2021, 1, 1), date(2021, 1, 25), date(2021, 2, 15),
    date(2021, 2, 16), date(2021, 4, 2),  date(2021, 4, 21),
    date(2021, 5, 1),  date(2021, 6, 3),  date(2021, 9, 7),
    date(2021, 10, 12), date(2021, 11, 2), date(2021, 11, 15),
    date(2021, 12, 24), date(2021, 12, 25), date(2021, 12, 31),
    # 2022
    date(2022, 1, 1),  date(2022, 1, 25), date(2022, 2, 28),
    date(2022, 3, 1),  date(2022, 4, 15), date(2022, 4, 21),
    date(2022, 5, 1),  date(2022, 6, 16), date(2022, 9, 7),
    date(2022, 10, 12), date(2022, 11, 2), date(2022, 11, 15),
    date(2022, 12, 25),
    # 2023
    date(2023, 1, 1),  date(2023, 2, 20), date(2023, 2, 21),
    date(2023, 4, 7),  date(2023, 4, 21), date(2023, 5, 1),
    date(2023, 6, 8),  date(2023, 9, 7),  date(2023, 10, 12),
    date(2023, 11, 2), date(2023, 11, 15), date(2023, 12, 25),
    # 2024
    date(2024, 1, 1),  date(2024, 1, 25), date(2024, 2, 12),
    date(2024, 2, 13), date(2024, 3, 29), date(2024, 4, 21),
    date(2024, 5, 1),  date(2024, 5, 30), date(2024, 9, 7),
    date(2024, 10, 12), date(2024, 11, 2), date(2024, 11, 15),
    date(2024, 11, 20), date(2024, 12, 24), date(2024, 12, 25),
    date(2024, 12, 31),
    # 2025
    date(2025, 1, 1),  date(2025, 3, 3),  date(2025, 3, 4),
    date(2025, 3, 5),  date(2025, 4, 18), date(2025, 4, 21),
    date(2025, 5, 1),  date(2025, 6, 19), date(2025, 9, 7),
    date(2025, 10, 12), date(2025, 11, 2), date(2025, 11, 15),
    date(2025, 11, 20), date(2025, 12, 24), date(2025, 12, 25),
    date(2025, 12, 31),
    # 2026
    date(2026, 1, 1),  date(2026, 2, 16), date(2026, 2, 17),
    date(2026, 2, 18), date(2026, 4, 3),  date(2026, 4, 21),
    date(2026, 5, 1),  date(2026, 6, 4),  date(2026, 9, 7),
    date(2026, 10, 12), date(2026, 11, 2), date(2026, 11, 15),
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


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
def http_get(path: str) -> dict:
    with urllib.request.urlopen(f"{AGENT_URL}{path}", timeout=30) as r:
        return json.loads(r.read())


def http_post(path: str, body: dict, timeout: int = 30) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{AGENT_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def format_dt(d: date, hour: str) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year} {hour}"


def probe_with_retry(ticker: str, exchange: str, d: date, timeout: int):
    """Retorna (resp, err). Retenta em URLError/Timeout transitorios."""
    body = {
        "ticker":   ticker,
        "exchange": exchange,
        "dt_start": format_dt(d, "09:00:00"),
        "dt_end":   format_dt(d, "18:30:00"),
        "timeout":  timeout,
    }
    last_err = None
    for attempt in range(RETRY_MAX):
        try:
            resp = http_post("/collect_history", body, timeout=timeout + 60)
            return resp, None
        except urllib.error.URLError as e:
            last_err = f"URLError: {e}"
        except (TimeoutError, ConnectionError) as e:
            last_err = f"{type(e).__name__}: {e}"
        except Exception as e:
            # Erro nao-transitorio: falha rapido
            return None, f"{type(e).__name__}: {e}"
        if attempt < RETRY_MAX - 1:
            sleep_s = RETRY_DELAY_BASE * (attempt + 1)
            print(f"         retry {attempt+1}/{RETRY_MAX-1} em {sleep_s}s ({last_err})")
            time.sleep(sleep_s)
    return None, last_err


# ----------------------------------------------------------------------------
# DB
# ----------------------------------------------------------------------------
def get_watchlist_tickers() -> list[str]:
    """Carrega watchlist VERDE + AMARELO_* ordenada por liquidez DESC."""
    import psycopg2  # type: ignore
    try:
        conn = psycopg2.connect(DB_DSN)
        cur  = conn.cursor()
        cur.execute("""
            SELECT ticker
              FROM watchlist_tickers
             WHERE status = 'VERDE' OR status LIKE 'AMARELO_%%'
             ORDER BY mediana_vol_brl DESC NULLS LAST
        """)
        rows = [r[0] for r in cur.fetchall()]
        cur.close(); conn.close()
        return rows
    except Exception as e:
        print(f"[ERRO] Nao conseguiu ler watchlist_tickers: {e}")
        sys.exit(1)


def get_collected_dates(ticker: str, start: date, end: date) -> set[date]:
    """Datas que ja tem ticks no banco para o ticker no range."""
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
        cur.close(); conn.close()
        return {r[0] for r in rows}
    except Exception as e:
        print(f"  [AVISO] verificacao banco falhou para {ticker}: {e}")
        return set()


# ----------------------------------------------------------------------------
# Graceful shutdown (para nssm stop)
# ----------------------------------------------------------------------------
_shutdown = False


def _signal_handler(sig, frame):
    global _shutdown
    if _shutdown:
        print("[INFO] Segundo sinal recebido. Encerrando imediatamente.")
        sys.exit(1)
    _shutdown = True
    print(f"\n[INFO] Sinal {sig} recebido. Finalizando ticker atual e saindo...")


def _install_signals() -> None:
    signal.signal(signal.SIGINT, _signal_handler)
    try:
        signal.signal(signal.SIGTERM, _signal_handler)
    except Exception:
        pass
    # Windows (nssm usa Ctrl+Break para stop)
    if hasattr(signal, "SIGBREAK"):
        try:
            signal.signal(signal.SIGBREAK, _signal_handler)  # type: ignore[attr-defined]
        except Exception:
            pass


# ----------------------------------------------------------------------------
# Core
# ----------------------------------------------------------------------------
def backfill(
    start: date,
    end: date,
    delay: float,
    dry_run: bool,
    from_ticker: str,
    include_futures: bool,
    futures: list[str],
    only: list[str] | None,
) -> None:
    print(f"\n{'='*72}")
    print("BACKFILL HISTORICO - watchlist completa + futuros")
    print(f"  Periodo  : {start.isoformat()} -> {end.isoformat()}")
    print(f"  Delay    : {delay}s")
    print(f"  Futuros  : {','.join(futures) if include_futures else '(desativados)'}")
    print(f"  Dry-run  : {dry_run}")
    print(f"  Agent    : {AGENT_URL}")
    print(f"{'='*72}\n")

    # Health check
    try:
        status = http_get("/status")
        if not status.get("market_connected"):
            print("[ERRO] Agent nao conectado ao mercado. Abortando.")
            sys.exit(1)
        print(f"[OK] Agent online - market={status.get('market_connected')} "
              f"db={status.get('db_connected')} assets={status.get('total_assets', '?')}")
    except Exception as e:
        print(f"[ERRO] Agent inacessivel em {AGENT_URL}: {e}")
        sys.exit(1)

    # Universo: stocks (watchlist ou --only) + futuros
    if only:
        stocks = only
        print(f"[OK] Modo --only: {len(stocks)} tickers fornecidos via CLI")
    else:
        stocks = get_watchlist_tickers()
        print(f"[OK] Watchlist: {len(stocks)} stocks (VERDE+AMARELO por liquidez DESC)")

    universe: list[tuple[str, str, int]] = []  # (ticker, exchange, timeout)
    for t in stocks:
        universe.append((t, "B", TIMEOUT_STOCK_S))
    if include_futures:
        for f in futures:
            universe.append((f, "F", TIMEOUT_FUT_S))
        print(f"[OK] Futuros: {len(futures)} ({','.join(futures)}) timeout={TIMEOUT_FUT_S}s")

    # --from-ticker: retoma a partir de um ticker
    if from_ticker:
        idx = None
        for i, (t, _, _) in enumerate(universe):
            if t.upper() == from_ticker.upper():
                idx = i
                break
        if idx is None:
            print(f"[ERRO] --from-ticker {from_ticker} nao esta no universo")
            sys.exit(1)
        print(f"[OK] Retomando de {from_ticker} (pulados {idx} tickers anteriores)")
        universe = universe[idx:]

    # Calendario
    all_days = trading_days(start, end)
    print(f"[OK] {len(all_days)} pregoes uteis no periodo (excl. fins de semana + feriados B3)\n")

    # Monta plano: pula dias ja no banco
    plan: list[tuple[str, str, int, list[date]]] = []
    total_skip = 0
    for ticker, exchange, tmo in universe:
        days = list(all_days)
        if not dry_run:
            ja = get_collected_dates(ticker, start, end)
            if ja:
                before = len(days)
                days = [d for d in days if d not in ja]
                total_skip += (before - len(days))
        if not days:
            print(f"  [{ticker:10s}] em dia - 0 probes")
            continue
        plan.append((ticker, exchange, tmo, days))

    if not plan:
        print("\n[OK] Nada a coletar - universo inteiro em dia.\n")
        return

    total_calls = sum(len(d) for _, _, _, d in plan)
    print(f"\n[OK] Plano: {len(plan)}/{len(universe)} tickers ativos, "
          f"{total_calls:,} probes (puladas {total_skip:,} ja no banco)")
    eta_s = total_calls * (15 + delay)
    print(f"     ETA estimada: {eta_s/3600:.1f}h = {eta_s/86400:.1f} dias "
          f"(supondo ~15s/probe + {delay}s delay)\n")

    done_calls    = 0
    total_ticks   = 0
    total_ins     = 0
    errors: list[tuple[str, date, str]] = []
    contaminacoes = 0

    for ticker, exchange, tmo, days in plan:
        if _shutdown:
            print(f"\n[INFO] Shutdown solicitado. Saindo antes de {ticker}.")
            break
        print(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] === {ticker} "
              f"({exchange}, timeout={tmo}s, {len(days)} dias) ===")
        ticker_ticks = 0

        for d in days:
            if _shutdown:
                print(f"\n[INFO] Shutdown solicitado no meio de {ticker}.")
                break
            done_calls += 1
            pct = done_calls / total_calls * 100
            t0 = time.time()
            print(f"  [{pct:5.1f}%] {ticker} {d.strftime('%Y-%m-%d')} ... ",
                  end="", flush=True)

            if dry_run:
                print("(dry-run)")
                continue

            resp, err = probe_with_retry(ticker, exchange, d, tmo)
            dt = time.time() - t0

            if err:
                print(f"ERRO ({dt:.1f}s): {err}")
                errors.append((ticker, d, err))
                time.sleep(delay)
                continue

            ticks    = resp.get("ticks", 0)
            inserted = resp.get("inserted", 0)
            v1       = resp.get("v1_count", 0)
            v2       = resp.get("v2_count", 0)

            # Validacao anti-contaminacao
            first = resp.get("first") or {}
            last  = resp.get("last")  or {}
            flag = "OK"
            if ticks == 0:
                flag = "ZERO"
            elif first.get("ticker") != ticker or last.get("ticker") != ticker:
                flag = "CONT_ticker"
                contaminacoes += 1
                errors.append((ticker, d,
                               f"CONT first={first.get('ticker')} last={last.get('ticker')}"))

            print(f"{ticks:>8}t {inserted:>8}i v1={v1:>3} v2={v2:>7} "
                  f"{dt:>6.1f}s [{flag}]")

            ticker_ticks += ticks
            total_ticks  += ticks
            total_ins    += inserted
            time.sleep(delay)

        print(f"  [{ticker}] subtotal: {ticker_ticks:,} ticks")

    # Resumo final
    print(f"\n{'='*72}")
    print("RESUMO")
    print(f"  Probes executados   : {done_calls:,}/{total_calls:,}")
    print(f"  Ticks coletados     : {total_ticks:,}")
    print(f"  Ticks inseridos     : {total_ins:,} (resto = ON CONFLICT DO NOTHING)")
    print(f"  Erros/warns         : {len(errors):,}")
    print(f"  Contaminacoes       : {contaminacoes:,}   <- se > 0, patch NAO ativo")
    if errors:
        print("\n  Primeiros 30 erros:")
        for tkr, d, err in errors[:30]:
            print(f"    {tkr:10s} {d.isoformat()}: {err}")
        if len(errors) > 30:
            print(f"    ... +{len(errors)-30} omitidos")
    print(f"{'='*72}\n")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    _install_signals()

    parser = argparse.ArgumentParser(
        description="Backfill historico completo watchlist + futuros (2020-hoje)"
    )
    parser.add_argument("--start", default="2020-01-02",
                        help="Data inicial YYYY-MM-DD (default: 2020-01-02)")
    parser.add_argument("--end", default=date.today().isoformat(),
                        help="Data final YYYY-MM-DD (default: hoje)")
    parser.add_argument("--delay", type=float, default=DELAY_S,
                        help=f"Delay entre probes em segundos (default: {DELAY_S})")
    parser.add_argument("--from-ticker", default="",
                        help="Retoma a partir deste ticker (pula anteriores)")
    parser.add_argument("--only", default="",
                        help="CSV de tickers especificos (ignora watchlist do DB)")
    parser.add_argument("--futures", default=",".join(DEFAULT_FUTURES),
                        help=f"CSV de futuros (default: {','.join(DEFAULT_FUTURES)})")
    parser.add_argument("--no-futures", action="store_true",
                        help="Desativa backfill de futuros")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simula sem chamadas reais (lista plano)")
    args = parser.parse_args()

    futures = [f.strip().upper() for f in args.futures.split(",") if f.strip()]
    only_list = None
    if args.only:
        only_list = [t.strip().upper() for t in args.only.split(",") if t.strip()]

    backfill(
        start           = date.fromisoformat(args.start),
        end             = date.fromisoformat(args.end),
        delay           = args.delay,
        dry_run         = args.dry_run,
        from_ticker     = args.from_ticker,
        include_futures = not args.no_futures,
        futures         = futures,
        only            = only_list,
    )
