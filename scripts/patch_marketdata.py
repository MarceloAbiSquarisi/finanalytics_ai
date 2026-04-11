"""Appends live market endpoints to the existing marketdata.py router."""
import os

path = r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\interfaces\api\routes\marketdata.py"

ADDITION = '''

# ── Live Market Data (TimescaleDB: profit_tick_worker + tape_service) ──────────

_LIVE_DSN: str = os.getenv(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
).replace("postgresql://", "postgres://")

_VALID_RES = {"1", "5", "15", "60", "D"}


async def _live_conn():
    import asyncpg
    return await asyncpg.connect(_LIVE_DSN)


@router.get("/live/tickers", summary="Tickers ativos com ultimo preco (TimescaleDB)")
async def live_tickers():
    conn = await _live_conn()
    try:
        rows = await conn.fetch("""
            SELECT DISTINCT ON (ticker) ticker, exchange, price AS last_price, ts AS last_ts
            FROM ticks WHERE ticker != \'__warmup__\' ORDER BY ticker, ts DESC
        """)
        return [dict(r) for r in rows]
    finally:
        await conn.close()


@router.get("/live/ticks/{ticker}", summary="Ultimos N ticks brutos")
async def live_ticks(ticker: str, limit: int = Query(100, ge=1, le=5000)):
    conn = await _live_conn()
    try:
        rows = await conn.fetch(
            "SELECT ticker,exchange,ts,trade_number,price,quantity,volume,trade_type "
            "FROM ticks WHERE ticker=$1 ORDER BY ts DESC LIMIT $2",
            ticker.upper(), limit
        )
        if not rows:
            from fastapi import HTTPException
            raise HTTPException(404, detail=f"Ticker \'{ticker.upper()}\' nao encontrado")
        return {"ticker": ticker.upper(), "count": len(rows), "ticks": [dict(r) for r in rows]}
    finally:
        await conn.close()


@router.get("/live/ohlc/{ticker}", summary="Barras OHLCV (tape_service)")
async def live_ohlc(
    ticker: str,
    resolution: str = Query("1", description="1, 5, 15, 60 ou D"),
    limit: int = Query(100, ge=1, le=2000),
):
    if resolution not in _VALID_RES:
        from fastapi import HTTPException
        raise HTTPException(400, detail=f"Resolucao invalida. Use: {_VALID_RES}")
    conn = await _live_conn()
    try:
        rows = await conn.fetch(
            "SELECT ticker,exchange,ts,resolution,open,high,low,close,volume,quantity,trade_count "
            "FROM ohlc WHERE ticker=$1 AND resolution=$2 ORDER BY ts DESC LIMIT $3",
            ticker.upper(), resolution, limit
        )
        if not rows:
            from fastapi import HTTPException
            raise HTTPException(404, detail=f"Sem OHLC para \'{ticker.upper()}\' res={resolution}")
        return {"ticker": ticker.upper(), "resolution": resolution,
                "count": len(rows), "bars": list(reversed([dict(r) for r in rows]))}
    finally:
        await conn.close()


@router.get("/live/ohlc/{ticker}/latest", summary="Ultima barra OHLCV")
async def live_ohlc_latest(ticker: str, resolution: str = Query("1")):
    conn = await _live_conn()
    try:
        row = await conn.fetchrow(
            "SELECT ticker,exchange,ts,resolution,open,high,low,close,volume,quantity,trade_count "
            "FROM ohlc WHERE ticker=$1 AND resolution=$2 ORDER BY ts DESC LIMIT 1",
            ticker.upper(), resolution
        )
        if not row:
            from fastapi import HTTPException
            raise HTTPException(404, detail=f"Sem dados para \'{ticker.upper()}\'")
        return dict(row)
    finally:
        await conn.close()
'''

with open(path, encoding="utf-8") as f:
    content = f.read()

if "/live/tickers" in content:
    print("JA EXISTE — nada a fazer")
else:
    with open(path, "a", encoding="utf-8") as f:
        f.write(ADDITION)
    print("OK — endpoints adicionados ao marketdata.py")
