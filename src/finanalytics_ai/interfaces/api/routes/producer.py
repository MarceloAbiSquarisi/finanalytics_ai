"""
Rotas do BRAPI Producer — controle e observabilidade.

  POST /producer/start    — inicia o loop (se não estiver rodando)
  POST /producer/stop     — para gracefully
  POST /producer/trigger  — ciclo imediato (debug/teste)
  GET  /producer/status   — métricas
"""

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()
logger = structlog.get_logger(__name__)

class TriggerRequest(BaseModel):
    tickers: list[str] | None = None

def _get_producer():
    from finanalytics_ai.interfaces.api.app import get_price_producer

    return get_price_producer()

@router.post("/start")
async def start_producer() -> dict:
    import asyncio

    from finanalytics_ai.config import get_settings
    from finanalytics_ai.interfaces.api.app import get_price_producer

    producer = get_price_producer()
    if producer and producer.state.running:
        return {"started": False, "message": "Producer já está rodando"}

    # Recriar o producer se não existir
    if producer is None:
        settings = get_settings()
        if not settings.brapi_token:
            raise HTTPException(400, detail="BRAPI_TOKEN não configurado no .env")
        try:
            import finanalytics_ai.interfaces.api.app as _app
            from finanalytics_ai.application.services.price_producer import BrapiPriceProducer
            from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient
            from finanalytics_ai.infrastructure.queue.kafka_adapter import KafkaMarketEventProducer

            tickers = [t.strip() for t in settings.producer_tickers.split(",") if t.strip()]
            p = BrapiPriceProducer(
                tickers=tickers,
                poll_interval=settings.producer_poll_interval_seconds,
                brapi_client=BrapiClient(),
                kafka_producer=KafkaMarketEventProducer()
            )
            await p.start()
            _app._price_producer = p
            _app._producer_task = asyncio.create_task(p.run())
            return {"started": True, "tickers": tickers}
        except Exception as exc:
            raise HTTPException(500, detail=str(exc)) from exc

    await producer.start()
    return {"started": True, "tickers": producer.state.tickers}

@router.post("/stop")
async def stop_producer() -> dict:
    producer = _get_producer()
    if not producer or not producer.state.running:
        return {"stopped": False, "message": "Producer não estava rodando"}
    await producer.stop()
    return {"stopped": True}

@router.post("/trigger")
async def trigger_cycle(body: TriggerRequest = TriggerRequest()) -> dict:
    """Executa um ciclo imediato sem aguardar o intervalo."""
    producer = _get_producer()
    if not producer:
        raise HTTPException(503, detail="Producer não inicializado — configure BRAPI_TOKEN no .env")

    try:
        published = await producer.run_once()
        return {
            "triggered": True,
            "tickers": producer.state.tickers,
            "events_published": published,
            "last_prices": producer.state.last_prices,
        }
    except Exception as exc:
        raise HTTPException(502, detail=f"Erro ao buscar cotações BRAPI: {exc}") from exc

@router.get("/status")
async def producer_status() -> dict:
    from finanalytics_ai.config import get_settings

    settings = get_settings()
    producer = _get_producer()

    if not producer:
        return {
            "running": False,
            "message": "Producer não inicializado",
            "brapi_token_configured": bool(settings.brapi_token),
            "config": {
                "tickers_raw": settings.producer_tickers,
                "interval_s": settings.producer_poll_interval_seconds,
                "producer_enabled": settings.producer_enabled,
            },
        }

    s = producer.state
    return {
        "running": s.running,
        "started_at": s.started_at,
        "tickers": s.tickers,
        "poll_interval_s": s.poll_interval,
        "cycles": {
            "total": s.cycles_total,
            "ok": s.cycles_ok,
            "error": s.cycles_error,
        },
        "events_published": s.events_published,
        "last_cycle_at": s.last_cycle_at,
        "last_cycle_ms": s.last_cycle_ms,
        "last_prices": s.last_prices,
        "last_error": s.last_error,
        "brapi_token_configured": bool(settings.brapi_token),
    }
