"""
FinAnalytics AI — Entrypoint principal.

Inicializa: logging → observabilidade → fila → worker de eventos.

Design decision: startup/shutdown explícitos com lifespan async.
Evita globals mutáveis — passa dependências construídas para baixo.
"""
from __future__ import annotations

import asyncio
import signal
import structlog

from finanalytics_ai.config import get_settings
from finanalytics_ai.logging_config import configure_logging
from finanalytics_ai.observability import setup_metrics, setup_tracing
from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient
from finanalytics_ai.infrastructure.queue.event_queue import InMemoryEventQueue

logger = structlog.get_logger(__name__)


async def run_event_worker(queue: InMemoryEventQueue) -> None:
    """Worker assíncrono que consome eventos da fila."""
    logger.info("event.worker.started")
    while True:
        event = await queue.dequeue()
        try:
            logger.info(
                "event.consuming",
                event_id=event.event_id,
                event_type=event.event_type,
                ticker=event.ticker,
            )
            # TODO: injetar EventProcessorService e chamar process()
        except Exception as exc:
            logger.error("event.worker.error", error=str(exc))
        finally:
            queue.task_done()


async def main() -> None:
    configure_logging()
    settings = get_settings()

    logger.info(
        "finanalytics_ai.starting",
        env=settings.app_env,
        version="0.1.0",
    )

    # Observabilidade
    tracer = setup_tracing()
    if settings.metrics_enabled:
        setup_metrics()

    # Infraestrutura
    queue = InMemoryEventQueue()
    brapi = BrapiClient()

    # Graceful shutdown
    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("finanalytics_ai.shutdown_requested")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    worker_task = asyncio.create_task(run_event_worker(queue))

    logger.info("finanalytics_ai.ready")
    await stop_event.wait()

    worker_task.cancel()
    await brapi.close()
    logger.info("finanalytics_ai.stopped")


if __name__ == "__main__":
    asyncio.run(main())
