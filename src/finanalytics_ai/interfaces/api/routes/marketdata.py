"""
marketdata.py - rotas de market data (historico + live via TimescaleDB)

Melhorias (2026-04-11):
- Imports deduplicados e organizados no topo
- SQL injection: ticker e resolution sanitizados com regex antes de interpolacao
- Constantes extraidas (_LIVE_CONTAINER, _VALID_RES)
- Tipos adicionados nas funcoes live
- SSE: intervalo minimo 0.2s para nao sobrecarregar docker exec
- pattern= substituiu regex= deprecado
"""
from __future__ import annotations

import asyncio as _aio
import json as _json
import os
import re
import subprocess
from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

router = APIRouter()

# ── Constantes ────────────────────────────────────────────────────────────────
_LIVE_CONTAINER = os.getenv("TIMESCALE_CONTAINER", "finanalytics_timescale")
_LIVE_USER      = os.getenv("TIMESCALE_USER",      "finanalytics")
_LIVE_DB        = os.getenv("TIMESCALE_DB",        "market_data")
_VALID_RES      = {"1", "5", "15", "60", "D"}
_TICKER_RE      = re.compile(r"^[A-Z0-9]{1,12}$")

_INTERVALS = {
    "1m": "1 minute", "5m": "5 minutes", "15m": "15 minutes",
    "30m": "30 minutes", "1h": "1 hour", "1d": "1 day",
}


def _sanitize_ticker(ticker: str) -> str:
    t = ticker.upper().strip()
    if not _TICKER_RE.match(t):
        raise HTTPException(400, detail=f"Ticker invalido: {ticker!r}")
    return t


def _sanitize_resolution(resolution: str) -> str:
    if resolution not in _VALID_RES:
        raise HTTPException(400, detail=f"Resolucao invalida. Use: {sorted(_VALID_RES)}")
    return resolution


# ── TimescaleDB historico (asyncpg) ───────────────────────────────────────────
_TS_DSN = os.getenv(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
).replace("postgresql://", "postgres://")


async def _conn() -> asyncpg.Connection:  # type: ignore[type-arg]
    return await asyncpg.connect(_TS_DSN)


@router.get("/quotes")
async def get_quotes() -> Any:
    try:
        conn = await _conn()
        rows = await conn.fetch("""
            SELECT DISTINCT ON (ticker)
                ticker, exchange, price AS last_price,
                quantity AS last_qty, time AS last_ts
            FROM profit_ticks ORDER BY ticker, time DESC
        """)
        await conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


@router.get("/ticks/{ticker}")
async def get_ticks(ticker: str, limit: int = Query(500, le=1000)) -> Any:
    t = _sanitize_ticker(ticker)
    try:
        conn = await _conn()
        rows = await conn.fetch(
            "SELECT time, price, quantity, volume, trade_type "
            "FROM profit_ticks WHERE ticker=$1 ORDER BY time DESC LIMIT $2",
            t, limit,
        )
        await conn.close()
        return {"ticker": t, "count": len(rows), "ticks": [dict(r) for r in rows]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


@router.get("/candles/{ticker}")
async def get_candles(
    ticker: str,
    resolution: str = Query("5m", pattern="^(1m|5m|15m|30m|1h|1d)$"),
    limit: int = Query(1000, ge=1, le=10000),
) -> Any:
    t = _sanitize_ticker(ticker)
    bucket = _INTERVALS.get(resolution, "5 minutes")
    try:
        conn = await _conn()
        # Une dados históricos (market_history_trades) + live (profit_ticks)
        rows = await conn.fetch(f"""
            WITH combined AS (
                -- Dados históricos
                SELECT trade_date AS ts_raw,
                    CASE WHEN price < 5 THEN price * 100 ELSE price END AS price,
                    quantity
                FROM market_history_trades
                WHERE ticker = $1

                UNION ALL

                -- Dados live (últimas 24h para cobrir sessão atual)
                SELECT time AS ts_raw, price, quantity
                FROM profit_ticks
                WHERE ticker = $1
                  AND time >= NOW() - INTERVAL '1 day'
                  AND price > 0
            ),
            bucketed AS (
                SELECT
                    time_bucket('{bucket}', ts_raw) AS ts,
                    (array_agg(price ORDER BY ts_raw ASC))[1]  AS open,
                    MAX(price)                                   AS high,
                    MIN(price)                                   AS low,
                    (array_agg(price ORDER BY ts_raw DESC))[1]  AS close,
                    SUM(quantity)                                AS volume,
                    COUNT(*)                                     AS trades
                FROM combined
                GROUP BY 1
            )
            SELECT ts, open, high, low, close, volume, trades
            FROM (
                SELECT * FROM bucketed
                ORDER BY ts DESC
                LIMIT $2
            ) sub
            ORDER BY ts ASC
        """, t, limit)
        await conn.close()
        return {"ticker": t, "resolution": resolution, "candles": [dict(r) for r in rows]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


@router.get("/volume/{ticker}")
async def get_volume(ticker: str) -> Any:
    t = _sanitize_ticker(ticker)
    try:
        conn = await _conn()
        row = await conn.fetchrow(
            "SELECT SUM(volume) AS total_volume, SUM(quantity) AS total_qty, COUNT(*) AS trades "
            "FROM profit_ticks WHERE ticker=$1 AND time >= NOW() - INTERVAL '1 day'",
            t,
        )
        await conn.close()
        return {"ticker": t, **(dict(row) if row else {})}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


@router.get("/status")
async def get_agent_status() -> Any:
    import aiohttp
    agent_url = os.getenv("PROFIT_AGENT_URL", "http://localhost:8001")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{agent_url}/status", timeout=aiohttp.ClientTimeout(total=3)) as r:
                return await r.json()
    except Exception as e:
        return {"status": "unavailable", "error": str(e)}


@router.get("/candles/{ticker}/last")
async def get_last_candle(
    ticker: str,
    resolution: str = Query("1m", pattern="^(1m|5m|15m|30m|1h|1d)$"),
) -> Any:
    t = _sanitize_ticker(ticker)
    bucket = _INTERVALS.get(resolution, "1 minute")
    try:
        conn = await _conn()
        row = await conn.fetchrow(f"""
            SELECT time_bucket('{bucket}', time) AS ts,
                (array_agg(price ORDER BY time ASC))[1]  AS open,
                MAX(price) AS high, MIN(price) AS low,
                (array_agg(price ORDER BY time DESC))[1] AS close,
                SUM(quantity) AS volume
            FROM profit_ticks
            WHERE ticker=$1 AND time >= NOW() - INTERVAL '2 hours'
            GROUP BY 1 ORDER BY 1 DESC LIMIT 1
        """, t)
        await conn.close()
        if not row:
            return {"ticker": t, "candle": None}
        return {"ticker": t, "candle": dict(row)}
    except Exception as e:
        return {"ticker": t, "candle": None, "error": str(e)}


# ── Live Market Data (docker exec psql) ───────────────────────────────────────

def _live_query(sql: str) -> list[dict]:
    result = subprocess.run(
        ["docker", "exec", _LIVE_CONTAINER,
         "psql", "-U", _LIVE_USER, "-d", _LIVE_DB,
         "--no-psqlrc", "-A", "--csv", "-c", sql],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    lines = [l for l in result.stdout.strip().splitlines() if l]
    if not lines:
        return []
    header = lines[0].split(",")
    return [dict(zip(header, l.split(","))) for l in lines[1:]]


def _parse_floats(rows: list[dict], keys: tuple[str, ...]) -> list[dict]:
    for r in rows:
        for k in keys:
            try:
                r[k] = float(r[k])
            except (ValueError, KeyError, TypeError):
                pass
    return rows


@router.get("/live/tickers", summary="Tickers ativos com ultimo preco")
def live_tickers() -> list[dict]:
    rows = _live_query(
        "SELECT DISTINCT ON (ticker) ticker, exchange, "
        "price::text AS last_price, ts::text AS last_ts "
        "FROM ticks WHERE ticker != '__warmup__' ORDER BY ticker, ts DESC"
    )
    return _parse_floats(rows, ("last_price",))


@router.get("/live/ticks/{ticker}", summary="Ultimos N ticks brutos")
def live_ticks(ticker: str, limit: int = Query(100, ge=1, le=1000)) -> dict:
    t = _sanitize_ticker(ticker)
    rows = _live_query(
        f"SELECT ticker, exchange, ts::text AS ts, price::text AS price, "
        f"quantity, volume::text AS volume "
        f"FROM ticks WHERE ticker='{t}' ORDER BY ts DESC LIMIT {limit}"
    )
    if not rows:
        raise HTTPException(404, detail=f"Ticker '{t}' nao encontrado")
    return {"ticker": t, "count": len(rows), "ticks": _parse_floats(rows, ("price", "volume"))}


@router.get("/live/ohlc/{ticker}/latest", summary="Ultima barra OHLCV")
def live_ohlc_latest(ticker: str, resolution: str = Query("1")) -> dict:
    t, r = _sanitize_ticker(ticker), _sanitize_resolution(resolution)
    rows = _live_query(
        f"SELECT ticker, exchange, ts::text AS ts, resolution, "
        f"open::text, high::text, low::text, close::text, "
        f"volume::text, quantity, trade_count "
        f"FROM ohlc WHERE ticker='{t}' AND resolution='{r}' ORDER BY ts DESC LIMIT 1"
    )
    if not rows:
        raise HTTPException(404, detail=f"Sem dados para '{t}'")
    return _parse_floats(rows, ("open", "high", "low", "close", "volume"))[0]


@router.get("/live/ohlc/{ticker}", summary="Barras OHLCV")
def live_ohlc(
    ticker: str,
    resolution: str = Query("1", description="1, 5, 15, 60 ou D"),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    t, r = _sanitize_ticker(ticker), _sanitize_resolution(resolution)
    rows = _live_query(
        f"SELECT ticker, exchange, ts::text AS ts, resolution, "
        f"open::text, high::text, low::text, close::text, "
        f"volume::text, quantity, trade_count "
        f"FROM ohlc WHERE ticker='{t}' AND resolution='{r}' ORDER BY ts DESC LIMIT {limit}"
    )
    if not rows:
        raise HTTPException(404, detail=f"Sem OHLC para '{t}' res={r}")
    bars = list(reversed(_parse_floats(rows, ("open", "high", "low", "close", "volume"))))
    return {"ticker": t, "resolution": r, "count": len(bars), "bars": bars}


# ── SSE Streaming ─────────────────────────────────────────────────────────────

@router.get("/live/sse/tickers", summary="SSE stream de precos ao vivo")
async def sse_tickers(interval: float = Query(1.0, ge=0.2, le=60.0)) -> StreamingResponse:
    async def gen():
        while True:
            try:
                rows = await _aio.to_thread(_live_query,
                    "SELECT DISTINCT ON (ticker) ticker, exchange, "
                    "price::text AS last_price, ts::text AS last_ts "
                    "FROM ticks WHERE ticker != '__warmup__' ORDER BY ticker, ts DESC"
                )
                _parse_floats(rows, ("last_price",))
                yield f"data: {_json.dumps(rows)}\n\n"
            except Exception:
                yield "data: []\n\n"
            await _aio.sleep(interval)

    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/live/sse/ticks/{ticker}", summary="SSE stream de ticks de um ticker")
async def sse_ticks(
    ticker: str,
    interval: float = Query(0.5, ge=0.2, le=60.0),
) -> StreamingResponse:
    t = _sanitize_ticker(ticker)

    async def gen():
        last_ts = None
        while True:
            try:
                rows = await _aio.to_thread(_live_query,
                    f"SELECT ticker, exchange, ts::text AS ts, "
                    f"price::text AS price, quantity, volume::text AS volume "
                    f"FROM ticks WHERE ticker='{t}' ORDER BY ts DESC LIMIT 1"
                )
                if rows:
                    r = rows[0]
                    if r.get("ts") != last_ts:
                        last_ts = r.get("ts")
                        _parse_floats([r], ("price", "volume"))
                        yield f"data: {_json.dumps(r)}\n\n"
            except Exception:
                pass
            await _aio.sleep(interval)

    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})




