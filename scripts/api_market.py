"""
api_market.py — FastAPI Market Data API
Endpoints:
  GET /api/v1/ticks/{ticker}          — últimos N ticks
  GET /api/v1/ohlc/{ticker}           — barras OHLC por resolução
  GET /api/v1/tickers                 — lista de tickers disponíveis
  GET /healthz                        — health check
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

TIMESCALE_DSN = os.environ.get(
    "TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
)

app = FastAPI(title="finanalytics Market Data API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_conn():
    return psycopg2.connect(TIMESCALE_DSN)


def query(sql: str, params: tuple = ()) -> list[dict]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ── Models ────────────────────────────────────────────────────────────────────

class Tick(BaseModel):
    ticker: str
    exchange: str
    ts: datetime
    trade_number: int
    price: float
    quantity: int
    volume: float
    trade_type: int

class Bar(BaseModel):
    ticker: str
    exchange: str
    ts: datetime
    resolution: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    quantity: int
    trade_count: int


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/healthz")
def health():
    try:
        query("SELECT 1")
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        raise HTTPException(503, detail=str(e))


@app.get("/api/v1/tickers")
def list_tickers():
    """Lista tickers disponíveis com último preço e timestamp."""
    rows = query("""
        SELECT DISTINCT ON (ticker)
            ticker, exchange, price AS last_price, ts AS last_ts
        FROM ticks
        WHERE ticker != '__warmup__'
        ORDER BY ticker, ts DESC
    """)
    return rows


@app.get("/api/v1/ticks/{ticker}", response_model=list[Tick])
def get_ticks(
    ticker: str,
    limit: int = Query(100, ge=1, le=10000),
    since: Optional[datetime] = Query(None, description="ISO8601 — retorna ticks após esse timestamp"),
):
    """Últimos N ticks de um ticker, opcionalmente desde um timestamp."""
    ticker = ticker.upper()
    if since:
        rows = query("""
            SELECT ticker, exchange, ts, trade_number, price, quantity, volume, trade_type
            FROM ticks
            WHERE ticker = %s AND ts >= %s
            ORDER BY ts DESC
            LIMIT %s
        """, (ticker, since, limit))
    else:
        rows = query("""
            SELECT ticker, exchange, ts, trade_number, price, quantity, volume, trade_type
            FROM ticks
            WHERE ticker = %s
            ORDER BY ts DESC
            LIMIT %s
        """, (ticker, limit))

    if not rows:
        raise HTTPException(404, detail=f"Ticker '{ticker}' não encontrado")
    return rows


@app.get("/api/v1/ohlc/{ticker}", response_model=list[Bar])
def get_ohlc(
    ticker: str,
    resolution: str = Query("1", description="Resolução em minutos: 1, 5, 15, 60"),
    limit: int = Query(100, ge=1, le=5000),
    since: Optional[datetime] = Query(None),
):
    """Barras OHLC de um ticker por resolução."""
    ticker = ticker.upper()
    valid_resolutions = {"1", "5", "15", "60", "D"}
    if resolution not in valid_resolutions:
        raise HTTPException(400, detail=f"Resolução inválida. Use: {valid_resolutions}")

    if since:
        rows = query("""
            SELECT ticker, exchange, ts, resolution, open, high, low, close,
                   volume, quantity, trade_count
            FROM ohlc
            WHERE ticker = %s AND resolution = %s AND ts >= %s
            ORDER BY ts DESC
            LIMIT %s
        """, (ticker, resolution, since, limit))
    else:
        rows = query("""
            SELECT ticker, exchange, ts, resolution, open, high, low, close,
                   volume, quantity, trade_count
            FROM ohlc
            WHERE ticker = %s AND resolution = %s
            ORDER BY ts DESC
            LIMIT %s
        """, (ticker, resolution, limit))

    if not rows:
        raise HTTPException(404, detail=f"Sem dados OHLC para '{ticker}' resolução {resolution}m")
    return rows


@app.get("/api/v1/ohlc/{ticker}/latest")
def get_ohlc_latest(
    ticker: str,
    resolution: str = Query("1"),
):
    """Última barra OHLC — útil para preço atual com contexto."""
    ticker = ticker.upper()
    rows = query("""
        SELECT ticker, exchange, ts, resolution, open, high, low, close,
               volume, quantity, trade_count
        FROM ohlc
        WHERE ticker = %s AND resolution = %s
        ORDER BY ts DESC
        LIMIT 1
    """, (ticker, resolution))
    if not rows:
        raise HTTPException(404, detail=f"Sem dados para '{ticker}'")
    return rows[0]


if __name__ == "__main__":
    port = int(os.environ.get("API_PORT", 8001))
    uvicorn.run("api_market:app", host="0.0.0.0", port=port, reload=False)
