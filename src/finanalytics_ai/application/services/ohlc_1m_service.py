from __future__ import annotations

from datetime import UTC
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
import structlog

from finanalytics_ai.domain.indicators.ohlc_aggregator import aggregate_bars, filter_by_range
from finanalytics_ai.domain.value_objects.money import Ticker
from finanalytics_ai.infrastructure.database.repositories.ohlc_1m_repo import (
    OHLC1mMetaModel,
    OHLC1mModel,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient

logger = structlog.get_logger(__name__)
DAILY = {"1d", "2d", "3d", "4d", "5d", "1wk", "1mo", "3mo"}


def _ttl() -> int:
    from datetime import datetime

    now = datetime.now(UTC)
    if now.weekday() >= 5:
        return 43200
    s = now.hour * 3600 + now.minute * 60
    return 60 if 46800 <= s <= 77700 else 43200


class OHLC1mService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], brapi_client: BrapiClient
    ) -> None:
        self._sf = session_factory
        self._brapi = brapi_client

    async def get_bars(
        self, ticker: str, interval: str = "5m", range_period: str = "5d"
    ) -> list[dict[str, Any]]:
        ticker = ticker.upper()
        if interval in DAILY:
            return await self._daily(ticker, interval, range_period)
        async with self._sf() as s:
            meta = await self._meta(s, ticker)
            stale = (
                not meta or not meta.last_fetch_at or (time.time() - meta.last_fetch_at) > _ttl()
            )
            if stale:
                await self._refresh(s, ticker)
            bars = await self._load(s, ticker, range_period)
        if not bars:
            return []
        return filter_by_range(aggregate_bars(bars, interval), range_period)

    async def ingest(self, ticker: str) -> int:
        async with self._sf() as s:
            return await self._refresh(s, ticker.upper())

    async def _meta(self, s, ticker):
        r = await s.execute(select(OHLC1mMetaModel).where(OHLC1mMetaModel.ticker == ticker))
        return r.scalar_one_or_none()

    async def _refresh(self, s: AsyncSession, ticker: str) -> int:
        try:
            bars = await self._brapi.get_ohlc_bars(Ticker(ticker), range_period="5d", interval="1m")
        except Exception as e:
            logger.warning("ohlc_1m.fail", ticker=ticker, error=str(e))
            return 0
        if not bars:
            return 0
        rows = [
            {
                "ticker": ticker,
                "timestamp": int(b["time"]),
                "open": b["open"],
                "high": b["high"],
                "low": b["low"],
                "close": b["close"],
                "volume": b.get("volume") or 0,
            }
            for b in bars
        ]
        st = pg_insert(OHLC1mModel).values(rows)
        await s.execute(
            st.on_conflict_do_update(
                index_elements=["ticker", "timestamp"],
                set_={
                    "open": st.excluded.open,
                    "high": st.excluded.high,
                    "low": st.excluded.low,
                    "close": st.excluded.close,
                    "volume": st.excluded.volume,
                },
            )
        )
        now = int(time.time())
        ms = pg_insert(OHLC1mMetaModel).values(
            ticker=ticker,
            last_fetch_at=now,
            oldest_bar_ts=min(r["timestamp"] for r in rows),
            newest_bar_ts=max(r["timestamp"] for r in rows),
            bar_count=len(rows),
        )
        await s.execute(
            ms.on_conflict_do_update(
                index_elements=["ticker"],
                set_={
                    "last_fetch_at": now,
                    "newest_bar_ts": max(r["timestamp"] for r in rows),
                    "bar_count": len(rows),
                },
            )
        )
        await s.commit()
        logger.info("ohlc_1m.refreshed", ticker=ticker, bars=len(rows))
        return len(rows)

    async def _load(self, s, ticker, range_period):
        _M = {
            "1d": 86400,
            "5d": 432000,
            "1mo": 2592000,
            "3mo": 7776000,
            "6mo": 15552000,
            "1y": 31536000,
            "2y": 63072000,
        }
        d = _M.get(range_period)
        q = select(OHLC1mModel).where(OHLC1mModel.ticker == ticker).order_by(OHLC1mModel.timestamp)
        if d:
            q = q.where(OHLC1mModel.timestamp >= int(time.time()) - d)
        r = await s.execute(q)
        return [
            {
                "time": x.timestamp,
                "open": x.open,
                "high": x.high,
                "low": x.low,
                "close": x.close,
                "volume": x.volume or 0,
            }
            for x in r.scalars()
        ]

    async def _daily(self, ticker, interval, range_period):
        try:
            return await self._brapi.get_ohlc_bars(
                Ticker(ticker), range_period=range_period, interval=interval
            )
        except Exception as e:
            logger.warning("ohlc_daily.fail", ticker=ticker, error=str(e))
            return []
