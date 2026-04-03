"""
Market Data API - le ticks em tempo real do TimescaleDB (profit_agent).
Endpoints:
  GET /api/v1/marketdata/quotes          -> precos atuais de todos os tickers
  GET /api/v1/marketdata/ticks/{ticker}  -> ultimos N ticks
  GET /api/v1/marketdata/candles/{ticker}-> candles OHLCV (1m, 5m, 1h)
  GET /api/v1/marketdata/volume/{ticker} -> volume e trades do dia
  GET /api/v1/marketdata/status          -> status do profit_agent
"""
from __future__ import annotations

import os
from typing import Any

import asyncpg
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/v1/marketdata", tags=["Market Data"])

_TS_DSN = os.getenv(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@timescale:5432/market_data",
).replace("postgresql://", "postgres://")

# BUG #2 FIX: dict completo com 30m e 1d que estavam ausentes no endpoint
# /candles/{ticker}, causando KeyError latente.
_INTERVALS: dict[str, str] = {
    "1m":  "1 minute",
    "5m":  "5 minutes",
    "15m": "15 minutes",
    "30m": "30 minutes",
    "1h":  "1 hour",
    "1d":  "1 day",
}


async def _conn() -> asyncpg.Connection:  # type: ignore[type-arg]
    return await asyncpg.connect(_TS_DSN)


@router.get("/quotes")
async def get_quotes() -> Any:
    """Preco atual, variacao do dia e volume por ticker."""
    try:
        conn = await _conn()
        rows = await conn.fetch("""
            WITH last_tick AS (
                SELECT DISTINCT ON (ticker)
                    ticker, exchange, price, quantity, volume, time
                FROM profit_ticks
                ORDER BY ticker, time DESC
            ),
            day_open AS (
                SELECT ticker, price AS open_price
                FROM profit_ticks
                WHERE time >= date_trunc('day', NOW() AT TIME ZONE 'America/Sao_Paulo')
                      AND time = (
                          SELECT MIN(time) FROM profit_ticks t2
                          WHERE t2.ticker = profit_ticks.ticker
                            AND t2.time >= date_trunc('day', NOW() AT TIME ZONE 'America/Sao_Paulo')
                      )
            ),
            day_stats AS (
                SELECT ticker,
                       MAX(price) AS high,
                       MIN(price) AS low,
                       SUM(volume) AS volume_day,
                       COUNT(*)   AS trades_day
                FROM profit_ticks
                WHERE time >= date_trunc('day', NOW() AT TIME ZONE 'America/Sao_Paulo')
                GROUP BY ticker
            )
            SELECT l.ticker, l.exchange, l.price, l.quantity, l.time,
                   d.open_price,
                   s.high, s.low, s.volume_day, s.trades_day,
                   CASE WHEN d.open_price > 0
                        THEN ROUND(((l.price - d.open_price) / d.open_price * 100)::numeric, 2)
                        ELSE 0 END AS change_pct
            FROM last_tick l
            LEFT JOIN day_open d USING (ticker)
            LEFT JOIN day_stats s USING (ticker)
            ORDER BY l.ticker
        """)
        await conn.close()
        return {"quotes": [dict(r) for r in rows], "count": len(rows)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


@router.get("/ticks/{ticker}")
async def get_ticks(ticker: str, limit: int = Query(500, le=1000)) -> Any:
    """Ultimos N ticks de um ticker."""
    try:
        conn = await _conn()
        rows = await conn.fetch("""
            SELECT time, price, quantity, volume, buy_agent, sell_agent, trade_type
            FROM profit_ticks
            WHERE ticker = $1
            ORDER BY time DESC
            LIMIT $2
        """, ticker.upper(), limit)
        await conn.close()
        result = [dict(r) for r in rows]
        result.reverse()
        return {"ticker": ticker.upper(), "ticks": result, "count": len(result)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


@router.get("/candles/{ticker}")
async def get_candles(
    ticker: str,
    resolution: str = Query("1m", regex="^(1m|5m|15m|30m|1h|1d)$"),
    limit: int = Query(120, le=5000),
) -> Any:
    """Candles OHLCV agregados do TimescaleDB."""
    # BUG #2 FIX: usa _INTERVALS global -- inclui 30m e 1d que faltavam aqui.
    bucket = _INTERVALS[resolution]
    try:
        conn = await _conn()
        rows = await conn.fetch(f"""
            SELECT
                time_bucket('{bucket}', time) AS ts,
                FIRST(price, time)  AS open,
                MAX(price)          AS high,
                MIN(price)          AS low,
                LAST(price, time)   AS close,
                SUM(volume)         AS volume,
                COUNT(*)            AS trades
            FROM profit_ticks
            WHERE ticker = $1
              AND time >= NOW() - INTERVAL '1 day'
            GROUP BY ts
            ORDER BY ts DESC
            LIMIT $2
        """, ticker.upper(), limit)
        await conn.close()
        result = [dict(r) for r in rows]
        result.reverse()
        return {"ticker": ticker.upper(), "resolution": resolution,
                "candles": result, "count": len(result)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


@router.get("/volume/{ticker}")
async def get_volume(ticker: str) -> Any:
    """Volume, trades e VWAP do dia."""
    try:
        conn = await _conn()
        row = await conn.fetchrow("""
            SELECT
                ticker,
                COUNT(*) AS trades,
                SUM(quantity) AS total_qty,
                SUM(volume) AS total_volume,
                CASE WHEN SUM(quantity) > 0
                     THEN SUM(price * quantity) / SUM(quantity)
                     ELSE 0 END AS vwap,
                MAX(price) AS high,
                MIN(price) AS low,
                FIRST(price, time) AS open,
                LAST(price, time) AS close
            FROM profit_ticks
            WHERE ticker = $1
              AND time >= date_trunc('day', NOW() AT TIME ZONE 'America/Sao_Paulo')
        """, ticker.upper())
        await conn.close()
        return dict(row) if row else {"error": "no data"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


@router.get("/status")
async def get_agent_status() -> Any:
    """Status do profit_agent -- consulta direto o agente na porta 8002."""
    import aiohttp
    agent_url = os.getenv("PROFIT_AGENT_URL", "http://host.docker.internal:8002")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{agent_url}/status",
                                   timeout=aiohttp.ClientTimeout(total=3)) as r:
                return await r.json()
    except Exception as e:
        return JSONResponse({"error": str(e), "agent_url": agent_url}, status_code=503)


@router.get("/candles/{ticker}/last")
async def get_last_candle(
    ticker: str,
    resolution: str = Query("1m", regex="^(1m|5m|15m|30m|1h|1d)$"),
) -> Any:
    """
    Retorna o candle atual em formacao -- usado para update por tick.

    BUG #1 FIX (root cause do dashboard nao atualizar):
    -------------------------------------------------------
    A versao anterior usava:
        to_char(time_bucket(bucket, time AT TIME ZONE 'America/Sao_Paulo'),
                'DD/MM/YYYY HH24:MI:SS') AS ts

    Isso retornava o horario em BRT como string sem timezone.
    O _doRefresh no frontend convertia com Date.UTC() -- tratando BRT como UTC --
    produzindo um timestamp 3 horas ANTES do ultimo bar do grafico (carregado em UTC).
    O LightweightCharts rejeita priceSeries.update() silenciosamente quando
    o time e <= ao ultimo bar renderizado.

    Fix: retornar time_bucket puro (timestamptz UTC), identico ao /candles/{ticker}.
    O frontend trata ISO 8601 corretamente via new Date(ts).getTime()/1000.

    BUG #3 FIX: usa _conn() e _TS_DSN globais em vez de reimportar os/asyncpg
    e usar DSN sem o .replace().
    """
    bucket = _INTERVALS[resolution]
    try:
        conn = await _conn()  # BUG #3 FIX: helper global com _TS_DSN correto
        row = await conn.fetchrow(f"""
            SELECT
                -- BUG #1 FIX: sem AT TIME ZONE nem to_char.
                -- Retorna timestamptz UTC -- mesmo formato que /candles/{{}}.
                -- asyncpg serializa como ISO 8601: "2026-04-02T14:00:00+00:00"
                -- Frontend converte via new Date(ts).getTime()/1000 -> Unix UTC correto.
                time_bucket('{bucket}', time)             AS ts,
                (array_agg(price ORDER BY time ASC))[1]   AS open,
                MAX(price)                                 AS high,
                MIN(price)                                 AS low,
                (array_agg(price ORDER BY time DESC))[1]  AS close,
                SUM(quantity)                              AS volume
            FROM profit_ticks
            WHERE ticker = $1
              AND time >= NOW() - INTERVAL '2 hours'
            GROUP BY 1
            ORDER BY 1 DESC
            LIMIT 1
        """, ticker.upper())
        await conn.close()
        if not row:
            return {"ticker": ticker.upper(), "candle": None}
        return {"ticker": ticker.upper(), "candle": dict(row)}
    except Exception as e:
        return {"ticker": ticker.upper(), "candle": None, "error": str(e)}