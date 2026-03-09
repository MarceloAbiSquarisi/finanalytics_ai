"""
Ingestor continuo de barras 1m durante o pregao B3.
Rodar: python -m finanalytics_ai.workers.ohlc_1m_ingestor
"""
from __future__ import annotations
import asyncio, os, signal, time
from datetime import datetime, timezone
import structlog

logger = structlog.get_logger(__name__)
DATABASE_DSN  = os.environ.get("DATABASE_DSN", "postgresql://finanalytics:secret@finanalytics_postgres:5432/finanalytics")
BRAPI_TOKEN   = os.environ.get("BRAPI_TOKEN", "")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))
TICKERS = [t.strip().upper() for t in os.environ.get(
    "INGESTOR_TICKERS","PETR4,VALE3,ITUB4,BBDC4,WEGE3,BBAS3,ABEV3,MXRF11,HGLG11,BOVA11").split(",")]
_shutdown = False

def _open():
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5: return False
    s = now.hour * 3600 + now.minute * 60
    return 46800 <= s <= 77700

def _wait():
    now = datetime.now(timezone.utc)
    s = now.hour * 3600 + now.minute * 60
    return float(max(0, 46800 - s) if s < 46800 and now.weekday() < 5 else 86400 - s + 46800)

async def run():
    global _shutdown
    dsn = DATABASE_DSN.replace("postgresql://","postgresql+asyncpg://")
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    engine = create_async_engine(dsn, pool_size=5, max_overflow=2, pool_pre_ping=True)
    sf = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    from finanalytics_ai.infrastructure.database.repositories.ohlc_1m_repo import Base
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
    from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient
    from finanalytics_ai.application.services.ohlc_1m_service import OHLC1mService
    svc = OHLC1mService(sf, BrapiClient(token=BRAPI_TOKEN))
    logger.info("ingestor.init_fetch")
    sem = asyncio.Semaphore(3)
    async def _one(t):
        async with sem:
            try: n = await svc.ingest(t); logger.debug("ok", t=t, n=n)
            except Exception as e: logger.warning("fail", t=t, e=str(e))
    await asyncio.gather(*[_one(t) for t in TICKERS])
    while not _shutdown:
        if _open():
            t0 = time.monotonic()
            await asyncio.gather(*[_one(t) for t in TICKERS])
            await asyncio.sleep(max(0, POLL_INTERVAL - (time.monotonic()-t0)))
        else:
            await asyncio.sleep(min(_wait(), 300))
    await engine.dispose()

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda s,f: globals().update(_shutdown=True))
    signal.signal(signal.SIGINT,  lambda s,f: globals().update(_shutdown=True))
    asyncio.run(run())
