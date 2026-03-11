from __future__ import annotations

from sqlalchemy import BigInteger, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class OHLC1mModel(Base):
    __tablename__ = "ohlc_1m"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    timestamp: Mapped[int] = mapped_column(BigInteger, nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True, default=0)
    __table_args__ = (
        UniqueConstraint("ticker", "timestamp", name="uq_ohlc1m_ticker_ts"),
        Index("ix_ohlc1m_ticker_ts", "ticker", "timestamp"),
    )


class OHLC1mMetaModel(Base):
    __tablename__ = "ohlc_1m_meta"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    last_fetch_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    oldest_bar_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    newest_bar_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bar_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
