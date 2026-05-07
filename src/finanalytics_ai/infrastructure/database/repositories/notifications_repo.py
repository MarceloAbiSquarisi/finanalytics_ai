"""asyncpg CRUD para notification_settings + notifications_log.

Tabelas em Postgres principal (:5432, db finanalytics).

Cache em memoria das settings (TTL curto) p/ evitar 1 hit/notificacao.
"""

from __future__ import annotations

import os
import time
from typing import Any

import asyncpg
import structlog

logger = structlog.get_logger(__name__)


def _normalize_dsn(raw: str) -> str:
    return raw.replace("postgresql+asyncpg://", "postgres://").replace(
        "postgresql://", "postgres://"
    )


_PG_DSN = _normalize_dsn(
    os.getenv("DATABASE_URL_SYNC")
    or os.getenv("DATABASE_URL", "")
    or "postgres://finanalytics:secret@postgres:5432/finanalytics"
)

# Cache de settings — TTL 10s suficiente p/ ack de toggle pelo operador.
_SETTINGS_CACHE: dict[str, str] = {}
_SETTINGS_CACHE_TS: float = 0.0
_SETTINGS_TTL_S = 10.0

CATEGORIES = (
    "backfill",
    "scheduler",
    "auto_trader",
    "indicator",
    "system",
    "test",
)


async def _connect() -> asyncpg.Connection:
    return await asyncpg.connect(_PG_DSN, timeout=5)


async def get_settings(*, force_refresh: bool = False) -> dict[str, str]:
    global _SETTINGS_CACHE, _SETTINGS_CACHE_TS
    now = time.time()
    if (
        not force_refresh
        and _SETTINGS_CACHE
        and (now - _SETTINGS_CACHE_TS) < _SETTINGS_TTL_S
    ):
        return dict(_SETTINGS_CACHE)
    try:
        conn = await _connect()
        try:
            rows = await conn.fetch(
                "SELECT key, value FROM notification_settings"
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("notifications_repo.settings_fetch_failed", error=str(exc))
        # Fail-open: assume tudo ligado se DB indisponivel.
        return {"master_enabled": "true"} | {f"cat_{c}": "true" for c in CATEGORIES}
    settings = {r["key"]: r["value"] for r in rows}
    _SETTINGS_CACHE = settings
    _SETTINGS_CACHE_TS = now
    return dict(settings)


async def is_category_enabled(category: str) -> tuple[bool, str | None]:
    """Retorna (enabled, skip_reason)."""
    settings = await get_settings()
    if settings.get("master_enabled", "true").lower() != "true":
        return False, "master_off"
    key = f"cat_{category}"
    val = settings.get(key, "true")
    if val.lower() != "true":
        return False, "category_off"
    return True, None


async def update_setting(
    key: str, value: str, *, updated_by: str | None = None
) -> None:
    global _SETTINGS_CACHE_TS
    conn = await _connect()
    try:
        await conn.execute(
            """
            INSERT INTO notification_settings (key, value, updated_at, updated_by)
            VALUES ($1, $2, NOW(), $3)
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value,
                  updated_at = NOW(),
                  updated_by = EXCLUDED.updated_by
            """,
            key, value, updated_by,
        )
    finally:
        await conn.close()
    _SETTINGS_CACHE_TS = 0.0  # invalida cache


async def log_notification(
    *,
    category: str,
    title: str,
    message: str,
    priority: int,
    critical: bool,
    outcome: str,
    skip_reason: str | None = None,
    error_msg: str | None = None,
) -> None:
    try:
        conn = await _connect()
        try:
            await conn.execute(
                """
                INSERT INTO notifications_log
                  (category, title, message, priority, critical, outcome,
                   skip_reason, error_msg)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                category, title[:250], message, priority, critical, outcome,
                skip_reason, error_msg,
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("notifications_repo.log_failed", error=str(exc))


async def list_log(
    *,
    limit: int = 100,
    category: str | None = None,
    outcome: str | None = None,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 500))
    where: list[str] = []
    args: list[Any] = []
    if category:
        args.append(category)
        where.append(f"category = ${len(args)}")
    if outcome:
        args.append(outcome)
        where.append(f"outcome = ${len(args)}")
    args.append(limit)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT id, sent_at, category, title, message, priority, critical,
               outcome, skip_reason, error_msg
        FROM notifications_log
        {where_sql}
        ORDER BY sent_at DESC
        LIMIT ${len(args)}
    """
    conn = await _connect()
    try:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def stats(*, days: int = 7) -> dict[str, Any]:
    days = max(1, min(int(days), 90))
    conn = await _connect()
    try:
        total_row = await conn.fetchrow(
            """
            SELECT
              count(*) FILTER (WHERE outcome='sent')    AS sent,
              count(*) FILTER (WHERE outcome='skipped') AS skipped,
              count(*) FILTER (WHERE outcome='failed')  AS failed,
              count(*)                                  AS total
            FROM notifications_log
            WHERE sent_at >= NOW() - ($1::int || ' days')::interval
            """,
            days,
        )
        cat_rows = await conn.fetch(
            """
            SELECT category,
                   count(*) FILTER (WHERE outcome='sent')    AS sent,
                   count(*) FILTER (WHERE outcome='skipped') AS skipped,
                   count(*) FILTER (WHERE outcome='failed')  AS failed
            FROM notifications_log
            WHERE sent_at >= NOW() - ($1::int || ' days')::interval
            GROUP BY category
            ORDER BY category
            """,
            days,
        )
    finally:
        await conn.close()
    return {
        "days": days,
        "totals": dict(total_row) if total_row else {"sent": 0, "skipped": 0, "failed": 0, "total": 0},
        "by_category": [dict(r) for r in cat_rows],
    }
