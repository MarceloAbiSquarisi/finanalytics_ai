"""
FastAPI application factory — Sprint 5: Kafka + TimescaleDB.

Lifespan:
  1. PostgreSQL engine (portfolio / event store)
  2. TimescaleDB pool (OHLC + price ticks) — graceful skip se indisponível
  3. Kafka consumer como background task — graceful skip se backend≠kafka

Design decision: falhas de infraestrutura opcional (Kafka, TimescaleDB)
não derrubam a API. O serviço degrada graciosamente e loga warnings.
Isso segue o princípio de "partial availability" — melhor servir sem
features em tempo real do que ficar offline.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from finanalytics_ai.config import EventQueueBackend, get_settings
from finanalytics_ai.exceptions import FinAnalyticsError
from finanalytics_ai.infrastructure.database.connection import close_engine, get_engine
from finanalytics_ai.interfaces.api.routes import dashboard, health, portfolio, quotes, events

logger = structlog.get_logger(__name__)

# Referência global ao consumer Kafka — acessível para SSE endpoint
_kafka_consumer: object | None = None
_kafka_task: asyncio.Task | None = None  # type: ignore[type-arg]


def get_kafka_consumer() -> object | None:
    return _kafka_consumer


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _kafka_consumer, _kafka_task

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
        logger.warning(
            "timescale.unavailable",
            error=str(exc),
            hint="Verifique TIMESCALE_URL e se o container está rodando na porta 5433",
        )

    # ── 3. Kafka consumer ─────────────────────────────────────────────────────
    kafka_ok = False
    if settings.event_queue_backend == EventQueueBackend.KAFKA:
        try:
            from finanalytics_ai.infrastructure.queue.kafka_adapter import (
                KafkaMarketEventConsumer,
            )
            consumer = KafkaMarketEventConsumer()
            await consumer.start()
            _kafka_consumer = consumer

            # Handler simples: persiste tick no TimescaleDB + loga
            async def _handle_event(event: object) -> None:
                from finanalytics_ai.domain.entities.event import MarketEvent, EventType
                if not isinstance(event, MarketEvent):
                    return
                logger.info(
                    "kafka.event.received",
                    type=event.event_type,
                    ticker=event.ticker,
                )
                if event.event_type == EventType.PRICE_UPDATE and timescale_ok:
                    try:
                        from finanalytics_ai.infrastructure.timescale.repository import (
                            TimescalePriceTickRepository,
                            get_timescale_pool,
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
            logger.info(
                "kafka.consumer.running",
                topics=settings.kafka_topic_market_events,
                group=settings.kafka_consumer_group,
            )
        except Exception as exc:
            logger.warning(
                "kafka.unavailable",
                error=str(exc),
                hint="Defina EVENT_QUEUE_BACKEND=kafka e verifique KAFKA_BOOTSTRAP_SERVERS",
            )

    logger.info(
        "api.ready",
        postgres=True,
        timescale=timescale_ok,
        kafka=kafka_ok,
    )

    yield  # ── aplicação rodando ──────────────────────────────────────────────

    # ── Shutdown ──────────────────────────────────────────────────────────────
    if _kafka_task:
        _kafka_task.cancel()
        try:
            await _kafka_task
        except asyncio.CancelledError:
            pass
    if _kafka_consumer:
        try:
            await _kafka_consumer.stop()  # type: ignore[attr-defined]
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
    settings = get_settings()

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
    async def finanalytics_error_handler(
        request: object, exc: FinAnalyticsError
    ) -> JSONResponse:
        logger.warning("api.domain_error", code=exc.code, message=exc.message)
        status_map = {
            "PORTFOLIO_NOT_FOUND": 404,
            "INVALID_TICKER": 422,
            "INSUFFICIENT_FUNDS": 422,
            "INVALID_QUANTITY": 422,
            "DUPLICATE_EVENT": 409,
        }
        return JSONResponse(
            status_code=status_map.get(exc.code, 400),
            content={"error": exc.code, "message": exc.message, "context": exc.context},
        )

    app.include_router(dashboard.router, tags=["Dashboard"])
    app.include_router(health.router,    tags=["Health"])
    app.include_router(portfolio.router, prefix="/api/v1/portfolios",  tags=["Portfolio"])
    app.include_router(quotes.router,    prefix="/api/v1/quotes",      tags=["Cotações"])
    app.include_router(events.router,    prefix="/api/v1/events",      tags=["Eventos"])

    return app


# Static files
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
