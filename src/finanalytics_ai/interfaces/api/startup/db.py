"""startup/db.py — PostgreSQL e TimescaleDB."""

from __future__ import annotations

import subprocess

import structlog

log = structlog.get_logger(__name__)


async def init_postgres(app) -> None:
    from finanalytics_ai.infrastructure.database.connection import get_engine

    get_engine()
    log.info("postgres.connected")
    try:
        from finanalytics_ai.infrastructure.database.connection import get_session as _gs
        from finanalytics_ai.interfaces.api.routes.admin import run_bootstrap

        async with _gs() as session:
            result = await run_bootstrap(session)
            log.info("bootstrap.master", result=result)
    except Exception as exc:
        log.warning("bootstrap.FAILED", error=str(exc))


async def init_timescale() -> bool:
    try:
        from finanalytics_ai.infrastructure.timescale.repository import get_timescale_pool

        await get_timescale_pool()
        log.info("timescale.connected")
        _warmup_chunk()
        return True
    except Exception as exc:
        log.warning("timescale.unavailable", error=str(exc))
        return False


def _warmup_chunk() -> None:
    sql = (
        "INSERT INTO ticks (ticker,exchange,ts,trade_number,price,quantity,volume,trade_type) "
        "VALUES ('__warmup__','B',now(),0,1.0,1,1.0,0) ON CONFLICT DO NOTHING; "
        "DELETE FROM ticks WHERE ticker='__warmup__';"
    )
    try:
        subprocess.run(
            [
                "docker",
                "exec",
                "finanalytics_timescale",
                "psql",
                "-U",
                "finanalytics",
                "-d",
                "market_data",
                "--no-psqlrc",
                "-c",
                sql,
            ],
            capture_output=True,
            timeout=10,
        )
        log.info("timescale.chunk.warmup.ok")
    except Exception as exc:
        log.warning("timescale.chunk.warmup.failed", error=str(exc))


async def shutdown(timescale_ok: bool) -> None:
    if timescale_ok:
        try:
            from finanalytics_ai.infrastructure.timescale.repository import close_timescale_pool

            await close_timescale_pool()
        except Exception:
            pass
    try:
        from finanalytics_ai.infrastructure.timescale.connection import close_ts_pool

        await close_ts_pool()
    except Exception:
        pass
    from finanalytics_ai.infrastructure.database.connection import close_engine

    await close_engine()
