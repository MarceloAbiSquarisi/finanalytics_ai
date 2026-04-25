"""Portfolio Repository — SQLAlchemy async."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    delete,
    select,
    update,
)
from sqlalchemy.orm import Mapped, mapped_column
import structlog

from finanalytics_ai.domain.entities.portfolio import Portfolio, Position
from finanalytics_ai.domain.value_objects.money import Currency, Money, Quantity, Ticker
from finanalytics_ai.infrastructure.database.connection import Base

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


class PortfolioModel(Base):
    __tablename__ = "portfolios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    benchmark: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    cash: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    investment_account_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class PositionModel(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    average_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(30), nullable=False, default="stock")
    __table_args__ = (UniqueConstraint("portfolio_id", "ticker", name="uq_portfolio_ticker"),)


class PortfolioNameHistoryModel(Base):
    __tablename__ = "portfolio_name_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    old_name: Mapped[str] = mapped_column(String(200), nullable=False)
    new_name: Mapped[str] = mapped_column(String(200), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    changed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class SQLPortfolioRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, portfolio: Portfolio) -> None:
        existing = await self._session.get(PortfolioModel, portfolio.portfolio_id)
        if existing:
            existing.name = portfolio.name
            existing.description = portfolio.description  # type: ignore[assignment]
            existing.benchmark = portfolio.benchmark  # type: ignore[assignment]
            existing.is_active = portfolio.is_active  # type: ignore[assignment]
            existing.cash = portfolio.cash.amount  # type: ignore[assignment]
            existing.updated_at = datetime.now(UTC)  # type: ignore[assignment]
        else:
            model = PortfolioModel(
                id=portfolio.portfolio_id,
                user_id=portfolio.user_id,
                name=portfolio.name,
                description=portfolio.description,
                benchmark=portfolio.benchmark,
                is_active=portfolio.is_active,
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
            self._session.add(
                PositionModel(
                    portfolio_id=portfolio.portfolio_id,
                    ticker=pos.ticker.symbol,
                    quantity=pos.quantity.value,
                    average_price=pos.average_price.amount,
                    asset_class=pos.asset_class,
                )
            )
        await self._session.flush()
        logger.debug("portfolio.saved", portfolio_id=portfolio.portfolio_id)

    async def find_by_id(self, portfolio_id: str) -> Portfolio | None:
        pm = await self._session.get(PortfolioModel, portfolio_id)
        if not pm:
            return None
        return await self._hydrate(pm)

    async def find_by_user(self, user_id: str, include_inactive: bool = False) -> list[Portfolio]:
        stmt = select(PortfolioModel).where(PortfolioModel.user_id == user_id)
        if not include_inactive:
            stmt = stmt.where(PortfolioModel.is_active.is_(True))
        result = await self._session.execute(stmt)
        portfolios = []
        for pm in result.scalars():
            portfolios.append(await self._hydrate(pm))
        return portfolios

    async def delete(self, portfolio_id: str) -> None:
        await self._session.execute(delete(PortfolioModel).where(PortfolioModel.id == portfolio_id))

    async def link_to_account(
        self, portfolio_id: str, account_id: str, user_id: str
    ) -> bool:
        """Associa portfolio a uma investment_account. Valida propriedade + atividade.
        Retorna True se linkou, False se conta inválida/não pertence/inativa."""
        from sqlalchemy import text as sql_text

        # Valida conta: dona do user + ativa
        acc_ok = (
            await self._session.execute(
                sql_text(
                    "SELECT 1 FROM investment_accounts "
                    "WHERE id = :a AND user_id = :u AND is_active = true"
                ),
                {"a": account_id, "u": user_id},
            )
        ).scalar_one_or_none()
        if not acc_ok:
            return False
        # Valida portfolio pertence ao user (defesa em profundidade)
        pf_ok = (
            await self._session.execute(
                sql_text("SELECT 1 FROM portfolios WHERE id = :p AND user_id = :u"),
                {"p": portfolio_id, "u": user_id},
            )
        ).scalar_one_or_none()
        if not pf_ok:
            return False
        await self._session.execute(
            sql_text(
                "UPDATE portfolios SET investment_account_id = :a, updated_at = NOW() "
                "WHERE id = :p"
            ),
            {"a": account_id, "p": portfolio_id},
        )
        await self._session.flush()
        return True

    async def record_name_change(
        self,
        portfolio_id: str,
        old_name: str,
        new_name: str,
        changed_by: str | None,
    ) -> None:
        """Grava 1 linha em portfolio_name_history. Idempotencia eh
        responsabilidade do chamador — repassamos sem deduplicar."""
        self._session.add(
            PortfolioNameHistoryModel(
                portfolio_id=portfolio_id,
                old_name=old_name,
                new_name=new_name,
                changed_at=datetime.now(UTC),
                changed_by=changed_by,
            )
        )
        await self._session.flush()

    async def name_history(self, portfolio_id: str) -> list[dict[str, str | None]]:
        """Retorna historico de renames em ordem decrescente (mais recente primeiro)."""
        stmt = (
            select(PortfolioNameHistoryModel)
            .where(PortfolioNameHistoryModel.portfolio_id == portfolio_id)
            .order_by(PortfolioNameHistoryModel.changed_at.desc())
        )
        result = await self._session.execute(stmt)
        return [
            {
                "old_name": str(h.old_name),
                "new_name": str(h.new_name),
                "changed_at": h.changed_at.isoformat() if h.changed_at else None,
                "changed_by": h.changed_by,
            }
            for h in result.scalars()
        ]

    async def has_active_holdings(self, portfolio_id: str) -> dict[str, int]:
        """
        Conta quantos holdings com saldo > 0 existem em cada classe.

        Trades NAO sao contadas — sao logs imutaveis.
        Retorna {classe: count}; vazio = portfolio sem saldo.
        """
        from sqlalchemy import text

        sql = text(
            """
            SELECT 'positions'        AS classe, COUNT(*) AS n
              FROM positions
             WHERE portfolio_id = :pid AND quantity > 0
             UNION ALL
            SELECT 'crypto_holdings'  AS classe, COUNT(*)
              FROM crypto_holdings
             WHERE portfolio_id = :pid AND quantity > 0
             UNION ALL
            SELECT 'rf_holdings'      AS classe, COUNT(*)
              FROM rf_holdings
             WHERE portfolio_id = :pid AND invested > 0
             UNION ALL
            SELECT 'other_assets'     AS classe, COUNT(*)
              FROM other_assets
             WHERE portfolio_id = :pid AND current_value > 0
            """
        )
        result = await self._session.execute(sql, {"pid": portfolio_id})
        out: dict[str, int] = {}
        for classe, n in result.all():
            if int(n) > 0:
                out[str(classe)] = int(n)
        return out

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
            description=str(pm.description) if pm.description else "",
            benchmark=str(pm.benchmark) if pm.benchmark else "",
            is_active=bool(pm.is_active),
            currency=Currency(str(pm.currency)),
            cash=Money(Decimal(str(pm.cash)), Currency(str(pm.currency))),
            investment_account_id=str(pm.investment_account_id) if pm.investment_account_id else None,
            created_at=pm.created_at,  # type: ignore[arg-type]
            updated_at=pm.updated_at,  # type: ignore[arg-type]
        )
        p.positions = positions
        return p
