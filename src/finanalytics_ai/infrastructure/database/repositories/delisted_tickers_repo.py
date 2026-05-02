"""
Repositorio de b3_delisted_tickers (R5 — survivorship bias step 2).

Modelo: b3_delisted_tickers — populada via:
  - scripts/survivorship_collect_cvm.py (1863 placeholders UNK_<cnpj>)
  - scripts/survivorship_collect_fintz_delta.py (449 tickers reais source=FINTZ)

Uso esperado pelo R5:
  - BacktestService.run(): consulta `get_delisting_info(ticker)` antes de
    chamar o engine. Se ticker existe na tabela com `delisting_date`
    populada, passa (delisting_date, last_known_price) pro run_backtest.
  - Engine usa esses params p/ force-close em delisting_date e truncar
    bars posteriores (evita ler bars stale ou inexistentes).

Spec viva: docs/runbook_survivorship_bias.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import Date, DateTime, Numeric, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
import structlog

from finanalytics_ai.infrastructure.database.connection import Base

logger = structlog.get_logger(__name__)


# ── Modelo ────────────────────────────────────────────────────────────────────


class DelistedTickerModel(Base):
    __tablename__ = "b3_delisted_tickers"

    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    cnpj: Mapped[str | None] = mapped_column(String(18), nullable=True)
    razao_social: Mapped[str | None] = mapped_column(String(200), nullable=True)
    delisting_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    delisting_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_known_price: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    last_known_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="CVM")
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[date] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[date] = mapped_column(DateTime(timezone=True), nullable=False)


# ── DTO de saida ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DelistingInfo:
    """Subset de DelistedTickerModel relevante para o engine de backtest."""

    ticker: str
    delisting_date: date
    last_known_price: float | None
    source: str  # 'CVM' | 'FINTZ' | 'B3' | 'MANUAL' | 'NEWS'

    @property
    def is_high_confidence(self) -> bool:
        """True se source garante delisting real (Fintz delta high_confidence)."""
        return self.source == "FINTZ"


# ── Repo ──────────────────────────────────────────────────────────────────────


async def get_delisting_info(
    session: AsyncSession, ticker: str
) -> DelistingInfo | None:
    """
    Lookup de info de delisting para 1 ticker.

    Retorna None se:
      - Ticker nao esta em b3_delisted_tickers
      - Ticker esta na tabela mas com delisting_date NULL (placeholder CVM
        pre-bridge — nao utilizavel pelo engine)
      - Ticker e' UNK_<cnpj> (placeholder CVM)
    """
    ticker_upper = ticker.upper().strip()
    if ticker_upper.startswith("UNK_"):
        return None

    stmt = select(DelistedTickerModel).where(DelistedTickerModel.ticker == ticker_upper)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None or row.delisting_date is None:
        return None

    return DelistingInfo(
        ticker=row.ticker,
        delisting_date=row.delisting_date,
        last_known_price=float(row.last_known_price) if row.last_known_price else None,
        source=row.source,
    )


async def list_delisted_in_range(
    session: AsyncSession,
    start: date,
    end: date,
    *,
    only_high_confidence: bool = False,
) -> list[DelistingInfo]:
    """
    Lista tickers que delistaram dentro de [start, end].

    Use case: expansao de universo de backtest — adicionar ao set de tickers
    a serem testados todos os que delistaram na janela do backtest. Sem isso,
    R5 sobre IBOV historico inclui implicitamente o vies "sobreviventes".
    """
    stmt = (
        select(DelistedTickerModel)
        .where(DelistedTickerModel.delisting_date >= start)
        .where(DelistedTickerModel.delisting_date <= end)
        .where(~DelistedTickerModel.ticker.like("UNK_%"))
    )
    if only_high_confidence:
        stmt = stmt.where(DelistedTickerModel.source == "FINTZ")
    stmt = stmt.order_by(DelistedTickerModel.delisting_date.desc())

    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [
        DelistingInfo(
            ticker=r.ticker,
            delisting_date=r.delisting_date,
            last_known_price=float(r.last_known_price) if r.last_known_price else None,
            source=r.source,
        )
        for r in rows
        if r.delisting_date is not None
    ]
