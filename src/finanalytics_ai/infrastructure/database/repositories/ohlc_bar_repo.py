"""
SQLOHLCBarRepository — persistência de barras OHLC via SQLAlchemy.

Design decisions:
  - Usa a Base principal (connection.py) para que create_all() na app
    crie a tabela automaticamente — sem migration separada para a tabela
    ohlc_bars_events (nome distinto de ohlc_bars para evitar conflito
    com a tabela legacy do backtest que usa a Base local).
  - ON CONFLICT DO NOTHING via insert(...).on_conflict_do_nothing():
    idempotência nativa sem round-trip SELECT antes do INSERT.
  - AsyncSession injetada: mesma sessão da transação do evento — commit
    e rollback controlados pelo caller (get_session context manager).
  - get_latest usa ORDER BY DESC + LIMIT: índice (ticker, timestamp)
    é suficiente para essa query no volume esperado (~10k barras/ticker).

Trade-off: SQLAlchemy vs asyncpg direto:
  - SQLAlchemy: mesma sessão do evento = mesma transação, sem pool extra.
  - asyncpg direto (TimescaleDB): ~3x mais rápido para bulk, suporta
    time_bucket(). Escolha correta quando volume > 100k barras/dia.
  - Para o worker atual (eventos one-by-one), SQLAlchemy é correto.
    TimescaleDB é adicionado na sprint de ingestão em batch (S18+).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import DateTime, Float, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from finanalytics_ai.domain.entities.event import OHLCBar
from finanalytics_ai.infrastructure.database.connection import Base

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


class OHLCBarEventModel(Base):
    """
    Tabela para barras OHLC vindas do processamento de eventos.

    Separada da OHLCBarModel (usada pelo backtest service) para não
    poluir o schema do backtest com dados do streaming de eventos.
    No futuro, pode ser migrado para hypertable TimescaleDB sem
    impacto no restante da app.
    """

    __tablename__ = "ohlc_bars_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False, default="1m")
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (
        # Garante idempotência: mesma barra nunca é inserida duas vezes
        UniqueConstraint("ticker", "timestamp", "timeframe", name="uq_ohlc_events_ticker_ts_tf"),
        Index("ix_ohlc_events_ticker_ts", "ticker", "timestamp"),
    )


class SQLOHLCBarRepository:
    """
    Implementação do OHLCBarRepository usando SQLAlchemy AsyncSession.

    Recebe sessão já aberta — o lifecycle de commit/rollback é
    responsabilidade do caller (padrão Unit of Work).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_bar(self, bar: OHLCBar) -> bool:
        """
        Insere barra ou ignora se já existir (idempotência).

        Usa INSERT ... ON CONFLICT DO NOTHING do PostgreSQL.
        Retorna True se inserida, False se já existia.

        Nota: rowcount=0 após ON CONFLICT DO NOTHING indica duplicata.
        """
        stmt = (
            insert(OHLCBarEventModel)
            .values(
                ticker=bar.ticker,
                timestamp=bar.timestamp,
                timeframe=bar.timeframe,
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=float(bar.volume) if bar.volume else None,
                source=bar.source,
            )
            .on_conflict_do_nothing(
                index_elements=["ticker", "timestamp", "timeframe"],
            )
        )

        result = await self._session.execute(stmt)
        # CursorResult.rowcount: 1 se inserido, 0 se ON CONFLICT DO NOTHING
        from sqlalchemy.engine import CursorResult

        inserted = isinstance(result, CursorResult) and result.rowcount == 1

        logger.debug(
            "ohlc.bar.upsert",
            ticker=bar.ticker,
            timestamp=bar.timestamp.isoformat(),
            timeframe=bar.timeframe,
            close=float(bar.close),
            inserted=inserted,
        )
        return inserted

    async def get_latest(self, ticker: str, timeframe: str, limit: int = 100) -> list[OHLCBar]:
        """Retorna as barras mais recentes em ordem crescente de timestamp."""
        from sqlalchemy import desc, select

        stmt = (
            select(OHLCBarEventModel)
            .where(
                OHLCBarEventModel.ticker == ticker,
                OHLCBarEventModel.timeframe == timeframe,
            )
            .order_by(desc(OHLCBarEventModel.timestamp))
            .limit(limit)
        )

        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        # Retorna em ordem cronológica (mais antiga primeiro)
        bars = [
            OHLCBar(
                ticker=row.ticker,
                timestamp=row.timestamp,
                timeframe=row.timeframe,
                open=Decimal(str(row.open)),
                high=Decimal(str(row.high)),
                low=Decimal(str(row.low)),
                close=Decimal(str(row.close)),
                volume=Decimal(str(row.volume)) if row.volume is not None else Decimal("0"),
                source=row.source,
            )
            for row in reversed(rows)
        ]

        logger.debug(
            "ohlc.get_latest",
            ticker=ticker,
            timeframe=timeframe,
            count=len(bars),
        )
        return bars
