"""
backfill_resilient.py — backfill com checkpoint, retry e early-exit cooperativo.

Diferencas vs backfill_today_subscribed.py:
  - State file (`/data/backfill_resilient_state.json`) persiste status por
    (day, ticker). Re-runs pulam tickers ja' done/skip.
  - Per-ticker max_attempts (default 3). Falhas adicionam attempts.
  - Consecutive_errors >= 5 -> exit_code=2 ("agente stuck, supervisor restart").
  - SIGINT/SIGTERM salva state e exit 0.
  - Heartbeat a cada 30s no log (dashboard mostra agent vivo).
  - Atomic state save (tmp + rename).

Exit codes:
  0 = DONE (sucesso ou ja' completo)
  1 = Erro fatal (DB unreachable, agent down, etc)
  2 = Agent stuck — supervisor deve restart agent e re-rodar

Args:
  --day YYYY-MM-DD   Data alvo (default: latest_trading_day)
  --tickers FOO,BAR  Limita a subset (default: profit_subscribed_tickers active)
  --reset            Apaga state file e comeca do zero
  --max-attempts N   Max tentativas por ticker (default 3)
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

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# .env loader (rodar standalone fora do container)
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
DB_DSN = os.getenv(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)

# Timeouts curtos (DLL Nelogica nao tem CancelHistory; ticker stuck = 60s wasted, ok).
TIMEOUT_S = 60
TIMEOUT_FUT = 300
DELAY_S = 2
HEARTBEAT_INTERVAL_S = 30
CONSECUTIVE_ERROR_THRESHOLD = 5
MAX_ATTEMPTS_DEFAULT = 3

FUTURES_EXCHANGE = {"F"}
EXCLUDE = {"WINFUT", "WDOFUT", "WINM26", "WINK26", "WDOM26", "WDON26", "XPTO"}
HOLIDAYS_BR_2026: set[date] = {
    date(2026, 1, 1),
    date(2026, 2, 16), date(2026, 2, 17),
    date(2026, 4, 3), date(2026, 4, 21),
    date(2026, 5, 1), date(2026, 6, 4),
    date(2026, 9, 7), date(2026, 10, 12),
    date(2026, 11, 2), date(2026, 11, 15),
    date(2026, 12, 25),
}

STATE_FILE = Path(os.environ.get("BACKFILL_STATE_FILE", "/data/backfill_resilient_state.json"))

EXIT_OK = 0
EXIT_FATAL = 1
EXIT_AGENT_STUCK = 2

# ---------------------------------------------------------------------------
# Util
# ---------------------------------------------------------------------------

_stop = False


def _on_signal(signum, _frame):
    global _stop
    _stop = True
    emit("SIGNAL", sig=signum, will_save_and_exit=True)


def emit(tag: str, **kwargs) -> None:
    parts = [tag] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" ".join(parts), flush=True)


def pushover_alert(title: str, message: str, *, critical: bool = False) -> bool:
    """Push direto via API Pushover — usado em terminus do script (DONE c/
    err, AGENT_STUCK, STOPPED_BY_SIGNAL incompleto, FATAL).

    Standalone — nao depende do FastAPI app. Lê creds das mesmas env vars
    que o app (PUSHOVER_USER_KEY + PUSHOVER_APP_TOKEN).

    Fire-and-forget: erros sao logados via emit() mas nao quebram o exit
    do script.
    """
    user = os.environ.get("PUSHOVER_USER_KEY", "").strip()
    token = os.environ.get("PUSHOVER_APP_TOKEN", "").strip()
    if not (user and token):
        emit("PUSHOVER_SKIP", reason="missing_creds")
        return False
    try:
        data = json.dumps({
            "token": token,
            "user": user,
            "title": f"FinAnalytics: {title}",
            "message": message[:1024],
            "priority": 1 if critical else 0,
            "sound": "siren" if critical else None,
        }).encode("utf-8")
        # api Pushover aceita JSON com Content-Type correto
        req = urllib.request.Request(
            "https://api.pushover.net/1/messages.json",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            ok = 200 <= r.status < 300
            emit("PUSHOVER", sent=ok, code=r.status, critical=critical)
            return ok
    except Exception as exc:
        emit("PUSHOVER_ERR", err=type(exc).__name__, msg=str(exc)[:120])
        return False


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS_BR_2026


def latest_trading_day(now: datetime | None = None) -> date:
    now = now or datetime.now()
    today = now.date()
    if is_trading_day(today) and now.hour >= 18:
        return today
    d = today - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


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
    except urllib.error.HTTPError as exc:
        return (0, 0, f"http_{exc.code}", time.time() - t0)
    except urllib.error.URLError as exc:
        return (0, 0, f"url_err_{type(exc.reason).__name__}", time.time() - t0)
    except Exception as exc:
        return (0, 0, f"err_{type(exc).__name__}", time.time() - t0)


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def load_state(day: date) -> dict:
    if not STATE_FILE.exists():
        return _empty_state(day)
    try:
        s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if s.get("day") != day.isoformat():
            emit("STATE_DAY_MISMATCH", saved_day=s.get("day"), target_day=day.isoformat())
            return _empty_state(day)
        return s
    except Exception as exc:
        emit("STATE_LOAD_ERROR", err=str(exc)[:80])
        return _empty_state(day)


def _empty_state(day: date) -> dict:
    return {
        "day": day.isoformat(),
        "started_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "tickers": {},  # ticker -> {status, attempts, ticks, inserted, last_error, last_elapsed_s}
        "summary": {"ok": 0, "skip": 0, "err": 0, "abort_count": 0},
    }


def save_state(state: dict) -> None:
    state["updated_at"] = datetime.now().isoformat()
    tmp = STATE_FILE.with_suffix(".tmp")
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception as exc:
        emit("STATE_SAVE_ERROR", err=str(exc)[:80])


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", type=date.fromisoformat, default=None)
    parser.add_argument("--tickers", default=None, help="Comma-separated subset")
    parser.add_argument("--reset", action="store_true", help="Apaga state file")
    parser.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS_DEFAULT)
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    day = args.day or latest_trading_day()

    if args.reset and STATE_FILE.exists():
        STATE_FILE.unlink()
        emit("STATE_RESET", file=str(STATE_FILE))

    state = load_state(day)
    all_tickers = get_subscribed_tickers()
    if args.tickers:
        wanted = set(args.tickers.split(","))
        all_tickers = [t for t in all_tickers if t["ticker"] in wanted]

    # Filtra ja' processados (status ok/skip e attempts < max)
    pending = []
    auto_skip_attempts = 0
    for t in all_tickers:
        ts = state["tickers"].get(t["ticker"])
        if ts and ts.get("status") in ("ok", "skip"):
            continue
        if ts and ts.get("attempts", 0) >= args.max_attempts:
            auto_skip_attempts += 1
            continue
        pending.append(t)

    emit(
        "START",
        day=day.isoformat(),
        agent=AGENT_URL,
        total=len(all_tickers),
        pending=len(pending),
        already_done=len(all_tickers) - len(pending) - auto_skip_attempts,
        skipped_max_attempts=auto_skip_attempts,
        max_attempts=args.max_attempts,
    )

    consecutive_errors = 0
    last_heartbeat = time.time()

    for i, t in enumerate(pending, 1):
        if _stop:
            remaining = len(pending) - i + 1
            emit("STOPPED_BY_SIGNAL", processed=i - 1, remaining=remaining)
            save_state(state)
            s = state["summary"]
            pushover_alert(
                title=f"Backfill {day.isoformat()} interrompido (signal)",
                message=(
                    f"Coleta diária parada por sinal antes de completar.\n"
                    f"Processados: {i - 1}/{len(pending)} · Restantes: {remaining}\n"
                    f"OK={s['ok']} skip={s['skip']} err={s['err']}"
                ),
                critical=True,
            )
            return EXIT_OK

        ticker = t["ticker"]
        exchange = t["exchange"]

        # already_in_db check (idempotencia secundaria; primaria e' state file)
        if already_in_db(ticker, day):
            state["tickers"][ticker] = {
                "status": "skip",
                "reason": "already_in_db",
                "attempts": state["tickers"].get(ticker, {}).get("attempts", 0),
            }
            state["summary"]["skip"] += 1
            emit("SKIP", ticker=ticker, day=day.isoformat(), reason="already_in_db", i=i, n=len(pending))
            if i % 20 == 0:
                save_state(state)
            continue

        # Heartbeat
        if time.time() - last_heartbeat > HEARTBEAT_INTERVAL_S:
            emit(
                "HEARTBEAT",
                cur_ticker=ticker,
                processed=i - 1,
                pending=len(pending) - i + 1,
                consecutive_errors=consecutive_errors,
                ts=datetime.now().isoformat(timespec="seconds"),
            )
            last_heartbeat = time.time()

        attempts = state["tickers"].get(ticker, {}).get("attempts", 0)
        ticks, inserted, status, elapsed = collect_one(ticker, exchange, day)
        attempts += 1

        if status == "ok":
            state["tickers"][ticker] = {
                "status": "ok", "attempts": attempts,
                "ticks": ticks, "inserted": inserted,
                "last_elapsed_s": round(elapsed, 1),
            }
            state["summary"]["ok"] += 1
            consecutive_errors = 0
            emit(
                "PROGRESS", ticker=ticker, day=day.isoformat(),
                ticks=ticks, inserted=inserted,
                elapsed_s=round(elapsed, 1), attempt=attempts, i=i, n=len(pending),
            )
        else:
            state["tickers"][ticker] = {
                "status": "err", "attempts": attempts,
                "last_error": status,
                "last_elapsed_s": round(elapsed, 1),
            }
            state["summary"]["err"] += 1
            consecutive_errors += 1
            emit(
                "ERROR", ticker=ticker, day=day.isoformat(),
                status=status, elapsed_s=round(elapsed, 1),
                attempt=attempts, consecutive=consecutive_errors,
                i=i, n=len(pending),
            )

            if consecutive_errors >= CONSECUTIVE_ERROR_THRESHOLD:
                state["summary"]["abort_count"] += 1
                save_state(state)
                emit(
                    "AGENT_STUCK",
                    consecutive_errors=consecutive_errors,
                    last_ticker=ticker,
                    request_action="restart_agent_and_resume",
                )
                pushover_alert(
                    title=f"Backfill {day.isoformat()} ABORTOU — agent stuck",
                    message=(
                        f"{consecutive_errors} erros consecutivos. "
                        f"Último ticker: {ticker}.\n"
                        f"Supervisor deve reiniciar profit_agent e re-rodar backfill.\n"
                        f"Processados: {i}/{len(pending)} · "
                        f"OK={state['summary']['ok']} err={state['summary']['err']}"
                    ),
                    critical=True,
                )
                return EXIT_AGENT_STUCK

        save_state(state)
        time.sleep(DELAY_S)

    save_state(state)
    s = state["summary"]
    emit("DONE", day=day.isoformat(), ok=s["ok"], skip=s["skip"], err=s["err"],
         abort_count=s["abort_count"], state_file=str(STATE_FILE))

    # Pushover quando coleta agendada termina com falhas. Sucesso completo
    # (err==0 E nada pendente) é silencioso por design.
    if s["err"] > 0:
        total_pendente = len(pending)
        processados = s["ok"] + s["skip"] + s["err"]
        incompleto = processados < total_pendente
        pushover_alert(
            title=f"Backfill {day.isoformat()} c/ {s['err']} falha(s)",
            message=(
                f"Coleta diária terminou com erros.\n"
                f"OK={s['ok']} skip={s['skip']} err={s['err']}"
                + (f" · {total_pendente - processados} item(s) não processado(s)" if incompleto else "")
                + f"\nState file: {STATE_FILE.name}"
            ),
            critical=False,
        )
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        emit("FATAL", err=type(exc).__name__, msg=str(exc)[:200])
        pushover_alert(
            title="Backfill resilient FATAL",
            message=(
                f"Script abortou com exception {type(exc).__name__}: "
                f"{str(exc)[:300]}\nVerifique logs do NSSM service "
                f"FinAnalyticsBackfill."
            ),
            critical=True,
        )
        sys.exit(EXIT_FATAL)
