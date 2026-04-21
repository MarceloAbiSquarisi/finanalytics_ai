"""
Rotas de eventos — SSE feed, publicação no Kafka e queries no TimescaleDB.

Endpoints:
  GET  /events/stream           — SSE: stream de eventos ao vivo do Kafka
  GET  /events/ticks/{ticker}   — Últimos N ticks do TimescaleDB
  GET  /events/ohlc/{ticker}    — Barras OHLC do TimescaleDB
  POST /events/publish          — Publica evento no Kafka (teste/debug)
  GET  /events/status           — Status do consumer Kafka

Design decision: SSE (Server-Sent Events) ao invés de WebSocket porque:
  - Unidirecional (servidor → cliente) é suficiente para feed de preços
  - Funciona sobre HTTP/1.1 sem upgrade
  - Auto-reconexão nativa no browser
  - Mais simples de implementar com FastAPI StreamingResponse
"""

import asyncio
from collections.abc import AsyncIterator
import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
import structlog

from finanalytics_ai.config import get_settings

router = APIRouter()
logger = structlog.get_logger(__name__)

# ── SSE STREAM (Kafka → Browser) ─────────────────────────────────────────────


@router.get("/stream")
async def event_stream(
    ticker: str | None = Query(default=None, description="Filtrar por ticker (opcional)"),
) -> StreamingResponse:
    """
    Server-Sent Events — stream de eventos ao vivo do Kafka.

    O frontend conecta com:
        const es = new EventSource('/api/v1/events/stream?ticker=PETR4')
        es.onmessage = (e) => console.log(JSON.parse(e.data))

    Cada evento é enviado como:
        data: {"ticker":"PETR4","type":"price_update","price":32.50,...}\\n\\n

    Heartbeat a cada 15s para manter a conexão aberta por proxies/load balancers.
    """
    settings = get_settings()

    async def _generator() -> AsyncIterator[str]:
        if not settings.kafka_bootstrap_servers:
            # Modo demo: simula eventos quando Kafka não está configurado
            async for chunk in _demo_event_generator(ticker):
                yield chunk
            return

        from finanalytics_ai.interfaces.api.app import get_kafka_consumer

        consumer = get_kafka_consumer()

        if consumer is None:
            yield _sse_event({"type": "error", "message": "Kafka consumer não disponível"})
            return

        yield _sse_event({"type": "connected", "message": "Stream conectado", "filter": ticker})
        logger.info("sse.client.connected", ticker_filter=ticker)

        try:
            while True:
                try:
                    event = await asyncio.wait_for(consumer.dequeue(), timeout=15.0)
                    # Filtro opcional por ticker
                    if ticker and event.ticker.upper() != ticker.upper():
                        consumer._buffer.task_done()
                        continue

                    payload = {
                        "type": event.event_type.value,
                        "ticker": event.ticker,
                        "event_id": event.event_id,
                        "source": event.source,
                        "occurred_at": event.occurred_at.isoformat(),
                        **event.payload,
                    }
                    yield _sse_event(payload)
                    consumer._buffer.task_done()

                except TimeoutError:
                    # Heartbeat — mantém conexão viva
                    yield ": heartbeat\n\n"

        except asyncio.CancelledError:
            logger.info("sse.client.disconnected")
        except Exception as exc:
            logger.error("sse.stream.error", error=str(exc))
            yield _sse_event({"type": "error", "message": str(exc)})

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # desativa buffering no nginx
        },
    )


def _sse_event(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _demo_event_generator(ticker: str | None) -> AsyncIterator[str]:
    """
    Gerador de eventos simulados para modo sem Kafka.
    Emite um evento a cada 2s com preços randômicos.
    """
    import math
    import random

    tickers = [ticker] if ticker else ["PETR4", "VALE3", "ITUB4", "BBDC4", "WEGE3"]
    base_prices = {"PETR4": 32.0, "VALE3": 65.0, "ITUB4": 28.0, "BBDC4": 15.0, "WEGE3": 42.0}
    step = 0

    yield _sse_event({"type": "connected", "message": "Stream demo (sem Kafka)", "filter": ticker})

    for _ in range(300):  # limita a 300 eventos (10min a 2s) para não vazar conexão
        await asyncio.sleep(2.0)
        t = random.choice(tickers)
        base = base_prices.get(t, 30.0)
        price = base * (1 + 0.002 * math.sin(step * 0.3) + random.gauss(0, 0.003))
        step += 1  # noqa: SIM113
        base_prices[t] = price

        yield _sse_event(
            {
                "type": "price_update",
                "ticker": t,
                "price": round(price, 2),
                "change_pct": round((price / base - 1) * 100, 3),
                "volume": random.randint(100_000, 5_000_000),
                "source": "demo",
            }
        )


# ── TIMESCALEDB QUERIES ───────────────────────────────────────────────────────


@router.get("/ticks/{ticker}")
async def get_price_ticks(ticker: str, limit: int = Query(default=60, ge=1, le=1000)) -> dict:
    """Últimos N ticks de preço do TimescaleDB."""
    try:
        from finanalytics_ai.infrastructure.timescale.repository import (
            TimescalePriceTickRepository,
            get_timescale_pool,
        )

        pool = await get_timescale_pool()
        repo = TimescalePriceTickRepository(pool)
        ticks = await repo.query_latest(ticker, limit)
        vwap = await repo.query_vwap(ticker)
        return {"ticker": ticker.upper(), "ticks": ticks, "count": len(ticks), "vwap": vwap}
    except Exception as exc:
        logger.warning("timescale.ticks.query_failed", ticker=ticker, error=str(exc))
        raise HTTPException(503, detail=f"TimescaleDB indisponível: {exc}") from exc


@router.get("/ohlc/{ticker}")
async def get_timescale_ohlc(
    ticker: str,
    timeframe: str = Query(default="1d"),
    limit: int = Query(default=200, ge=1, le=2000),
) -> dict:
    """Barras OHLC do TimescaleDB (dados locais, sem BRAPI)."""
    try:
        from finanalytics_ai.infrastructure.timescale.repository import (
            TimescaleOHLCRepository,
            get_timescale_pool,
        )

        pool = await get_timescale_pool()
        repo = TimescaleOHLCRepository(pool)
        bars = await repo.query_latest(ticker, timeframe, limit)
        return {
            "ticker": ticker.upper(),
            "timeframe": timeframe,
            "bars": bars,
            "count": len(bars),
            "source": "timescale",
        }
    except Exception as exc:
        logger.warning("timescale.ohlc.query_failed", ticker=ticker, error=str(exc))
        raise HTTPException(503, detail=f"TimescaleDB indisponível: {exc}") from exc


# ── KAFKA PUBLISH (debug / ingestão manual) ───────────────────────────────────


@router.post("/publish")
async def publish_event(body: dict) -> dict:
    """
    Publica um evento no Kafka.
    Útil para testes de integração e ingestão manual de dados.

    Body mínimo:
        {"event_type": "price_update", "ticker": "PETR4", "payload": {"price": 32.50}}
    """
    settings = get_settings()
    if not settings.kafka_bootstrap_servers:
        raise HTTPException(
            400, detail="Kafka não habilitado. Defina KAFKA_BOOTSTRAP_SERVERS no .env"
        )

    from finanalytics_ai.domain.entities.event import EventType, MarketEvent
    from finanalytics_ai.infrastructure.queue.kafka_adapter import KafkaMarketEventProducer

    try:
        event = MarketEvent(
            event_type=EventType(body.get("event_type", "price_update")),
            ticker=body.get("ticker", "UNKNOWN").upper(),
            payload=body.get("payload", {}),
            source=body.get("source", "manual"),
        )
        async with KafkaMarketEventProducer() as producer:
            await producer.publish(event)
        return {"published": True, "event_id": event.event_id, "ticker": event.ticker}
    except Exception as exc:
        raise HTTPException(500, detail=str(exc)) from exc


# ── STATUS ────────────────────────────────────────────────────────────────────


@router.get("/status")
async def events_status() -> dict:
    """Status do consumer Kafka e das conexões de infraestrutura."""
    from finanalytics_ai.interfaces.api.app import get_kafka_consumer

    settings = get_settings()

    consumer = get_kafka_consumer()
    kafka_status = {
        "backend": settings.event_queue_backend,
        "connected": consumer is not None,
        "buffer_size": 0,
    }
    if consumer:
        import contextlib

        with contextlib.suppress(Exception):
            kafka_status["buffer_size"] = consumer._buffer.qsize()

    timescale_status = {"connected": False}
    try:
        from finanalytics_ai.infrastructure.timescale.repository import get_timescale_pool

        pool = await get_timescale_pool()
        timescale_status["connected"] = pool is not None
    except Exception as exc:
        timescale_status["error"] = str(exc)  # type: ignore[assignment]

    return {
        "kafka": kafka_status,
        "timescale": timescale_status,
        "topics": {
            "market_events": settings.kafka_topic_market_events,
            "price_updates": settings.kafka_topic_price_updates,
        },
    }
