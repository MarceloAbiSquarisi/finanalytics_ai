"""Rewrites live endpoints using docker exec psql (proven to work with Redis pubsub active)."""
import json, subprocess

path = r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\interfaces\api\routes\marketdata.py"

with open(path, encoding="utf-8") as f:
    content = f.read()

marker = "# ── Live Market Data"
idx = content.find(marker)
if idx == -1:
    print("ERRO: marcador nao encontrado")
else:
    content = content[:idx] + r"""# ── Live Market Data (TimescaleDB via docker exec psql) ──────────────────────

_LIVE_CONTAINER = os.getenv("TIMESCALE_CONTAINER", "finanalytics_timescale")
_LIVE_USER      = os.getenv("TIMESCALE_USER",      "finanalytics")
_LIVE_DB        = os.getenv("TIMESCALE_DB",        "market_data")
_VALID_RES      = {"1", "5", "15", "60", "D"}


def _live_query(sql: str) -> list[dict]:
    """Executa SQL via docker exec psql — funciona com Redis pubsub ativo no processo."""
    import subprocess, json
    result = subprocess.run(
        ["docker", "exec", _LIVE_CONTAINER,
         "psql", "-U", _LIVE_USER, "-d", _LIVE_DB,
         "--no-psqlrc", "-t", "-A", "--csv",
         "-c", sql],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    lines = [l for l in result.stdout.strip().splitlines() if l]
    if not lines:
        return []
    header = lines[0].split(",")
    rows = []
    for line in lines[1:]:
        vals = line.split(",")
        rows.append(dict(zip(header, vals)))
    return rows


@router.get("/live/tickers", summary="Tickers ativos com ultimo preco")
def live_tickers():
    rows = _live_query(
        "SELECT DISTINCT ON (ticker) ticker, exchange, "
        "price::text AS last_price, ts::text AS last_ts "
        "FROM ticks WHERE ticker != '__warmup__' ORDER BY ticker, ts DESC"
    )
    for r in rows:
        try: r["last_price"] = float(r["last_price"])
        except: pass
    return rows


@router.get("/live/ticks/{ticker}", summary="Ultimos N ticks brutos")
def live_ticks(ticker: str, limit: int = Query(100, ge=1, le=1000)):
    from fastapi import HTTPException
    rows = _live_query(
        f"SELECT ticker, exchange, ts::text AS ts, trade_number, "
        f"price::text AS price, quantity, volume::text AS volume, trade_type "
        f"FROM ticks WHERE ticker='{ticker.upper()}' ORDER BY ts DESC LIMIT {limit}"
    )
    if not rows:
        raise HTTPException(404, detail=f"Ticker '{ticker.upper()}' nao encontrado")
    for r in rows:
        for k in ("price", "volume"):
            try: r[k] = float(r[k])
            except: pass
        for k in ("trade_number", "quantity", "trade_type"):
            try: r[k] = int(r[k])
            except: pass
    return {"ticker": ticker.upper(), "count": len(rows), "ticks": rows}


@router.get("/live/ohlc/{ticker}/latest", summary="Ultima barra OHLCV")
def live_ohlc_latest(ticker: str, resolution: str = Query("1")):
    from fastapi import HTTPException
    rows = _live_query(
        f"SELECT ticker, exchange, ts::text AS ts, resolution, "
        f"open::text, high::text, low::text, close::text, "
        f"volume::text, quantity, trade_count "
        f"FROM ohlc WHERE ticker='{ticker.upper()}' AND resolution='{resolution}' "
        f"ORDER BY ts DESC LIMIT 1"
    )
    if not rows:
        raise HTTPException(404, detail=f"Sem dados para '{ticker.upper()}'")
    r = rows[0]
    for k in ("open","high","low","close","volume"):
        try: r[k] = float(r[k])
        except: pass
    for k in ("quantity","trade_count"):
        try: r[k] = int(r[k])
        except: pass
    return r


@router.get("/live/ohlc/{ticker}", summary="Barras OHLCV (tape_service)")
def live_ohlc(
    ticker: str,
    resolution: str = Query("1", description="1, 5, 15, 60 ou D"),
    limit: int = Query(100, ge=1, le=500),
):
    from fastapi import HTTPException
    if resolution not in _VALID_RES:
        raise HTTPException(400, detail=f"Resolucao invalida. Use: {_VALID_RES}")
    rows = _live_query(
        f"SELECT ticker, exchange, ts::text AS ts, resolution, "
        f"open::text, high::text, low::text, close::text, "
        f"volume::text, quantity, trade_count "
        f"FROM ohlc WHERE ticker='{ticker.upper()}' AND resolution='{resolution}' "
        f"ORDER BY ts DESC LIMIT {limit}"
    )
    if not rows:
        raise HTTPException(404, detail=f"Sem OHLC para '{ticker.upper()}' res={resolution}")
    for r in rows:
        for k in ("open","high","low","close","volume"):
            try: r[k] = float(r[k])
            except: pass
        for k in ("quantity","trade_count"):
            try: r[k] = int(r[k])
            except: pass
    return {"ticker": ticker.upper(), "resolution": resolution,
            "count": len(rows), "bars": list(reversed(rows))}
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("OK — live endpoints reescritos com docker exec psql")
