"""
FastAPI application factory — Sprint 6: Alertas + Notificações SSE.

Lifespan:
  1. PostgreSQL engine
  2. TimescaleDB pool
  3. NotificationBus (singleton em memória)
  4. AlertService (singleton, injetado no Kafka handler)
  5. Kafka consumer — avalia alertas a cada PRICE_UPDATE
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from finanalytics_ai.config import EventQueueBackend, get_settings
from finanalytics_ai.exceptions import FinAnalyticsError
from finanalytics_ai.infrastructure.database.connection import close_engine, get_engine
from finanalytics_ai.interfaces.api.routes import dashboard, health, portfolio, quotes, events, alerts

logger = structlog.get_logger(__name__)

# ── Singletons globais ────────────────────────────────────────────────────────
_kafka_consumer: Any = None
_kafka_task: Any = None
_alert_service: Any = None


def get_kafka_consumer() -> Any:
    return _kafka_consumer

def get_alert_service() -> Any:
    return _alert_service


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _kafka_consumer, _kafka_task, _alert_service

    settings = get_settings()
    logger.info("api.starting", env=settings.app_env)

    # ── 1. PostgreSQL ─────────────────────────────────────────────────────────
    get_engine()
    logger.info("postgres.connected")

    # ── 2. TimescaleDB ────────────────────────────────────────────────────────
    timescale_ok = False
    try:
        from finanalytics_ai.infrastructure.timescale.repository import get_timescale_pool
        await get_timescale_pool()
        timescale_ok = True
        logger.info("timescale.connected")
    except Exception as exc:
        logger.warning("timescale.unavailable", error=str(exc))

    # ── 3. NotificationBus + AlertService ─────────────────────────────────────
    from finanalytics_ai.infrastructure.notifications import get_notification_bus
    from finanalytics_ai.infrastructure.database.connection import get_session as get_async_session
    from finanalytics_ai.application.services.alert_service import AlertService

    bus = get_notification_bus()
    _alert_service = AlertService(
        session_factory=get_async_session,
        notification_bus=bus,
    )
    logger.info("alert_service.ready")

    # ── 4. Kafka consumer ─────────────────────────────────────────────────────
    kafka_ok = False
    if settings.event_queue_backend == EventQueueBackend.KAFKA:
        try:
            from finanalytics_ai.infrastructure.queue.kafka_adapter import KafkaMarketEventConsumer
            consumer = KafkaMarketEventConsumer()
            await consumer.start()
            _kafka_consumer = consumer

            async def _handle_event(event: Any) -> None:
                from finanalytics_ai.domain.entities.event import MarketEvent, EventType
                if not isinstance(event, MarketEvent):
                    return
                logger.info("kafka.event.received", type=event.event_type, ticker=event.ticker)

                # Avalia alertas para PRICE_UPDATE
                if event.event_type == EventType.PRICE_UPDATE and _alert_service:
                    price = event.payload.get("price")
                    if price:
                        triggered = await _alert_service.evaluate_price(event.ticker, float(price))
                        if triggered:
                            logger.info("alerts.triggered", ticker=event.ticker, count=triggered)

                # Persiste tick no TimescaleDB
                if event.event_type == EventType.PRICE_UPDATE and timescale_ok:
                    try:
                        from finanalytics_ai.infrastructure.timescale.repository import (
                            TimescalePriceTickRepository, get_timescale_pool,
                        )
                        pool = await get_timescale_pool()
                        repo = TimescalePriceTickRepository(pool)
                        await repo.save_tick(
                            ticker=event.ticker,
                            price=float(event.payload.get("price", 0)),
                            change_pct=event.payload.get("change_pct"),
                            volume=event.payload.get("volume"),
                            source=event.source,
                        )
                    except Exception as e:
                        logger.warning("timescale.tick.save_failed", error=str(e))

            _kafka_task = asyncio.create_task(consumer.consume_loop(_handle_event))
            kafka_ok = True
            logger.info("kafka.consumer.running", group=settings.kafka_consumer_group)
        except Exception as exc:
            logger.warning("kafka.unavailable", error=str(exc))

    logger.info("api.ready", postgres=True, timescale=timescale_ok, kafka=kafka_ok)
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    if _kafka_task:
        _kafka_task.cancel()
        try:
            await _kafka_task
        except asyncio.CancelledError:
            pass
    if _kafka_consumer:
        try:
            await _kafka_consumer.stop()
        except Exception:
            pass
    if timescale_ok:
        try:
            from finanalytics_ai.infrastructure.timescale.repository import close_timescale_pool
            await close_timescale_pool()
        except Exception:
            pass
    await close_engine()
    logger.info("api.stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="FinAnalytics AI",
        description="Framework de Análise e Busca de Investimentos",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(FinAnalyticsError)
    async def finanalytics_error_handler(request: object, exc: FinAnalyticsError) -> JSONResponse:
        logger.warning("api.domain_error", code=exc.code, message=exc.message)
        status_map = {
            "PORTFOLIO_NOT_FOUND": 404, "INVALID_TICKER": 422,
            "INSUFFICIENT_FUNDS": 422,  "INVALID_QUANTITY": 422,
            "DUPLICATE_EVENT": 409,
        }
        return JSONResponse(
            status_code=status_map.get(exc.code, 400),
            content={"error": exc.code, "message": exc.message, "context": exc.context},
        )

    app.include_router(dashboard.router,  tags=["Dashboard"])
    app.include_router(health.router,     tags=["Health"])
    app.include_router(portfolio.router,  prefix="/api/v1/portfolios", tags=["Portfolio"])
    app.include_router(quotes.router,     prefix="/api/v1/quotes",     tags=["Cotações"])
    app.include_router(events.router,     prefix="/api/v1/events",     tags=["Eventos"])
    app.include_router(alerts.router,     prefix="/api/v1/alerts",     tags=["Alertas"])

    return app


from fastapi.responses import HTMLResponse
import pathlib

def mount_static(app: FastAPI) -> None:
    static_dir = pathlib.Path(__file__).parent / "static"

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def serve_dashboard() -> HTMLResponse:
        html_file = static_dir / "dashboard.html"
        if not html_file.exists():
            return HTMLResponse("<h1>Dashboard não encontrado</h1>", status_code=404)
        return HTMLResponse(html_file.read_text(encoding="utf-8"))
