"""
finanalytics_ai.interfaces.api.routes.system_status
GET  /api/v1/system/status          -- status agregado (MASTER/ADMIN)
POST /api/v1/system/producer/start  -- inicia producer
POST /api/v1/system/producer/stop   -- para producer
"""

import time

from fastapi import APIRouter, Depends, HTTPException
import structlog

from finanalytics_ai.domain.auth.entities import User
from finanalytics_ai.interfaces.api.dependencies import get_current_user

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/system", tags=["System"])


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.has_admin_access:
        raise HTTPException(403, detail="Acesso restrito a administradores.")
    return current_user


@router.get("/status")
async def system_status(_: User = Depends(require_admin)) -> dict:
    from datetime import UTC, datetime

    result: dict = {
        "timestamp": datetime.now(UTC).isoformat(),
        "services": [],
        "data_collectors": [],
    }

    # API (self)
    result["services"].append(
        {
            "name": "FinAnalytics API",
            "key": "api",
            "status": "ok",
            "detail": "Respondendo normalmente",
            "latency_ms": 0,
        }
    )

    # PostgreSQL
    try:
        t0 = time.monotonic()
        from sqlalchemy import text

        from finanalytics_ai.infrastructure.database.connection import get_engine

        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        ms = int((time.monotonic() - t0) * 1000)
        result["services"].append(
            {
                "name": "PostgreSQL",
                "key": "postgres",
                "status": "ok",
                "detail": "Conectado (" + str(ms) + "ms)",
                "latency_ms": ms,
            }
        )
    except Exception as e:
        result["services"].append(
            {
                "name": "PostgreSQL",
                "key": "postgres",
                "status": "error",
                "detail": str(e)[:80],
                "latency_ms": -1,
            }
        )

    # TimescaleDB
    try:
        t0 = time.monotonic()
        from finanalytics_ai.infrastructure.timescale.repository import get_timescale_pool

        pool = await get_timescale_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        ms = int((time.monotonic() - t0) * 1000)
        result["services"].append(
            {
                "name": "TimescaleDB",
                "key": "timescale",
                "status": "ok",
                "detail": "Conectado (" + str(ms) + "ms)",
                "latency_ms": ms,
            }
        )
    except Exception:
        result["services"].append(
            {
                "name": "TimescaleDB",
                "key": "timescale",
                "status": "warning",
                "detail": "Indisponivel (nao critico)",
                "latency_ms": -1,
            }
        )

    # Redis
    try:
        t0 = time.monotonic()
        import redis.asyncio as aioredis

        from finanalytics_ai.config import get_settings

        r = aioredis.from_url(str(get_settings().redis_url))
        await r.ping()
        await r.aclose()
        ms = int((time.monotonic() - t0) * 1000)
        result["services"].append(
            {
                "name": "Redis",
                "key": "redis",
                "status": "ok",
                "detail": "PONG (" + str(ms) + "ms)",
                "latency_ms": ms,
            }
        )
    except Exception as e:
        result["services"].append(
            {
                "name": "Redis",
                "key": "redis",
                "status": "error",
                "detail": str(e)[:80],
                "latency_ms": -1,
            }
        )

    # Kafka
    from finanalytics_ai.interfaces.api.app import get_kafka_consumer

    kafka = get_kafka_consumer()
    result["services"].append(
        {
            "name": "Kafka",
            "key": "kafka",
            "status": "warning" if not kafka else "ok",
            "detail": "Nao configurado (nao critico)" if not kafka else "Consumer ativo",
            "latency_ms": -1,
        }
    )

    # BRAPI Producer
    from finanalytics_ai.interfaces.api.app import get_price_producer

    producer = get_price_producer()
    if not producer:
        result["services"].append(
            {
                "name": "BRAPI Producer",
                "key": "producer",
                "status": "warning",
                "detail": "Nao inicializado",
                "latency_ms": -1,
            }
        )
    else:
        s = producer.state
        st = "ok" if s.running and s.cycles_error == 0 else ("warning" if s.running else "error")
        status_str = "Rodando" if s.running else "Parado"
        result["services"].append(
            {
                "name": "BRAPI Producer",
                "key": "producer",
                "status": st,
                "detail": status_str
                + " - "
                + str(s.cycles_ok)
                + " OK / "
                + str(s.cycles_error)
                + " erros",
                "latency_ms": s.last_cycle_ms or -1,
            }
        )

    # Coletor: BRAPI Producer
    if producer:
        s = producer.state
        import datetime as _dt

        next_at = None
        try:
            last_dt = (
                _dt.datetime.fromisoformat(str(s.last_cycle_at))
                if isinstance(s.last_cycle_at, str)
                else s.last_cycle_at
            )
            if last_dt and s.poll_interval:
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=_dt.UTC)
                next_at = (last_dt + _dt.timedelta(seconds=s.poll_interval)).isoformat()
        except Exception:
            pass
        result["data_collectors"].append(
            {
                "name": "BRAPI Price Producer",
                "description": "Cotacoes em tempo real",
                "key": "producer",
                "status": "ok" if s.running else "stopped",
                "last_update": str(s.last_cycle_at) if s.last_cycle_at else None,
                "next_update": next_at,
                "detail": str(len(s.tickers)) + " tickers, " + str(s.events_published) + " eventos",
                "can_restart": True,
                "running": s.running,
            }
        )
    else:
        result["data_collectors"].append(
            {
                "name": "BRAPI Price Producer",
                "description": "Cotacoes em tempo real",
                "key": "producer",
                "status": "stopped",
                "last_update": None,
                "next_update": None,
                "detail": "Nao inicializado - configure BRAPI_TOKEN",
                "can_restart": False,
                "running": False,
            }
        )

    # Coletor: Fintz
    try:
        from sqlalchemy import text

        from finanalytics_ai.infrastructure.database.connection import get_engine

        async with get_engine().connect() as conn:
            row = await conn.execute(
                text("SELECT MAX(data_publicacao), COUNT(*) FROM fintz_indicadores")
            )
            r = row.fetchone()
            last_fintz = str(r[0]) if r and r[0] else None
            total_fintz = int(r[1]) if r and r[1] else 0
        result["data_collectors"].append(
            {
                "name": "Fintz Indicadores",
                "description": "Indicadores fundamentalistas",
                "key": "fintz",
                "status": "ok" if last_fintz else "warning",
                "last_update": last_fintz,
                "next_update": None,
                "detail": str(total_fintz) + " registros",
                "can_restart": False,
                "running": True,
            }
        )
    except Exception as e:
        result["data_collectors"].append(
            {
                "name": "Fintz Indicadores",
                "description": "Indicadores fundamentalistas",
                "key": "fintz",
                "status": "error",
                "last_update": None,
                "next_update": None,
                "detail": str(e)[:80],
                "can_restart": False,
                "running": False,
            }
        )

    # Coletor: Profit Agent (DLL Nelogica realtime)
    try:
        import os as _os_pa

        import httpx as _httpx

        agent_url = _os_pa.getenv("PROFIT_AGENT_URL", "http://host.docker.internal:8002")
        async with _httpx.AsyncClient(timeout=2.5) as _client:
            _r = await _client.get(f"{agent_url}/status")
            _s = _r.json() if _r.status_code == 200 else {}
        _subs = _s.get("subscribed_tickers") or []
        _ticks = _s.get("total_ticks", 0)
        _market = bool(_s.get("market_connected"))
        _routing = bool(_s.get("routing_connected"))
        _db = bool(_s.get("db_connected"))
        _status = "ok" if (_market and _routing and _db) else (
            "warning" if _market or _routing else "error"
        )
        _parts = []
        _parts.append(f"market={'ON' if _market else 'OFF'}")
        _parts.append(f"routing={'ON' if _routing else 'OFF'}")
        _parts.append(f"db={'ON' if _db else 'OFF'}")
        _parts.append(f"{len(_subs)} tickers, {_ticks} ticks")
        _detail = " · ".join(_parts)
        result["data_collectors"].append(
            {
                "name": "Profit Agent (DLL)",
                "description": "Ticks realtime + ordens via Nelogica ProfitDLL",
                "key": "profit_agent",
                "status": _status,
                "last_update": None,
                "next_update": None,
                "detail": _detail,
                "can_restart": False,
                "running": _market,
            }
        )
    except Exception as _pe:
        result["data_collectors"].append(
            {
                "name": "Profit Agent (DLL)",
                "description": "Ticks realtime + ordens via Nelogica ProfitDLL",
                "key": "profit_agent",
                "status": "error",
                "last_update": None,
                "next_update": None,
                "detail": f"Offline: {str(_pe)[:80]}",
                "can_restart": False,
                "running": False,
            }
        )

    # Coletor: OHLC
    result["data_collectors"].append(
        {
            "name": "OHLC Daily Updater",
            "description": "Barras diarias OHLC",
            "key": "ohlc",
            "status": "warning",
            "last_update": None,
            "next_update": None,
            "detail": "Modulo nao carregado (ohlc_updater ausente)",
            "can_restart": False,
            "running": False,
        }
    )
    # ── ProfitDLL status ──────────────────────────────────────────────────────
    try:
        import os as _os

        dll_path = _os.getenv("PROFIT_DLL_PATH", r"C:\Nelogica\ProfitDLL64.dll")
        dll_exists = _os.path.exists(dll_path) if _os.name == "nt" else False
        has_key = bool(_os.getenv("PROFIT_ACTIVATION_KEY", ""))
        profit_status = {
            "name": "ProfitDLL",
            "dll_found": dll_exists,
            "credentials_configured": has_key,
            "tickers": _os.getenv("PROFIT_TICKERS", "").split(","),
            "platform": "windows" if _os.name == "nt" else "non-windows (noop mode)",
            "status": "configured" if (dll_exists and has_key) else "not_configured",
        }
    except Exception as _pe:
        profit_status = {"name": "ProfitDLL", "status": "error", "error": str(_pe)}
    result["profit_dll"] = profit_status

    return result


@router.post("/producer/start")
async def start_producer(_: User = Depends(require_admin)) -> dict:
    import asyncio

    from finanalytics_ai.config import get_settings
    from finanalytics_ai.interfaces.api.app import get_price_producer

    producer = get_price_producer()
    if producer and producer.state.running:
        return {"ok": True, "message": "Producer ja esta rodando"}
    if producer is None:
        settings = get_settings()
        if not settings.brapi_token:
            raise HTTPException(400, detail="BRAPI_TOKEN nao configurado")
        try:
            from finanalytics_ai.application.services.price_producer import BrapiPriceProducer
            from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient
            from finanalytics_ai.infrastructure.queue.kafka_adapter import KafkaMarketEventProducer
            import finanalytics_ai.interfaces.api.app as _app

            tickers = [t.strip() for t in settings.producer_tickers.split(",") if t.strip()]
            p = BrapiPriceProducer(
                tickers=tickers,
                poll_interval=settings.producer_poll_interval_seconds,
                brapi_client=BrapiClient(),
                kafka_producer=KafkaMarketEventProducer(),
            )
            await p.start()
            _app._price_producer = p
            _app._producer_task = asyncio.create_task(p.run())
            return {"ok": True, "message": "Producer iniciado", "tickers": tickers}
        except Exception as exc:
            raise HTTPException(500, detail=str(exc)) from exc
    await producer.start()
    return {"ok": True, "message": "Producer iniciado"}


@router.post("/producer/stop")
async def stop_producer(_: User = Depends(require_admin)) -> dict:
    from finanalytics_ai.interfaces.api.app import get_price_producer

    producer = get_price_producer()
    if not producer or not producer.state.running:
        return {"ok": True, "message": "Producer nao estava rodando"}
    await producer.stop()
    return {"ok": True, "message": "Producer parado"}
