from sqlalchemy import Column, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class OHLCBarModel(Base):
    __tablename__ = "ohlc_bars"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=True)
    __table_args__ = (UniqueConstraint("ticker", "timestamp", name="uq_ohlc_ticker_ts"),)


class OHLCCacheMetaModel(Base):
    __tablename__ = "ohlc_cache_meta"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, unique=True, index=True)
    last_updated = Column(DateTime, nullable=True)
    period = Column(String(20), nullable=True)
