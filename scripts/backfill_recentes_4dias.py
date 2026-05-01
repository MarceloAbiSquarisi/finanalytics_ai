"""
backfill_recentes_4dias.py - Re-coleta watchlist para dias com cobertura baixa.

Contexto (17/abr/2026 ~17:30 BRT):
  Auditoria em market_history_trades revelou degradacao severa:
    2026-04-13 -> 0.04%  cobertura (catastrofico)
    2026-04-14 -> 1.84%  cobertura
    2026-04-15 -> 14.16% cobertura
    2026-04-16 -> 9.84%  cobertura
  Causa: 5 bugs no /collect_history do profit_agent (filtro de ticker ausente,
  contaminacao realtime, race no done.set()). Patch aplicado 17/abr/2026.

Objetivo: re-probe dos 4 dias para ~132 tickers VERDE/AMARELO da watchlist.
ON CONFLICT (ticker, trade_date, trade_number) DO NOTHING no INSERT do agent
garante idempotencia - dados parciais existentes sao preservados, ticks
novos (faltantes) sao adicionados.

Validacao embutida: cada response traz first.ticker e last.ticker; script
flagga qualquer anomalia (se patch ativo, first/last devem bater com ticker).

Uso:
  python backfill_recentes_4dias.py
  python backfill_recentes_4dias.py --dry-run
  python backfill_recentes_4dias.py --delay 1 --timeout 180
  python backfill_recentes_4dias.py --only "PETR4,VALE3,BBAS3"
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
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
            import os as _os2
            if _k not in _os2.environ:
                _os2.environ[_k] = _v

import os as _os

AGENT_URL = "http://localhost:8002"
TIMEOUT_S = 300
DELAY_S   = 2

DB_DSN = _os.getenv(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
)

# 4 dias-alvo (ter-qua-qui-sex, todos pregoes validos)
DIAS_ALVO = [
    date(2026, 4, 13),
    date(2026, 4, 14),
    date(2026, 4, 15),
    date(2026, 4, 16),
]


def get_watchlist_tickers() -> list[str]:
    """Carrega watchlist VERDE + AMARELO_* ordenada por mediana_vol_brl DESC."""
    try:
        import psycopg2  # type: ignore
        conn = psycopg2.connect(DB_DSN)
        cur  = conn.cursor()
        cur.execute("""
            SELECT ticker
              FROM watchlist_tickers
             WHERE status = 'VERDE' OR status LIKE 'AMARELO_%%'
             ORDER BY mediana_vol_brl DESC NULLS LAST
        """)
        rows = [r[0] for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows
    except Exception as e:
        print(f"[ERRO] Nao conseguiu ler watchlist_tickers do DB: {e}")
        sys.exit(1)


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


def format_dt(d: date, hour: str) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year} {hour}"


def probe_ticker_dia(ticker: str, d: date, timeout: int) -> dict:
    body = {
        "ticker":   ticker,
        "exchange": "B",
        "dt_start": format_dt(d, "09:00:00"),
        "dt_end":   format_dt(d, "18:30:00"),
        "timeout":  timeout,
    }
    return http_post("/collect_history", body, timeout=timeout + 60)


def validar_response(ticker_esperado: str, d: date, resp: dict) -> tuple[str, str]:
    """Retorna (flag, detalhe). flag='OK' ou 'ERR...'/'CONT...'."""
    if "error" in resp:
        return ("ERR_api", resp.get("error", "?"))

    first = resp.get("first") or {}
    last  = resp.get("last")  or {}
    ticks = resp.get("ticks", 0)

    if ticks == 0:
        return ("ZERO", "nenhum tick devolvido")

    first_tk = first.get("ticker", "?")
    last_tk  = last.get("ticker",  "?")
    if first_tk != ticker_esperado or last_tk != ticker_esperado:
        return ("CONT_ticker", f"first={first_tk} last={last_tk}")

    # valida janela (td dentro do dia solicitado, com margem +/- 1 dia p/ TZ)
    try:
        first_dt = datetime.fromisoformat(first["trade_date"].replace("Z", "+00:00"))
        last_dt  = datetime.fromisoformat(last ["trade_date"].replace("Z", "+00:00"))
        if first_dt.date() > d or first_dt.date() < date(d.year, d.month, d.day - 1 if d.day > 1 else d.day):
            return ("CONT_janela", f"first.date={first_dt.date()} esperado~{d}")
        if last_dt.date() > d:
            return ("CONT_janela", f"last.date={last_dt.date()} esperado<={d}")
    except Exception as e:
        return ("WARN_parse", f"nao parseou datas: {e}")

    return ("OK", "")


def backfill(tickers: list[str], delay: float, timeout: int, dry_run: bool) -> None:
    print(f"\n{'='*70}")
    print("BACKFILL 4 DIAS RECENTES - re-coleta pos-patch contaminacao")
    print(f"  Dias     : {', '.join(d.isoformat() for d in DIAS_ALVO)}")
    print(f"  Tickers  : {len(tickers)} (watchlist VERDE+AMARELO)")
    print(f"  Delay    : {delay}s")
    print(f"  Timeout  : {timeout}s por probe")
    print(f"  Dry-run  : {dry_run}")
    print(f"{'='*70}\n")

    # Agent health check
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

    total_calls = len(tickers) * len(DIAS_ALVO)
    print(f"\n[OK] Plano: {total_calls} probes ({len(tickers)} tickers x {len(DIAS_ALVO)} dias)")
    eta_s = total_calls * (delay + 15)  # assume ~15s medio por probe
    print(f"     ETA estimada: {eta_s//60:.0f} min (assumindo ~15s/probe + {delay}s delay)\n")

    done        = 0
    total_ticks = 0
    total_ins   = 0
    errors: list[tuple[str, date, str, str]] = []
    contaminacoes = 0

    # Itera ticker-outer, dia-inner -> mantem cache do DLL por ticker quente
    for ticker in tickers:
        print(f"\n[{datetime.now():%H:%M:%S}] === {ticker} ===")
        ticker_ticks = 0

        for d in DIAS_ALVO:
            done += 1
            pct = done / total_calls * 100
            t0 = time.time()

            print(f"  [{pct:5.1f}%] {ticker} {d.isoformat()} ... ",
                  end="", flush=True)

            if dry_run:
                print("(dry-run)")
                continue

            try:
                resp = probe_ticker_dia(ticker, d, timeout)
            except urllib.error.URLError as e:
                print(f"ERRO_HTTP: {e}")
                errors.append((ticker, d, "HTTP", str(e)))
                time.sleep(delay)
                continue
            except Exception as e:
                print(f"ERRO: {e}")
                errors.append((ticker, d, "EXC", str(e)))
                time.sleep(delay)
                continue

            dt = time.time() - t0
            flag, detalhe = validar_response(ticker, d, resp)
            ticks    = resp.get("ticks", 0)
            inserted = resp.get("inserted", 0)
            v1       = resp.get("v1_count", 0)
            v2       = resp.get("v2_count", 0)

            print(f"{ticks:>6}t {inserted:>6}i v1={v1:>4} v2={v2:>5} "
                  f"{dt:>5.1f}s [{flag}]")
            if flag not in ("OK", "ZERO"):
                print(f"         >> {detalhe}")
                if flag.startswith("CONT"):
                    contaminacoes += 1
                errors.append((ticker, d, flag, detalhe))

            ticker_ticks += ticks
            total_ticks  += ticks
            total_ins    += inserted

            time.sleep(delay)

        print(f"  [{ticker}] subtotal: {ticker_ticks:,} ticks")

    # Resumo
    print(f"\n{'='*70}")
    print("RESUMO")
    print(f"  Probes executados  : {done}/{total_calls}")
    print(f"  Ticks coletados    : {total_ticks:,}")
    print(f"  Ticks inseridos    : {total_ins:,} (resto foi ON CONFLICT DO NOTHING)")
    print(f"  Erros/warns        : {len(errors)}")
    print(f"  Contaminacoes      : {contaminacoes}   <- se > 0, patch NAO esta funcionando")
    if errors:
        print("\n  Primeiros 30 problemas:")
        for tkr, d, flag, det in errors[:30]:
            print(f"    [{flag:12s}] {tkr:8s} {d.isoformat()}: {det}")
        if len(errors) > 30:
            print(f"    ... +{len(errors)-30} omitidos")
    print(f"{'='*70}\n")

    print("Proximo passo:")
    print("  1. Re-auditar cobertura dos 4 dias:")
    print("     docker exec -i finanalytics_timescale psql -U finanalytics -d market_data -c \"")
    print("       SELECT trade_date::date AS dia, count(DISTINCT ticker) tickers,")
    print("              sum(case when tn_max-tn_min>0 then (tn_max-tn_min)/10+1 else 1 end) esperado,")
    print("              count(*) trades")
    print("         FROM (SELECT ticker, trade_date::date, trade_date,")
    print("                      min(trade_number) OVER (PARTITION BY ticker, trade_date::date) tn_min,")
    print("                      max(trade_number) OVER (PARTITION BY ticker, trade_date::date) tn_max")
    print("                 FROM market_history_trades")
    print("                WHERE trade_date::date IN ('2026-04-13','2026-04-14','2026-04-15','2026-04-16')) q")
    print("        GROUP BY trade_date::date ORDER BY trade_date::date;\"")
    print()
    print("  2. Se cobertura >= 99%, re-agregar ohlc_1m desses 4 dias (Fase 1 re-run parcial).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-coleta 4 dias recentes com baixa cobertura")
    parser.add_argument("--delay", type=float, default=DELAY_S,
                        help=f"Delay entre probes (default: {DELAY_S}s)")
    parser.add_argument("--timeout", type=int, default=TIMEOUT_S,
                        help=f"Timeout por probe (default: {TIMEOUT_S}s)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simula sem chamadas reais (lista plano)")
    parser.add_argument("--only", default="",
                        help="CSV de tickers especificos (ignora watchlist do DB)")
    args = parser.parse_args()

    if args.only:
        tickers = [t.strip().upper() for t in args.only.split(",") if t.strip()]
        print(f"[OK] Modo --only: {len(tickers)} tickers fornecidos via CLI")
    else:
        tickers = get_watchlist_tickers()

    backfill(
        tickers = tickers,
        delay   = args.delay,
        timeout = args.timeout,
        dry_run = args.dry_run,
    )
