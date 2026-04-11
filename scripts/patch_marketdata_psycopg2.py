"""Replace asyncpg with psycopg2 in the live endpoints of marketdata.py"""

path = r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\interfaces\api\routes\marketdata.py"

with open(path, encoding="utf-8") as f:
    content = f.read()

# Replace the live section
old = """_LIVE_DSN: str = os.getenv(
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
        rows = await conn.fetch(\"\"\"
            SELECT DISTINCT ON (ticker) ticker, exchange, price AS last_price, ts AS last_ts
            FROM ticks WHERE ticker != \\'__warmup__\\' ORDER BY ticker, ts DESC
        \"\"\")
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
            raise HTTPException(404, detail=f"Ticker \\'{ticker.upper()}\\' nao encontrado")
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
            raise HTTPException(404, detail=f"Sem OHLC para \\'{ticker.upper()}\\' res={resolution}")
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
            raise HTTPException(404, detail=f"Sem dados para \\'{ticker.upper()}\\'")
        return dict(row)
    finally:
        await conn.close()"""

new = """_LIVE_DSN: str = os.getenv(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)

_VALID_RES = {"1", "5", "15", "60", "D"}


def _live_query(sql: str, params: tuple = ()) -> list[dict]:
    import psycopg2, psycopg2.extras
    conn = psycopg2.connect(_LIVE_DSN)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


@router.get("/live/tickers", summary="Tickers ativos com ultimo preco (TimescaleDB)")
def live_tickers():
    rows = _live_query(
        "SELECT DISTINCT ON (ticker) ticker, exchange, price AS last_price, ts AS last_ts "
        "FROM ticks WHERE ticker != %s ORDER BY ticker, ts DESC",
        ("__warmup__",)
    )
    return rows


@router.get("/live/ticks/{ticker}", summary="Ultimos N ticks brutos")
def live_ticks(ticker: str, limit: int = Query(100, ge=1, le=5000)):
    from fastapi import HTTPException
    rows = _live_query(
        "SELECT ticker,exchange,ts,trade_number,price,quantity,volume,trade_type "
        "FROM ticks WHERE ticker=%s ORDER BY ts DESC LIMIT %s",
        (ticker.upper(), limit)
    )
    if not rows:
        raise HTTPException(404, detail=f"Ticker '{ticker.upper()}' nao encontrado")
    return {"ticker": ticker.upper(), "count": len(rows), "ticks": rows}


@router.get("/live/ohlc/{ticker}", summary="Barras OHLCV (tape_service)")
def live_ohlc(
    ticker: str,
    resolution: str = Query("1", description="1, 5, 15, 60 ou D"),
    limit: int = Query(100, ge=1, le=2000),
):
    from fastapi import HTTPException
    if resolution not in _VALID_RES:
        raise HTTPException(400, detail=f"Resolucao invalida. Use: {_VALID_RES}")
    rows = _live_query(
        "SELECT ticker,exchange,ts,resolution,open,high,low,close,volume,quantity,trade_count "
        "FROM ohlc WHERE ticker=%s AND resolution=%s ORDER BY ts DESC LIMIT %s",
        (ticker.upper(), resolution, limit)
    )
    if not rows:
        raise HTTPException(404, detail=f"Sem OHLC para '{ticker.upper()}' res={resolution}")
    return {"ticker": ticker.upper(), "resolution": resolution,
            "count": len(rows), "bars": list(reversed(rows))}


@router.get("/live/ohlc/{ticker}/latest", summary="Ultima barra OHLCV")
def live_ohlc_latest(ticker: str, resolution: str = Query("1")):
    from fastapi import HTTPException
    rows = _live_query(
        "SELECT ticker,exchange,ts,resolution,open,high,low,close,volume,quantity,trade_count "
        "FROM ohlc WHERE ticker=%s AND resolution=%s ORDER BY ts DESC LIMIT 1",
        (ticker.upper(), resolution)
    )
    if not rows:
        raise HTTPException(404, detail=f"Sem dados para '{ticker.upper()}'")
    return rows[0]"""

if "_LIVE_DSN" not in content:
    print("ERRO: secao live nao encontrada")
elif "psycopg2" in content and "def live_tickers" in content:
    print("JA CORRIGIDO — nada a fazer")
else:
    content2 = content.replace(old, new)
    if content2 == content:
        # Try simpler replacement of just the async conn function
        content2 = content.replace(
            'async def _live_conn():\n    import asyncpg\n    return await asyncpg.connect(_LIVE_DSN)',
            'def _live_query(sql, params=()):\n    import psycopg2, psycopg2.extras\n    conn = psycopg2.connect(_LIVE_DSN.replace("postgres://","postgresql://"))\n    try:\n        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:\n            cur.execute(sql, params)\n            return [dict(r) for r in cur.fetchall()]\n    finally:\n        conn.close()'
        )
        if content2 == content:
            print("ERRO: padrao nao encontrado, tentando abordagem manual")
            # Find and show the live section
            idx = content.find("_LIVE_DSN")
            print(repr(content[idx:idx+200]))
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content2)
            print("OK — funcao async substituida por psycopg2 sync")
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content2)
        print("OK — secao live substituida por psycopg2 sync")
