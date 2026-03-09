from __future__ import annotations
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Boolean, Index
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class TickerModel(Base):
    __tablename__ = "tickers"
    ticker = Column(String(20), primary_key=True, nullable=False)
    name = Column(String(200), nullable=True)
    ticker_type = Column(String(20), nullable=True)
    exchange = Column(String(20), nullable=True, default="BVMF")
    active = Column(Boolean, nullable=False, default=True)
    last_updated = Column(DateTime, nullable=True)
    __table_args__ = (Index("ix_tickers_ticker_active", "ticker", "active"),)
