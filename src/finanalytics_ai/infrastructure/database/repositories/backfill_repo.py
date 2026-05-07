"""
Repositorio asyncpg para backfill_jobs + backfill_job_items.

Acessa o TimescaleDB (mesmo onde market_history_trades vive) usando a
mesma normalizacao de DSN do admin.py (asyncpg nao aceita
postgresql+asyncpg://). Sem ORM — admin/ohlc/rebuild ja segue esse padrao.

Funcoes principais:
  create_job_with_items  — cria job + items (cartesian tickers x trading days)
  list_jobs              — lista paginada
  get_job                — detalhe + counters
  list_items             — items de 1 job, opcional filter por status
  list_failures          — cross-job: status='err' filtrado por target_date
  next_pending_item      — usado pelo runner pra pegar o proximo item
  start_item / finish_item — atualiza status e counters do job
  cancel_job             — flag cancel_requested = TRUE
  mark_job_running       — started_at + status='running'
  mark_job_finished      — finished_at + status final
  is_job_cancel_requested
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import os
from typing import Any

import asyncpg

_TS_DSN_RAW = (
    os.getenv("TIMESCALE_URL")
    or os.getenv("PROFIT_TIMESCALE_DSN")
    or "postgresql://finanalytics:timescale_secret@timescale:5432/market_data"
)
_TS_DSN = _TS_DSN_RAW.replace("postgresql+asyncpg://", "postgres://").replace(
    "postgresql://", "postgres://"
)


# Feriados B3 — calculados via lib `holidays` (BR + subdiv SP) com cache
# por ano. Inclui:
#   - Feriados nacionais públicos (Confraternização, Tiradentes, Natal, ...)
#   - Carnaval (seg+ter) e Corpus Christi (categoria 'optional', mas B3 fecha)
#   - Quarta-feira de Cinzas (meio-pregão, geralmente skipado em backtest)
#   - Vésperas Natal e Ano-Novo (meio-pregão, geralmente skipado em backtest)
#   - Consciência Negra (nacional desde 2024 via Lei 14.759/2023)
#
# Decisão: tratar dias de meio-pregão como holiday completo. Para backtests
# day-trade, sessoes parciais (10h-14h ou 13h-18h) tem liquidez ruim e
# frequentemente sao excluidas. Backfill esses dias retorna 0 ou poucos
# ticks e fica como ruido em gaps query — preferivel marcar como feriado.
_B3_HOLIDAY_NAMES: frozenset[str] = frozenset({
    "Confraternização Universal",
    "Carnaval",
    "Início da Quaresma",         # Quarta de cinzas (B3 abre 13h)
    "Sexta-feira Santa",
    "Tiradentes",
    "Dia do Trabalhador",
    "Corpus Christi",
    "Independência do Brasil",
    "Nossa Senhora Aparecida",
    "Finados",
    "Proclamação da República",
    # Nacional pela Lei 14.759/2023 (a partir de 2024). Nome canonico
    # da lib `holidays`: "Dia Nacional de Zumbi e da Consciência Negra".
    "Dia Nacional de Zumbi e da Consciência Negra",
    "Véspera de Natal",            # 24/12 — B3 fecha 14h
    "Natal",
    "Véspera de Ano-Novo",         # 31/12 — B3 fecha 14h
})

_B3_HOLIDAYS_CACHE: dict[int, frozenset[date]] = {}


def _b3_holidays_for_year(year: int) -> frozenset[date]:
    cached = _B3_HOLIDAYS_CACHE.get(year)
    if cached is not None:
        return cached
    try:
        import holidays as _hol
        h = _hol.country_holidays(
            "BR", subdiv="SP", years=year,
            categories=("public", "optional"),
        )
        result = frozenset(d for d, name in h.items() if name in _B3_HOLIDAY_NAMES)
    except Exception:
        # Fallback minimo (apenas feriados fixos) se lib nao disponivel.
        result = frozenset({
            date(year, 1, 1),    # Confraternização
            date(year, 4, 21),   # Tiradentes
            date(year, 5, 1),    # Trabalhador
            date(year, 9, 7),    # Independência
            date(year, 10, 12),  # Aparecida
            date(year, 11, 2),   # Finados
            date(year, 11, 15),  # República
            date(year, 12, 25),  # Natal
        })
    _B3_HOLIDAYS_CACHE[year] = result
    return result


def is_b3_holiday(d: date) -> bool:
    return d in _b3_holidays_for_year(d.year)


# ── Dias atipicos B3 — tabela b3_no_trading_days ─────────────────────────────
# Cache em memoria do que tem em b3_no_trading_days (set pequeno, geralmente
# < 100 entradas em decada). Carregado sob demanda via load_b3_no_trading_days
# (idealmente no startup do app, mas tolerante a uso lazy).
_B3_NO_TRADING_DAYS: set[date] = set()
_B3_NO_TRADING_LOADED: bool = False


async def load_b3_no_trading_days() -> None:
    """Recarrega cache de dias atipicos B3 do DB. Idempotente.

    Chamado no startup do app e apos cada insert via mark_b3_no_trading_day.
    """
    global _B3_NO_TRADING_DAYS, _B3_NO_TRADING_LOADED
    try:
        conn = await _connect()
        try:
            rows = await conn.fetch("SELECT target_date FROM b3_no_trading_days")
        finally:
            await conn.close()
        _B3_NO_TRADING_DAYS = {r["target_date"] for r in rows}
        _B3_NO_TRADING_LOADED = True
    except Exception:
        # Fail-open: tabela nao existe ou DB off — usa cache atual (provavelmente
        # vazio). Nunca quebra is_trading_day por causa disso.
        _B3_NO_TRADING_LOADED = True


async def mark_b3_no_trading_day(
    d: date, *, job_id: int | None = None, notes: str | None = None
) -> None:
    """Marca um dia como atipico B3 (sem pregao). Adiciona ao cache.

    Usado pelo backfill_runner quando coleta retorna ok com ticks=0:
    sinal forte de que B3 nao teve pregao naquele dia (apesar de
    weekday < 5 e nao ser feriado oficial).
    """
    try:
        conn = await _connect()
        try:
            await conn.execute(
                """
                INSERT INTO b3_no_trading_days
                    (target_date, discovered_by_job_id, notes)
                VALUES ($1, $2, $3)
                ON CONFLICT (target_date) DO NOTHING
                """,
                d, job_id, notes,
            )
        finally:
            await conn.close()
        _B3_NO_TRADING_DAYS.add(d)
    except Exception:
        pass  # nao bloqueia o caller


def is_b3_no_trading_day(d: date) -> bool:
    return d in _B3_NO_TRADING_DAYS


def is_trading_day(d: date) -> bool:
    return (
        d.weekday() < 5
        and not is_b3_holiday(d)
        and not is_b3_no_trading_day(d)
    )


def trading_days_in_range(start: date, end: date) -> list[date]:
    if end < start:
        return []
    out: list[date] = []
    d = start
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


async def _connect() -> asyncpg.Connection:
    return await asyncpg.connect(_TS_DSN)


# ── jobs CRUD ────────────────────────────────────────────────────────────────


async def create_job_with_items(
    *,
    tickers: list[str],
    date_start: date,
    date_end: date,
    force_refetch: bool,
    requested_by: str | None,
    exchange_for: dict[str, str] | None = None,
    specific_days: list[date] | None = None,
) -> dict[str, Any]:
    """Cria job + items (1 por ticker x dia).

    Por default usa todos os trading_days no range [date_start, date_end].
    Se `specific_days` for fornecido, usa apenas esses (ainda dentro do range).
    Util para "preencher só os gaps" sem tentar dias ja presentes.

    `exchange_for` mapeia ticker -> exchange (default 'B'). Tickers sem mapping
    caem em 'B' (acoes B3).
    """
    if specific_days:
        # Filtra: aceita apenas dias dentro do range informado p/ consistencia
        # com date_start/date_end gravados no job.
        days = sorted({d for d in specific_days if date_start <= d <= date_end})
    else:
        days = trading_days_in_range(date_start, date_end)
    tickers = [t.strip().upper() for t in tickers if t and t.strip()]
    if not tickers:
        raise ValueError("tickers vazio")
    if not days:
        raise ValueError("sem dias no range informado")

    exchange_for = exchange_for or {}
    items_payload = [
        (t, exchange_for.get(t, "B"), d) for d in days for t in tickers
    ]
    total_items = len(items_payload)

    conn = await _connect()
    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO backfill_jobs
                    (status, tickers, date_start, date_end, force_refetch,
                     total_items, requested_by)
                VALUES ('queued', $1::text[], $2, $3, $4, $5, $6)
                RETURNING id, created_at
                """,
                tickers,
                date_start,
                date_end,
                force_refetch,
                total_items,
                requested_by,
            )
            job_id = int(row["id"])
            await conn.executemany(
                """
                INSERT INTO backfill_job_items
                    (job_id, ticker, exchange, target_date, status)
                VALUES ($1, $2, $3, $4, 'pending')
                ON CONFLICT (job_id, ticker, exchange, target_date) DO NOTHING
                """,
                [(job_id, t, ex, d) for (t, ex, d) in items_payload],
            )
        return {
            "id": job_id,
            "created_at": row["created_at"],
            "total_items": total_items,
            "trading_days": len(days),
        }
    finally:
        await conn.close()


async def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    conn = await _connect()
    try:
        rows = await conn.fetch(
            """
            SELECT id, created_at, started_at, finished_at, status,
                   cancel_requested, tickers, date_start, date_end,
                   force_refetch, total_items, done_items, ok_items,
                   err_items, skip_items, requested_by
            FROM backfill_jobs
            ORDER BY id DESC
            LIMIT $1
            """,
            limit,
        )
        return [_row_to_job(r) for r in rows]
    finally:
        await conn.close()


async def get_job(job_id: int) -> dict[str, Any] | None:
    conn = await _connect()
    try:
        row = await conn.fetchrow(
            """
            SELECT id, created_at, started_at, finished_at, status,
                   cancel_requested, tickers, date_start, date_end,
                   force_refetch, total_items, done_items, ok_items,
                   err_items, skip_items, requested_by, notes
            FROM backfill_jobs
            WHERE id = $1
            """,
            job_id,
        )
        return _row_to_job(row) if row else None
    finally:
        await conn.close()


async def list_items(
    job_id: int,
    *,
    status: str | None = None,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    conn = await _connect()
    try:
        if status:
            rows = await conn.fetch(
                """
                SELECT id, job_id, ticker, exchange, target_date, status,
                       ticks_returned, inserted, elapsed_s, error_msg,
                       attempts, started_at, finished_at
                FROM backfill_job_items
                WHERE job_id = $1 AND status = $2
                ORDER BY target_date, ticker
                LIMIT $3
                """,
                job_id, status, limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, job_id, ticker, exchange, target_date, status,
                       ticks_returned, inserted, elapsed_s, error_msg,
                       attempts, started_at, finished_at
                FROM backfill_job_items
                WHERE job_id = $1
                ORDER BY target_date, ticker
                LIMIT $2
                """,
                job_id, limit,
            )
        return [_row_to_item(r) for r in rows]
    finally:
        await conn.close()


async def list_failures(
    *,
    date_start: date,
    date_end: date,
    ticker: str | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    conn = await _connect()
    try:
        if ticker:
            rows = await conn.fetch(
                """
                SELECT id, job_id, ticker, exchange, target_date, status,
                       ticks_returned, inserted, elapsed_s, error_msg,
                       attempts, started_at, finished_at
                FROM backfill_job_items
                WHERE status = 'err'
                  AND target_date BETWEEN $1 AND $2
                  AND ticker = $3
                ORDER BY target_date DESC, ticker
                LIMIT $4
                """,
                date_start, date_end, ticker.upper(), limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, job_id, ticker, exchange, target_date, status,
                       ticks_returned, inserted, elapsed_s, error_msg,
                       attempts, started_at, finished_at
                FROM backfill_job_items
                WHERE status = 'err'
                  AND target_date BETWEEN $1 AND $2
                ORDER BY target_date DESC, ticker
                LIMIT $3
                """,
                date_start, date_end, limit,
            )
        return [_row_to_item(r) for r in rows]
    finally:
        await conn.close()


# ── runner helpers ──────────────────────────────────────────────────────────


async def mark_job_running(job_id: int) -> None:
    conn = await _connect()
    try:
        await conn.execute(
            """
            UPDATE backfill_jobs
               SET status = 'running', started_at = NOW()
             WHERE id = $1 AND status = 'queued'
            """,
            job_id,
        )
    finally:
        await conn.close()


async def mark_job_finished(job_id: int, *, status: str) -> None:
    conn = await _connect()
    try:
        await conn.execute(
            """
            UPDATE backfill_jobs
               SET status = $2, finished_at = NOW()
             WHERE id = $1
            """,
            job_id, status,
        )
    finally:
        await conn.close()


async def cancel_job(job_id: int) -> bool:
    conn = await _connect()
    try:
        out = await conn.execute(
            """
            UPDATE backfill_jobs
               SET cancel_requested = TRUE
             WHERE id = $1 AND status IN ('queued', 'running')
            """,
            job_id,
        )
        return out.endswith(" 1")
    finally:
        await conn.close()


async def is_cancel_requested(job_id: int) -> bool:
    conn = await _connect()
    try:
        v = await conn.fetchval(
            "SELECT cancel_requested FROM backfill_jobs WHERE id = $1",
            job_id,
        )
        return bool(v)
    finally:
        await conn.close()


async def next_pending_item(job_id: int) -> dict[str, Any] | None:
    conn = await _connect()
    try:
        row = await conn.fetchrow(
            """
            SELECT id, ticker, exchange, target_date, attempts
            FROM backfill_job_items
            WHERE job_id = $1 AND status = 'pending'
            ORDER BY target_date, ticker
            LIMIT 1
            """,
            job_id,
        )
        if not row:
            return None
        return {
            "id": int(row["id"]),
            "ticker": row["ticker"],
            "exchange": row["exchange"],
            "target_date": row["target_date"],
            "attempts": int(row["attempts"]),
        }
    finally:
        await conn.close()


async def start_item(item_id: int) -> None:
    conn = await _connect()
    try:
        await conn.execute(
            """
            UPDATE backfill_job_items
               SET status = 'running', started_at = NOW(),
                   attempts = attempts + 1
             WHERE id = $1
            """,
            item_id,
        )
    finally:
        await conn.close()


async def finish_item(
    item_id: int,
    *,
    status: str,  # ok|skip|err
    ticks_returned: int | None = None,
    inserted: int | None = None,
    elapsed_s: float | None = None,
    error_msg: str | None = None,
) -> None:
    conn = await _connect()
    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE backfill_job_items
                   SET status = $2, ticks_returned = $3, inserted = $4,
                       elapsed_s = $5, error_msg = $6, finished_at = NOW()
                 WHERE id = $1
                RETURNING job_id
                """,
                item_id, status, ticks_returned, inserted, elapsed_s, error_msg,
            )
            if not row:
                return
            job_id = int(row["job_id"])
            col = {"ok": "ok_items", "skip": "skip_items", "err": "err_items"}.get(status)
            if col:
                await conn.execute(
                    f"UPDATE backfill_jobs "
                    f"SET done_items = done_items + 1, {col} = {col} + 1 "
                    f"WHERE id = $1",
                    job_id,
                )
    finally:
        await conn.close()


# ── helpers integracao com market_history_trades / ohlc_1m ──────────────────


async def already_has_history(ticker: str, day: date) -> bool:
    """Check em market_history_trades — mesmo predicate do backfill_resilient."""
    conn = await _connect()
    try:
        v = await conn.fetchval(
            "SELECT 1 FROM market_history_trades "
            "WHERE ticker = $1 AND trade_date::date = $2 LIMIT 1",
            ticker, day,
        )
        return v is not None
    except Exception:
        return False
    finally:
        await conn.close()


# ── row mappers ─────────────────────────────────────────────────────────────


def _row_to_job(r: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": int(r["id"]),
        "created_at": _iso(r["created_at"]),
        "started_at": _iso(r.get("started_at")),
        "finished_at": _iso(r.get("finished_at")),
        "status": r["status"],
        "cancel_requested": bool(r.get("cancel_requested", False)),
        "tickers": list(r["tickers"]),
        "date_start": r["date_start"].isoformat() if r["date_start"] else None,
        "date_end": r["date_end"].isoformat() if r["date_end"] else None,
        "force_refetch": bool(r["force_refetch"]),
        "total_items": int(r["total_items"]),
        "done_items": int(r["done_items"]),
        "ok_items": int(r["ok_items"]),
        "err_items": int(r["err_items"]),
        "skip_items": int(r["skip_items"]),
        "requested_by": r.get("requested_by"),
        "notes": r.get("notes") if "notes" in r.keys() else None,
    }


def _row_to_item(r: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": int(r["id"]),
        "job_id": int(r["job_id"]),
        "ticker": r["ticker"],
        "exchange": r["exchange"],
        "target_date": r["target_date"].isoformat() if r["target_date"] else None,
        "status": r["status"],
        "ticks_returned": int(r["ticks_returned"]) if r["ticks_returned"] is not None else None,
        "inserted": int(r["inserted"]) if r["inserted"] is not None else None,
        "elapsed_s": float(r["elapsed_s"]) if r["elapsed_s"] is not None else None,
        "error_msg": r["error_msg"],
        "attempts": int(r["attempts"]),
        "started_at": _iso(r.get("started_at")),
        "finished_at": _iso(r.get("finished_at")),
    }


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)
