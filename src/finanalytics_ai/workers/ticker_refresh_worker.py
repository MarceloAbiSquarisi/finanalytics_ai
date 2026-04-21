#!/usr/bin/env python3
"""Ticker Refresh Worker - roda diariamente, atualiza tabela tickers via BRAPI."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format='"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s","worker":"ticker-refresh"',
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
BRAPI_TOKEN = os.environ.get("BRAPI_TOKEN")
REFRESH_HOUR = int(os.environ.get("REFRESH_HOUR", "6"))
RUN_ONCE = os.environ.get("RUN_ONCE", "false").lower() == "true"

if DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


async def run_refresh() -> None:
    logger.info("ticker_refresh.starting")
    sys.path.insert(0, "/app/src")
    from finanalytics_ai.infrastructure.database.repositories.ticker_service import refresh_tickers

    result = await refresh_tickers(DATABASE_URL, BRAPI_TOKEN)
    logger.info(f"ticker_refresh.complete upserted={result['upserted']} total={result['total']}")


async def schedule_loop() -> None:
    logger.info(f"ticker_refresh.scheduler.started refresh_hour={REFRESH_HOUR}")
    await run_refresh()
    while True:
        now = datetime.now(UTC)
        next_run = now.replace(hour=REFRESH_HOUR, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        wait = (next_run - now).total_seconds()
        logger.info(f"ticker_refresh.next_run in={wait:.0f}s")
        await asyncio.sleep(wait)
        await run_refresh()


def main() -> None:
    if not DATABASE_URL:
        logger.error("DATABASE_URL not set")
        sys.exit(1)
    asyncio.run(run_refresh() if RUN_ONCE else schedule_loop())


if __name__ == "__main__":
    main()
