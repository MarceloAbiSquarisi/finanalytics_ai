from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TickerModel(Base):
    __tablename__ = "tickers"
    ticker: Mapped[str] = mapped_column(String(20), primary_key=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ticker_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(20), nullable=True, default="BVMF")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    __table_args__ = (Index("ix_tickers_ticker_active", "ticker", "active"),)
