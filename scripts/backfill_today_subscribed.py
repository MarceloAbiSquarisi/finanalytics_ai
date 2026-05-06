"""
backfill_today_subscribed.py — coleta o ULTIMO PREGAO de TODOS os tickers
subscritos, idempotente (skip dias ja em DB).

Usado em agendamento noturno: roda 21:00 BRT diariamente, agente fica
com dados completos pra o proximo dia.

Decisoes:
  - Ultimo dia util = hoje se hoje e' dia util e ja passou 18h, senao
    primeiro dia util anterior. Determinacao em `latest_trading_day()`.
  - tickers de profit_subscribed_tickers WHERE active = TRUE.
  - Skip via market_history_trades.COUNT (idempotente).
  - Futuros (exchange='F') usam timeout maior (DLL processa mais lento).
  - EXCLUDE: WINFUT/WINM26 etc — futuros sao backfilled em script proprio.

Output formatado (Monitor grep-friendly):
  START tickers_count=N day=YYYY-MM-DD
  SKIP   ticker=X day=Y reason=already_in_db
  PROGRESS ticker=X day=Y ticks=N inserted=M elapsed_s=T
  ERROR  ticker=X day=Y err=...
  DONE   ok=N skip=M err=K duration_min=T

Como rodar:
  1. Manual (debug):
     docker exec -e PROFIT_AGENT_URL=http://host.docker.internal:8002 \
       -e PROFIT_TIMESCALE_DSN=postgresql://finanalytics:timescale_secret@timescale:5432/market_data \
       finanalytics_api python /app/scripts/backfill_today_subscribed.py

  2. Task Scheduler diario (configurar via install_daily_backfill.ps1).
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

# Carrega .env se existir (para rodar standalone fora do container)
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

AGENT_URL = os.environ.get("PROFIT_AGENT_URL", "http://localhost:8002")
# Reduzido de 300/1200 para 60/300 apos sessao 06/mai 02h: DLL Nelogica nao
# tem CancelHistoryTrade — quando server nao responde para um ticker, DLL
# fica stuck emitindo progress=0 a cada 25s indefinidamente. Cada timeout
# longo so amplifica wall-clock perdido (30 tickers * 300s = 2.5h).
TIMEOUT_S = 60
TIMEOUT_FUT = 300
# Early-exit: se N tickers consecutivos timeoutarem, DLL provavelmente esta
# stuck e proximos N+1.. tambem timeoutarao. Aborta cedo pra restart manual.
MAX_CONSECUTIVE_ERRORS = 5
FUTURES_EXCHANGE = {"F"}
DELAY_S = 2

DB_DSN = os.getenv(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)

# Feriados BR 2026 (lista incremental — adicionar conforme aparecer).
# Backfill diario nao roda em feriados via daily scheduler porque
# latest_trading_day() vai retornar dia util anterior e provavelmente
# ja' tem dados (skip).
HOLIDAYS_BR_2026: set[date] = {
    date(2026, 1, 1),    # Confraternizacao
    date(2026, 2, 16),   # Carnaval (varia)
    date(2026, 2, 17),
    date(2026, 4, 3),    # Sexta-feira Santa
    date(2026, 4, 21),   # Tiradentes
    date(2026, 5, 1),    # Trabalho
    date(2026, 6, 4),    # Corpus Christi
    date(2026, 9, 7),    # Independencia
    date(2026, 10, 12),  # N. Sra. Aparecida
    date(2026, 11, 2),   # Finados
    date(2026, 11, 15),  # Republica
    date(2026, 12, 25),  # Natal
}

# Futuros pesados — backfill_2y_futures.py cobre WINFUT/WDOFUT separado.
EXCLUDE = {"WINFUT", "WDOFUT", "WINM26", "WINK26", "WDOM26", "WDON26", "XPTO"}


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS_BR_2026


def latest_trading_day(now: datetime | None = None) -> date:
    """Retorna o ultimo pregao concluido.

    - Se hoje for dia util e horario >= 18h: hoje (mercado ja fechou).
    - Caso contrario: primeiro dia util anterior.
    """
    now = now or datetime.now()
    today = now.date()
    if is_trading_day(today) and now.hour >= 18:
        return today
    d = today - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def emit(tag: str, **kwargs) -> None:
    parts = [tag] + [f"{k}={v}" for k, v in kwargs.items()]
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


def get_subscribed_tickers() -> list[dict]:
    import psycopg2

    with psycopg2.connect(DB_DSN, connect_timeout=10) as conn, conn.cursor() as cur:
        excl = list(EXCLUDE)
        cur.execute(
            """
            SELECT ticker, exchange FROM profit_subscribed_tickers
            WHERE active = TRUE AND ticker != ALL(%s)
            ORDER BY ticker
            """,
            (excl,),
        )
        return [{"ticker": r[0], "exchange": r[1]} for r in cur.fetchall()]


def already_in_db(ticker: str, day: date) -> bool:
    try:
        import psycopg2

        with psycopg2.connect(DB_DSN, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM market_history_trades "
                "WHERE ticker = %s AND trade_date::date = %s LIMIT 1",
                (ticker, day),
            )
            return cur.fetchone() is not None
    except Exception:
        return False


def collect_one(ticker: str, exchange: str, day: date) -> tuple[int, int, str, float]:
    is_fut = exchange in FUTURES_EXCHANGE
    timeout = TIMEOUT_FUT if is_fut else TIMEOUT_S
    body = {
        "ticker": ticker,
        "exchange": exchange,
        "dt_start": fmt_dt(day, "09:00:00"),
        "dt_end": fmt_dt(day, "18:00:00"),
        "timeout": timeout,
    }
    t0 = time.time()
    try:
        r = http_post("/collect_history", body, timeout=timeout + 30)
        elapsed = time.time() - t0
        return (
            int(r.get("ticks", 0)),
            int(r.get("inserted", 0)),
            str(r.get("status", "?")),
            elapsed,
        )
    except Exception as exc:
        return (0, 0, f"err_{type(exc).__name__}", time.time() - t0)


def main() -> int:
    day = latest_trading_day()
    tickers = get_subscribed_tickers()

    emit("START", tickers_count=len(tickers), day=day.isoformat(), agent=AGENT_URL)

    t0 = time.time()
    ok = 0
    skip = 0
    err = 0
    total_ticks = 0
    consecutive_errors = 0

    for i, t in enumerate(tickers, 1):
        ticker = t["ticker"]
        exchange = t["exchange"]
        if already_in_db(ticker, day):
            skip += 1
            emit("SKIP", ticker=ticker, day=day.isoformat(), reason="already_in_db", i=i)
            continue
        ticks, inserted, status, elapsed = collect_one(ticker, exchange, day)
        if status == "ok":
            ok += 1
            total_ticks += ticks
            consecutive_errors = 0
            emit(
                "PROGRESS",
                ticker=ticker,
                day=day.isoformat(),
                ticks=ticks,
                inserted=inserted,
                elapsed_s=round(elapsed, 1),
                i=i,
            )
        else:
            err += 1
            consecutive_errors += 1
            emit(
                "ERROR",
                ticker=ticker,
                day=day.isoformat(),
                status=status,
                elapsed_s=round(elapsed, 1),
                i=i,
                consecutive_errors=consecutive_errors,
            )
            # Early-exit: DLL provavelmente stuck — abortar pra forca user
            # restart agent + investigar (DLL Nelogica nao tem CancelHistory).
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                emit(
                    "ABORT",
                    reason="consecutive_errors_threshold",
                    threshold=MAX_CONSECUTIVE_ERRORS,
                    last_ticker=ticker,
                    i=i,
                )
                break
        time.sleep(DELAY_S)

    duration_min = round((time.time() - t0) / 60, 1)
    emit(
        "DONE",
        ok=ok,
        skip=skip,
        err=err,
        total_ticks=total_ticks,
        duration_min=duration_min,
    )
    return 0 if err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
