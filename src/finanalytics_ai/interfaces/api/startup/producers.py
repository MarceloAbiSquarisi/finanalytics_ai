"""startup/producers.py — BRAPI Price Producer."""
from __future__ import annotations
import asyncio
from typing import Any
import structlog

log = structlog.get_logger(__name__)


async def init_price_producer(app, settings) -> tuple[Any, Any]:
    try:
        from finanalytics_ai.infrastructure.queue.kafka_adapter import KafkaMarketEventProducer
        from finanalytics_ai.application.services.price_update_service import PriceUpdateService
        producer = KafkaMarketEventProducer()
        await producer.start()
        tickers = list(getattr(settings, "watched_tickers", ["PETR4", "VALE3", "ITUB4", "ABEV3"]))
        price_svc = PriceUpdateService(producer=producer, tickers=tickers)

        async def _loop():
            while True:
                try:
                    await price_svc.publish_updates()
                except Exception as exc:
                    log.warning("price_producer.publish_failed", error=str(exc))
                await asyncio.sleep(60)

        task = asyncio.create_task(_loop())
        app.state.price_producer = producer
        log.info("price_producer.ready", tickers=tickers)
        return producer, task
    except Exception as exc:
        log.warning("price_producer.unavailable", error=str(exc))
        return None, None


async def shutdown(producer: Any, task: Any) -> None:
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    if producer:
        try:
            await producer.stop()
        except Exception:
            pass
