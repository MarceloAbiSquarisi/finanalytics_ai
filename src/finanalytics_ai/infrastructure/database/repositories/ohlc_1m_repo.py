from __future__ import annotations
from sqlalchemy import BigInteger, Column, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import declarative_base
Base = declarative_base()

class OHLC1mModel(Base):
    __tablename__ = "ohlc_1m"
    id        = Column(Integer,    primary_key=True, autoincrement=True)
    ticker    = Column(String(20), nullable=False)
    timestamp = Column(BigInteger, nullable=False)
    open      = Column(Float,      nullable=False)
    high      = Column(Float,      nullable=False)
    low       = Column(Float,      nullable=False)
    close     = Column(Float,      nullable=False)
    volume    = Column(Float,      nullable=True, default=0)
    __table_args__ = (
        UniqueConstraint("ticker", "timestamp", name="uq_ohlc1m_ticker_ts"),
        Index("ix_ohlc1m_ticker_ts", "ticker", "timestamp"),
    )

class OHLC1mMetaModel(Base):
    __tablename__ = "ohlc_1m_meta"
    id            = Column(Integer,    primary_key=True, autoincrement=True)
    ticker        = Column(String(20), nullable=False, unique=True, index=True)
    last_fetch_at = Column(BigInteger, nullable=True)
    oldest_bar_ts = Column(BigInteger, nullable=True)
    newest_bar_ts = Column(BigInteger, nullable=True)
    bar_count     = Column(Integer,    nullable=True, default=0)
