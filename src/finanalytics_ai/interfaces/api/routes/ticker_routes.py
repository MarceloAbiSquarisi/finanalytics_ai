
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/v1/tickers", tags=["tickers"])

@router.get("/search")
async def search_tickers(
    request: Request,
    q: str = Query("", min_length=1, max_length=20),
    limit: int = Query(15, ge=1, le=50)
):
    svc = getattr(request.app.state, "ticker_service", None)
    if svc is None:
        return JSONResponse({"results": [], "source": "unavailable"})
    results = await svc.search(q, limit=limit)
    return {"results": results, "count": len(results)}


@router.get("/subscriptions")
async def list_subscriptions(request: Request):
    """Lista todos os tickers com status de subscricao no profit_agent."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        from sqlalchemy import text
        rows = await session.execute(text("""
            SELECT ticker, name, ticker_type, exchange, active,
                   profit_subscribed, profit_exchange
            FROM tickers
            WHERE active = true
            ORDER BY ticker_type, ticker
        """))
        return {"tickers": [dict(r._mapping) for r in rows]}

@router.post("/subscriptions/{ticker}")
async def toggle_subscription(ticker: str, body: dict, request: Request):
    """Ativa ou desativa subscricao de um ticker no profit_agent."""
    subscribed = body.get("subscribed", True)
    exchange   = body.get("exchange", "B")
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        from sqlalchemy import text
        await session.execute(text("""
            UPDATE tickers
            SET profit_subscribed = :sub, profit_exchange = :exch
            WHERE ticker = :ticker
        """), {"sub": subscribed, "exch": exchange, "ticker": ticker.upper()})
        await session.commit()

        # Notifica o profit_agent via HTTP
        import httpx
        agent_url = "http://host.docker.internal:8002"
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                if subscribed:
                    await client.post(f"{agent_url}/subscribe",
                        json={"ticker": ticker.upper(), "exchange": exchange})
                else:
                    await client.post(f"{agent_url}/unsubscribe",
                        json={"ticker": ticker.upper()})
        except Exception:
            pass  # agent pode estar offline, subscricao foi salva no banco

        return {"ticker": ticker.upper(), "subscribed": subscribed, "exchange": exchange}

@router.get("/count")
async def count_tickers(request: Request):
    svc = getattr(request.app.state, "ticker_service", None)
    return {"count": await svc.count() if svc else 0}
