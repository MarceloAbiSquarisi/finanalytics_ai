"""startup/kafka.py — Kafka consumer."""
from __future__ import annotations
import asyncio
from typing import Any
import structlog

log = structlog.get_logger(__name__)


async def init_kafka(app, alert_service: Any, timescale_ok: bool) -> tuple[Any, Any]:
    try:
        from finanalytics_ai.infrastructure.queue.kafka_adapter import KafkaMarketEventConsumer
        consumer = KafkaMarketEventConsumer()
        await consumer.start()

        async def _handle(event: Any) -> None:
            from finanalytics_ai.domain.entities.event import EventType, MarketEvent
            if not isinstance(event, MarketEvent):
                return
            if event.event_type == EventType.PRICE_UPDATE and alert_service:
                price = event.payload.get("price")
                if price:
                    triggered = await alert_service.evaluate_price(event.ticker, float(price))
                    if triggered:
                        log.info("alerts.triggered", ticker=event.ticker, count=triggered)
            if event.event_type == EventType.PRICE_UPDATE and timescale_ok:
                await _save_tick(event)

        task = asyncio.create_task(consumer.consume(_handle))
        log.info("kafka.consumer.running")
        return consumer, task
    except Exception as exc:
        log.warning("kafka.unavailable", error=str(exc))
        return None, None


async def _save_tick(event: Any) -> None:
    try:
        from finanalytics_ai.infrastructure.timescale.repository import (
            TimescalePriceTickRepository, get_timescale_pool,
        )
        pool = await get_timescale_pool()
        repo = TimescalePriceTickRepository(pool)
        await repo.save_tick(
            ticker=event.ticker,
            price=float(event.payload.get("price", 0)),
            quantity=int(event.payload.get("quantity", 0)),
            volume=float(event.payload.get("volume", 0)),
            trade_type=int(event.payload.get("trade_type", 0)),
        )
    except Exception as exc:
        log.warning("timescale.tick.save_failed", error=str(exc))


async def shutdown(consumer: Any, task: Any) -> None:
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    if consumer:
        try:
            await consumer.stop()
        except Exception:
            pass
