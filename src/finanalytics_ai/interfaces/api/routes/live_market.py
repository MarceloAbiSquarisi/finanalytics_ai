"""
finanalytics_ai.interfaces.api.routes.live_market
--------------------------------------------------
Market data em tempo real via TimescaleDB (profit_tick_worker + tape_service).

GET /api/v1/live/tickers                     — tickers ativos com último preço
GET /api/v1/live/ticks/{ticker}              — últimos N ticks brutos
GET /api/v1/live/ohlc/{ticker}               — barras OHLCV por resolução
GET /api/v1/live/ohlc/{ticker}/latest        — última barra (preço atual + contexto)
"""

from __future__ import annotations

from datetime import datetime
import os

import asyncpg
from fastapi import APIRouter, HTTPException, Query
import structlog

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/live", tags=["Live Market Data"])

# Reutiliza a mesma DSN do marketdata.py existente
_TS_DSN: str = os.getenv(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
).replace("postgresql://", "postgres://")

_VALID_RESOLUTIONS = {"1", "5", "15", "60", "D"}


async def _conn() -> asyncpg.Connection:
    return await asyncpg.connect(_TS_DSN)


# ── Tickers ───────────────────────────────────────────────────────────────────


@router.get("/tickers", summary="Tickers ativos com último preço")
async def list_tickers() -> list[dict]:
    """
    Retorna todos os tickers que têm ticks no banco,
    com último preço, volume do dia e timestamp.
    """
    conn = await _conn()
    try:
        rows = await conn.fetch("""
            SELECT DISTINCT ON (ticker)
                ticker,
                exchange,
                price   AS last_price,
                ts      AS last_ts,
                quantity
            FROM ticks
            WHERE ticker != '__warmup__'
            ORDER BY ticker, ts DESC
        """)
        return [dict(r) for r in rows]
    finally:
        await conn.close()


# ── Ticks brutos ──────────────────────────────────────────────────────────────


@router.get("/ticks/{ticker}", summary="Últimos N ticks brutos")
async def get_ticks(
    ticker: str,
    limit: int = Query(100, ge=1, le=5000, description="Número de ticks a retornar"),
    since: datetime | None = Query(
        None, description="Retorna apenas ticks após este timestamp (ISO8601)"
    ),
) -> dict:
    """
    Retorna os últimos ticks de um ticker em ordem cronológica inversa.
    Use `since` para polling incremental.
    """
    ticker = ticker.upper()
    conn = await _conn()
    try:
        if since:
            rows = await conn.fetch(
                """
                SELECT ticker, exchange, ts, trade_number,
                       price, quantity, volume, trade_type
                FROM ticks
                WHERE ticker = $1 AND ts >= $2
                ORDER BY ts DESC
                LIMIT $3
            """,
                ticker,
                since,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT ticker, exchange, ts, trade_number,
                       price, quantity, volume, trade_type
                FROM ticks
                WHERE ticker = $1
                ORDER BY ts DESC
                LIMIT $2
            """,
                ticker,
                limit,
            )

        if not rows:
            raise HTTPException(404, detail=f"Nenhum tick encontrado para '{ticker}'")

        return {
            "ticker": ticker,
            "count": len(rows),
            "ticks": [dict(r) for r in rows],
        }
    finally:
        await conn.close()


# ── OHLC ──────────────────────────────────────────────────────────────────────


@router.get("/ohlc/{ticker}", summary="Barras OHLCV por resolução")
async def get_ohlc(
    ticker: str,
    resolution: str = Query("1", description="Resolução em minutos: 1, 5, 15, 60 ou D"),
    limit: int = Query(100, ge=1, le=2000, description="Número de barras"),
    since: datetime | None = Query(None, description="Barras a partir deste timestamp"),
) -> dict:
    """
    Barras OHLCV agregadas pelo tape_service.
    Resolução '1' = 1 minuto, '5' = 5 minutos, '60' = 1 hora, 'D' = diário.
    """
    ticker = ticker.upper()
    if resolution not in _VALID_RESOLUTIONS:
        raise HTTPException(400, detail=f"Resolução inválida. Use: {_VALID_RESOLUTIONS}")

    conn = await _conn()
    try:
        if since:
            rows = await conn.fetch(
                """
                SELECT ticker, exchange, ts, resolution,
                       open, high, low, close,
                       volume, quantity, trade_count
                FROM ohlc
                WHERE ticker = $1 AND resolution = $2 AND ts >= $3
                ORDER BY ts DESC
                LIMIT $4
            """,
                ticker,
                resolution,
                since,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT ticker, exchange, ts, resolution,
                       open, high, low, close,
                       volume, quantity, trade_count
                FROM ohlc
                WHERE ticker = $1 AND resolution = $2
                ORDER BY ts DESC
                LIMIT $3
            """,
                ticker,
                resolution,
                limit,
            )

        if not rows:
            raise HTTPException(
                404, detail=f"Sem dados OHLC para '{ticker}' resolução={resolution}"
            )

        # Retorna em ordem cronológica (mais antigo primeiro) para facilitar charting
        bars = list(reversed([dict(r) for r in rows]))
        return {
            "ticker": ticker,
            "resolution": resolution,
            "count": len(bars),
            "bars": bars,
        }
    finally:
        await conn.close()


@router.get("/ohlc/{ticker}/latest", summary="Última barra (preço atual + contexto OHLC)")
async def get_ohlc_latest(
    ticker: str,
    resolution: str = Query("1", description="Resolução: 1, 5, 15, 60"),
) -> dict:
    """
    Retorna a barra mais recente — útil para exibir preço atual com contexto OHLCV.
    """
    ticker = ticker.upper()
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            """
            SELECT ticker, exchange, ts, resolution,
                   open, high, low, close,
                   volume, quantity, trade_count
            FROM ohlc
            WHERE ticker = $1 AND resolution = $2
            ORDER BY ts DESC
            LIMIT 1
        """,
            ticker,
            resolution,
        )

        if not row:
            raise HTTPException(404, detail=f"Sem dados para '{ticker}'")

        return dict(row)
    finally:
        await conn.close()


import asyncio as _aio
import json as _json

from fastapi.responses import StreamingResponse


@router.get("/sse/tickers", summary="SSE stream de precos ao vivo")
async def sse_tickers(interval: float = Query(1.0, ge=0.2, le=60.0)) -> StreamingResponse:
    async def gen():
        while True:
            try:
                rows = await _aio.to_thread(
                    _live_query,
                    "SELECT DISTINCT ON (ticker) ticker, exchange, "
                    "price::text AS last_price, ts::text AS last_ts "
                    "FROM ticks WHERE ticker != '__warmup__' ORDER BY ticker, ts DESC",
                )
                for r in rows:
                    try:
                        r["last_price"] = float(r["last_price"])
                    except:
                        pass
                yield f"data: {_json.dumps(rows)}\n\n"
            except Exception:
                yield "data: []\n\n"
            await _aio.sleep(interval)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/sse/ticks/{ticker}", summary="SSE stream de ticks de um ticker")
async def sse_ticks(
    ticker: str, interval: float = Query(0.5, ge=0.2, le=60.0)
) -> StreamingResponse:
    t = _sanitize_ticker(ticker)

    async def gen():
        last_ts = None
        while True:
            try:
                rows = await _aio.to_thread(
                    _live_query,
                    f"SELECT ticker, exchange, ts::text AS ts, "
                    f"price::text AS price, quantity, volume::text AS volume "
                    f"FROM ticks WHERE ticker='{t}' ORDER BY ts DESC LIMIT 1",
                )
                if rows:
                    r = rows[0]
                    if r.get("ts") != last_ts:
                        last_ts = r.get("ts")
                        try:
                            r["price"] = float(r["price"])
                        except:
                            pass
                        yield f"data: {_json.dumps(r)}\n\n"
            except Exception:
                pass
            await _aio.sleep(interval)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
