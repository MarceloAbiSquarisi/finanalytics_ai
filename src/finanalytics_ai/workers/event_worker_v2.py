"""
Event Worker V2 - loop de processamento usando EventProcessorService.

Entry point:
    uv run python -m finanalytics_ai.workers.event_worker_v2

Design de sessoes:
    SqlEventRepository gerencia suas proprias sessoes internamente.
    O worker passa apenas a session_factory -- nao abre sessoes externas.
    Isso evita conflito de contexto de sessao (o bug anterior).

Concorrencia:
    asyncio.Semaphore(concurrency) garante que no maximo N eventos
    sejam processados em paralelo -- previne burst de conexoes no pool.

Graceful shutdown:
    SIGTERM/SIGINT seta stop_event. O loop aguarda o ciclo atual terminar
    antes de encerrar -- sem eventos perdidos em meio ao processamento.
"""
from __future__ import annotations

import asyncio
import signal
import sys
from typing import NoReturn

import structlog

from finanalytics_ai.application.event_processor.config import EventProcessorConfig
from finanalytics_ai.config import Settings, get_settings
from finanalytics_ai.container_v2 import (
    bootstrap_v2,
    build_engine_v2,
    build_event_processor_service_v2,
    build_session_factory_v2,
)
from finanalytics_ai.domain.events.models import EventStatus
from finanalytics_ai.infrastructure.event_processor.repository import SqlEventRepository
from finanalytics_ai.observability.correlation import bind_correlation_id, clear_correlation_id

logger = structlog.get_logger(__name__)

POLL_INTERVAL_SECONDS: float = 5.0
BATCH_SIZE: int = 50


async def _fetch_pending(session_factory, batch_size: int) -> list:  # type: ignore[type-arg]
    """
    Busca eventos pendentes.

    SqlEventRepository abre e fecha sua propria sessao internamente.
    Nao criamos sessao externa aqui.
    """
    repo = SqlEventRepository(session_factory)
    return await repo.find_by_status(EventStatus.PENDING, limit=batch_size)


async def _process_batch(
    session_factory,  # type: ignore[type-arg]
    settings: Settings,
    events: list,  # type: ignore[type-arg]
    semaphore: asyncio.Semaphore,
) -> tuple[int, int]:
    """
    Processa batch com concorrencia controlada via Semaphore.
    Retorna (completed_count, dead_letter_count).
    """
    service = build_event_processor_service_v2(session_factory, settings)

    async def _process_one(event) -> object:  # type: ignore[type-arg]
        cid = str(event.event_id)
        bind_correlation_id(cid)
        try:
            async with semaphore:
                return await service.process(event)
        except Exception as exc:
            # TransientError re-raised pelo servico -- nao e falha do worker
            logger.warning(
                "event_worker_v2.event_error",
                event_id=str(event.event_id),
                error=str(exc),
            )
            return None
        finally:
            clear_correlation_id()

    results = await asyncio.gather(*[_process_one(e) for e in events])

    completed = sum(
        1 for r in results
        if r is not None and hasattr(r, "status") and str(r.status) == "completed"
    )
    dead = sum(
        1 for r in results
        if r is not None and hasattr(r, "status") and str(r.status) == "dead_letter"
    )
    return completed, dead


async def run_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    engine = build_engine_v2(settings)
    session_factory = build_session_factory_v2(engine)

    config = EventProcessorConfig()
    semaphore = asyncio.Semaphore(config.concurrency)

    log = logger.bind(
        worker="event_worker_v2",
        poll_interval=POLL_INTERVAL_SECONDS,
        batch_size=BATCH_SIZE,
        concurrency=config.concurrency,
    )
    log.info("event_worker_v2.started")

    while not stop_event.is_set():
        try:
            events = await _fetch_pending(session_factory, BATCH_SIZE)

            if events:
                log.info("event_worker_v2.batch_fetched", count=len(events))
                completed, dead = await _process_batch(
                    session_factory, settings, events, semaphore
                )
                log.info(
                    "event_worker_v2.batch_processed",
                    total=len(events),
                    completed=completed,
                    dead_letter=dead,
                )
            else:
                log.debug("event_worker_v2.no_pending_events")

        except Exception:
            log.exception("event_worker_v2.loop_error")

        try:
            await asyncio.wait_for(
                asyncio.shield(stop_event.wait()),
                timeout=POLL_INTERVAL_SECONDS,
            )
        except asyncio.TimeoutError:
            pass

    log.info("event_worker_v2.stopped")
    await engine.dispose()


def main() -> NoReturn:
    settings = get_settings()
    bootstrap_v2(settings)

    stop_event = asyncio.Event()

    def _handle_signal(sig: int, _: object) -> None:
        logger.info("event_worker_v2.shutdown_signal", signal=sig)
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
