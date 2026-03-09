"""Portfolio Repository — SQLAlchemy async."""
from __future__ import annotations
import uuid
from datetime import datetime
from decimal import Decimal
from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from finanalytics_ai.domain.entities.portfolio import Portfolio, Position
from finanalytics_ai.domain.value_objects.money import Money, Ticker, Quantity, Currency
from finanalytics_ai.infrastructure.database.connection import Base
import structlog

logger = structlog.get_logger(__name__)


class PortfolioModel(Base):
    __tablename__ = "portfolios"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(100), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    currency = Column(String(3), nullable=False, default="BRL")
    cash = Column(Numeric(18, 2), nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PositionModel(Base):
    __tablename__ = "positions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_id = Column(String(36), ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True)
    ticker = Column(String(10), nullable=False)
    quantity = Column(Numeric(18, 8), nullable=False)
    average_price = Column(Numeric(18, 2), nullable=False)
    asset_class = Column(String(30), nullable=False, default="stock")
    __table_args__ = (UniqueConstraint("portfolio_id", "ticker", name="uq_portfolio_ticker"),)


class SQLPortfolioRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, portfolio: Portfolio) -> None:
        existing = await self._session.get(PortfolioModel, portfolio.portfolio_id)
        if existing:
            existing.name = portfolio.name
            existing.cash = portfolio.cash.amount
            existing.updated_at = datetime.utcnow()
        else:
            model = PortfolioModel(
                id=portfolio.portfolio_id,
                user_id=portfolio.user_id,
                name=portfolio.name,
                currency=portfolio.currency.value,
                cash=portfolio.cash.amount,
                created_at=portfolio.created_at,
                updated_at=portfolio.updated_at,
            )
            self._session.add(model)
        # Sync positions: delete all and re-insert
        await self._session.execute(
            delete(PositionModel).where(PositionModel.portfolio_id == portfolio.portfolio_id)
        )
        for pos in portfolio.positions.values():
            self._session.add(PositionModel(
                portfolio_id=portfolio.portfolio_id,
                ticker=pos.ticker.symbol,
                quantity=pos.quantity.value,
                average_price=pos.average_price.amount,
                asset_class=pos.asset_class,
            ))
        await self._session.flush()
        logger.debug("portfolio.saved", portfolio_id=portfolio.portfolio_id)

    async def find_by_id(self, portfolio_id: str) -> Portfolio | None:
        pm = await self._session.get(PortfolioModel, portfolio_id)
        if not pm:
            return None
        return await self._hydrate(pm)

    async def find_by_user(self, user_id: str) -> list[Portfolio]:
        stmt = select(PortfolioModel).where(PortfolioModel.user_id == user_id)
        result = await self._session.execute(stmt)
        portfolios = []
        for pm in result.scalars():
            portfolios.append(await self._hydrate(pm))
        return portfolios

    async def delete(self, portfolio_id: str) -> None:
        await self._session.execute(
            delete(PortfolioModel).where(PortfolioModel.id == portfolio_id)
        )

    async def _hydrate(self, pm: PortfolioModel) -> Portfolio:
        stmt = select(PositionModel).where(PositionModel.portfolio_id == pm.id)
        result = await self._session.execute(stmt)
        positions: dict[str, Position] = {}
        for pos_m in result.scalars():
            ticker = Ticker(str(pos_m.ticker))
            positions[ticker.symbol] = Position(
                ticker=ticker,
                quantity=Quantity(Decimal(str(pos_m.quantity))),
                average_price=Money(Decimal(str(pos_m.average_price)), Currency(str(pm.currency))),
                asset_class=str(pos_m.asset_class),
            )
        p = Portfolio(
            portfolio_id=str(pm.id),
            user_id=str(pm.user_id),
            name=str(pm.name),
            currency=Currency(str(pm.currency)),
            cash=Money(Decimal(str(pm.cash)), Currency(str(pm.currency))),
            created_at=pm.created_at,  # type: ignore[arg-type]
            updated_at=pm.updated_at,  # type: ignore[arg-type]
        )
        p.positions = positions
        return p
