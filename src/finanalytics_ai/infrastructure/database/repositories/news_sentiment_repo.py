"""
SQLNewsSentimentRepository — persistência de NewsSentiment via SQLAlchemy.

Mesma estratégia do OHLCBarRepository:
  - INSERT ... ON CONFLICT DO NOTHING por event_id (idempotência).
  - AsyncSession injetada pelo caller (Unit of Work).
  - Tabela separada de outros domínios para facilitar migração futura.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import DateTime, Float, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from finanalytics_ai.domain.entities.news_sentiment import NewsSentiment, SentimentLabel
from finanalytics_ai.infrastructure.database.connection import Base

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


class NewsSentimentModel(Base):
    """Tabela de sentimentos de notícias processados pelo event worker."""

    __tablename__ = "news_sentiments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    label: Mapped[str] = mapped_column(String(10), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="unknown")
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        # event_id já é UNIQUE acima — índice composto para query por ticker+data
        Index("ix_news_sentiments_ticker_analyzed", "ticker", "analyzed_at"),
        UniqueConstraint("event_id", name="uq_news_sentiments_event_id"),
    )


class SQLNewsSentimentRepository:
    """Implementação do NewsSentimentRepository usando SQLAlchemy AsyncSession."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, sentiment: NewsSentiment) -> bool:
        """Persiste sentimento. Retorna True se inserido, False se duplicata."""
        stmt = (
            insert(NewsSentimentModel)
            .values(
                event_id=sentiment.event_id,
                ticker=sentiment.ticker,
                headline=sentiment.headline,
                score=sentiment.score,
                label=str(sentiment.label),
                reasoning=sentiment.reasoning,
                model=sentiment.model,
                source=sentiment.source,
                analyzed_at=sentiment.analyzed_at,
            )
            .on_conflict_do_nothing(index_elements=["event_id"])
        )

        result = await self._session.execute(stmt)
        from sqlalchemy.engine import CursorResult

        inserted = isinstance(result, CursorResult) and result.rowcount == 1

        logger.debug(
            "news_sentiment.save",
            event_id=sentiment.event_id,
            ticker=sentiment.ticker,
            label=str(sentiment.label),
            inserted=inserted,
        )
        return inserted

    async def get_by_ticker(
        self,
        ticker: str,
        limit: int = 50,
    ) -> list[NewsSentiment]:
        """Retorna sentimentos mais recentes em ordem cronológica crescente."""
        from sqlalchemy import desc, select

        stmt = (
            select(NewsSentimentModel)
            .where(NewsSentimentModel.ticker == ticker)
            .order_by(desc(NewsSentimentModel.analyzed_at))
            .limit(limit)
        )

        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        sentiments = [
            NewsSentiment(
                event_id=row.event_id,
                ticker=row.ticker,
                headline=row.headline,
                score=row.score,
                label=SentimentLabel(row.label),
                reasoning=row.reasoning,
                model=row.model,
                analyzed_at=row.analyzed_at,
                source=row.source,
            )
            for row in reversed(rows)
        ]

        logger.debug("news_sentiment.get_by_ticker", ticker=ticker, count=len(sentiments))
        return sentiments
