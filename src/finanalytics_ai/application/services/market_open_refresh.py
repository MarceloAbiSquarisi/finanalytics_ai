"""B3 market open refresh — atualiza Prometheus gauge a cada 60s.

Sprint Pregão Mute (22/abr/2026) — destrava alert filter
`expr AND on() finanalytics_market_open == 1` em rules market-data
sem precisar de scrape em tempo real.

Background task asyncio iniciado pelo lifespan do FastAPI (app.py).
60s e suficiente — alert rules avaliam a cada minuto, refresh maior
que isso introduziria ate 1min de defasagem na transicao
abertura/fechamento.
"""

from __future__ import annotations

import asyncio
import os

import structlog

from finanalytics_ai.infrastructure.market_calendar import is_market_open
from finanalytics_ai.metrics import market_open

logger = structlog.get_logger(__name__)

REFRESH_SECONDS = int(os.environ.get("MARKET_OPEN_REFRESH_SECONDS", "60"))


def _refresh_once() -> None:
    """Atualiza gauge — sync, idempotente."""
    market_open.set(1 if is_market_open() else 0)


async def refresh_loop() -> None:
    """Loop background — chamada pelo lifespan do FastAPI."""
    logger.info("market_open_refresh.loop.start", interval_s=REFRESH_SECONDS)
    while True:
        try:
            _refresh_once()
        except Exception as exc:
            logger.error("market_open_refresh.cycle_failed", error=str(exc), exc_info=True)
        await asyncio.sleep(REFRESH_SECONDS)
