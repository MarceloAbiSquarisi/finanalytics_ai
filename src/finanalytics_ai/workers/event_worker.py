"""
Worker — loop de processamento de eventos.

Entry point: uv run python -m finanalytics_ai.workers.event_worker

Decisão de sessão:
    Fetch e processo usam sessões separadas intencionalmente.
    O SELECT FOR UPDATE SKIP LOCKED adquire row-lock — mantê-lo aberto
    durante o processamento (segundos) bloquearia outros workers.
    Duas sessões curtas + idempotência = correto e performático.
"""

from __future__ import annotations

import asyncio
import signal
import sys

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finanalytics_ai.config import Settings, get_settings
from finanalytics_ai.container import (
    bootstrap,
    build_engine,
    build_event_processor,
    build_session_factory,
)
from finanalytics_ai.infrastructure.database.repositories.event_repository import (
    PostgresEventRepository,
)
from finanalytics_ai.observability.logging import get_logger

log = get_logger(__name__)

POLL_INTERVAL_SECONDS = 5
BATCH_SIZE = 50


async def _process_one_batch(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Fetch + process de um batch em sessões separadas.

    Fase 1 (transação curta): SELECT FOR UPDATE SKIP LOCKED garante que
    dois workers nunca peguem o mesmo evento. Commit libera os locks imediatamente.

    Fase 2 (nova sessão): EventProcessor persiste cada resultado.
    Idempotência via ON CONFLICT é a rede de segurança extra.
    """
    async with session_factory() as session:
        async with session.begin():
            repo = PostgresEventRepository(session)
            events = await repo.get_pending_events(
                limit=BATCH_SIZE,
                for_update_skip_locked=True,
            )

    if not events:
        log.debug("event_worker_no_pending_events")
        return

    log.info("event_worker_batch_fetched", count=len(events))

    async with session_factory() as session:
        async with session.begin():
            processor = build_event_processor(session, settings)
            records = await processor.process_batch(events)

    completed = sum(1 for r in records if r.status.value == "completed")
    dead = sum(1 for r in records if r.status.value == "dead_letter")
    log.info(
        "event_worker_batch_processed",
        total=len(events),
        completed=completed,
        dead_letter=dead,
    )


async def run_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)

    log.info("event_worker_started", poll_interval=POLL_INTERVAL_SECONDS, batch_size=BATCH_SIZE)

    while not stop_event.is_set():
        try:
            await _process_one_batch(session_factory, settings)
        except Exception:
            log.exception("event_worker_loop_error")

        try:
            await asyncio.wait_for(
                asyncio.shield(stop_event.wait()),
                timeout=POLL_INTERVAL_SECONDS,
            )
        except asyncio.TimeoutError:
            pass

    log.info("event_worker_stopped")
    await engine.dispose()


def main() -> None:
    settings = get_settings()
    bootstrap(settings)

    stop_event = asyncio.Event()

    def _handle_signal(sig: int, _: object) -> None:
        log.info("event_worker_shutdown_signal", signal=sig)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        asyncio.run(run_loop(stop_event))
    except KeyboardInterrupt:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
