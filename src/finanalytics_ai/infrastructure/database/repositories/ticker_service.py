from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .ticker_repo import Base, TickerModel

logger = logging.getLogger(__name__)
BRAPI_BASE = "https://brapi.dev/api"


class TickerService:
    def __init__(self, session_factory) -> None:
        self._sf = session_factory

    async def search(self, q: str, limit: int = 15) -> list[dict[str, Any]]:
        q = q.upper().strip()
        if not q:
            return []
        async with self._sf() as session:
            stmt = (
                select(TickerModel)
                .where(
                    TickerModel.active,
                    or_(TickerModel.ticker.ilike(f"{q}%"), TickerModel.name.ilike(f"%{q}%")),
                )
                .order_by(TickerModel.ticker)
                .limit(limit)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [
                {"ticker": r.ticker, "name": r.name or "", "type": r.ticker_type or "stock"} for r in rows
            ]

    async def count(self) -> int:
        async with self._sf() as session:
            return (await session.execute(select(func.count()).select_from(TickerModel))).scalar() or 0


def _guess_type(ticker: str) -> str:
    t = ticker.upper()
    if t.endswith("11"):
        return "etf" if any(x in t for x in ["BOVA", "SMAL", "IVVB", "HASH"]) else "fii"
    if t.endswith("34") or t.endswith("35"):
        return "bdr"
    return "stock"


def _chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


async def refresh_tickers(dsn: str, brapi_token: str | None = None) -> dict[str, int]:
    engine = create_async_engine(dsn, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    headers = {"Authorization": f"Bearer {brapi_token}"} if brapi_token else {}
    tickers = []
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(f"{BRAPI_BASE}/available", headers=headers)
            resp.raise_for_status()
            raw = resp.json().get("stocks", [])
            for item in raw:
                if isinstance(item, str):
                    tickers.append({"ticker": item, "name": None, "type": _guess_type(item)})
                elif isinstance(item, dict):
                    t = item.get("stock", item.get("ticker", "")) or ""
                    tickers.append({"ticker": t, "name": item.get("name"), "type": _guess_type(t)})
        except Exception as exc:
            logger.error(f"brapi.available.error: {exc}")
    if not tickers:
        await engine.dispose()
        return {"upserted": 0, "total": 0}
    now = datetime.now()  # naive — compatível com TIMESTAMP WITHOUT TIME ZONE
    upserted = 0
    async with sf() as session, session.begin():
        for chunk in _chunks(tickers, 500):
            rows = [
                {
                    "ticker": t["ticker"].upper().strip(),
                    "name": t["name"],
                    "ticker_type": t["type"],
                    "exchange": "BVMF",
                    "active": True,
                    "last_updated": now,
                }
                for t in chunk
                if t["ticker"]
            ]
            if not rows:
                continue
            stmt = pg_insert(TickerModel).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ticker"],
                set_={
                    "name": stmt.excluded.name,
                    "ticker_type": stmt.excluded.ticker_type,
                    "active": True,
                    "last_updated": stmt.excluded.last_updated,
                },
            )
            await session.execute(stmt)
            upserted += len(rows)
    await engine.dispose()
    return {"upserted": upserted, "total": len(tickers)}
