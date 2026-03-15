"""
finanalytics_ai.infrastructure.database.repositories.rf_repo
──────────────────────────────────────────────────────────────
Persistência da carteira de Renda Fixa.

Schema (tabelas criadas pelo create_all via SQLAlchemy):
  rf_portfolios  — carteiras RF de cada usuário
  rf_holdings    — posições individuais dentro de cada carteira

Design decisions:
  Tabelas independentes do portfólio de ações:
    A carteira de RF tem características muito diferentes (vencimentos,
    indexadores, isenção fiscal) que justificam um schema próprio.
    Reutilizar a tabela `portfolios` criaria acoplamento desnecessário.

  Snapshots de nome/tipo/indexador no holding:
    Evitamos JOIN com o catálogo de bonds a cada leitura.
    O catálogo pode mudar; o holding registra o que foi contratado.

  Sem foreign key para o catálogo de bonds:
    Bonds do catálogo são configurados em memória (não no banco).
    Um holding pode referenciar um bond personalizado criado pelo usuário
    no futuro. A integridade é validada na camada de aplicação.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import (
    Boolean,
    Date,
    Float,
    String,
    Text,
    delete,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from finanalytics_ai.domain.fixed_income.portfolio import RFHolding, RFPortfolio

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


# ── ORM Models ────────────────────────────────────────────────────────────────


class Base(DeclarativeBase):
    pass


class RFPortfolioModel(Base):
    __tablename__ = "rf_portfolios"

    portfolio_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[date | None] = mapped_column(Date, nullable=False, default=date.today)


class RFHoldingModel(Base):
    __tablename__ = "rf_holdings"

    holding_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    bond_id: Mapped[str] = mapped_column(String(100), nullable=False)
    bond_name: Mapped[str] = mapped_column(String(200), nullable=False)
    bond_type: Mapped[str] = mapped_column(String(50), nullable=False)
    indexer: Mapped[str] = mapped_column(String(30), nullable=False)
    issuer: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    invested: Mapped[float] = mapped_column(Float, nullable=False)
    rate_annual: Mapped[float] = mapped_column(Float, nullable=False)
    rate_pct_indexer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)
    maturity_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ir_exempt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")


# ── Repository ────────────────────────────────────────────────────────────────


class RFPortfolioRepository:
    """CRUD assíncrono para carteiras de Renda Fixa."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Portfolio ──────────────────────────────────────────────────────────────

    async def create_portfolio(self, user_id: str, name: str) -> RFPortfolio:
        pid = str(uuid.uuid4())
        model = RFPortfolioModel(
            portfolio_id=pid,
            user_id=user_id,
            name=name,
            created_at=date.today(),
        )
        self._session.add(model)
        await self._session.flush()
        logger.info("rf_portfolio.created", portfolio_id=pid, user_id=user_id)
        return RFPortfolio(portfolio_id=pid, user_id=user_id, name=name, created_at=date.today())

    async def get_portfolio(self, portfolio_id: str) -> RFPortfolio | None:
        result = await self._session.execute(
            select(RFPortfolioModel).where(RFPortfolioModel.portfolio_id == portfolio_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        holdings = await self._get_holdings(portfolio_id)
        return RFPortfolio(
            portfolio_id=row.portfolio_id,
            user_id=row.user_id,
            name=row.name,
            holdings=holdings,
            created_at=row.created_at,
        )

    async def list_portfolios(self, user_id: str) -> list[RFPortfolio]:
        result = await self._session.execute(
            select(RFPortfolioModel).where(RFPortfolioModel.user_id == user_id)
        )
        rows = result.scalars().all()
        portfolios = []
        for row in rows:
            holdings = await self._get_holdings(row.portfolio_id)
            portfolios.append(
                RFPortfolio(
                    portfolio_id=row.portfolio_id,
                    user_id=row.user_id,
                    name=row.name,
                    holdings=holdings,
                    created_at=row.created_at,
                )
            )
        return portfolios

    async def delete_portfolio(self, portfolio_id: str) -> None:
        await self._session.execute(delete(RFHoldingModel).where(RFHoldingModel.portfolio_id == portfolio_id))
        await self._session.execute(
            delete(RFPortfolioModel).where(RFPortfolioModel.portfolio_id == portfolio_id)
        )

    # ── Holdings ───────────────────────────────────────────────────────────────

    async def add_holding(
        self,
        portfolio_id: str,
        bond_id: str,
        bond_name: str,
        bond_type: str,
        indexer: str,
        issuer: str,
        invested: float,
        rate_annual: float,
        rate_pct_indexer: bool,
        purchase_date: date,
        maturity_date: date | None,
        ir_exempt: bool,
        note: str = "",
    ) -> RFHolding:
        hid = str(uuid.uuid4())
        model = RFHoldingModel(
            holding_id=hid,
            portfolio_id=portfolio_id,
            bond_id=bond_id,
            bond_name=bond_name,
            bond_type=bond_type,
            indexer=indexer,
            issuer=issuer,
            invested=invested,
            rate_annual=rate_annual,
            rate_pct_indexer=rate_pct_indexer,
            purchase_date=purchase_date,
            maturity_date=maturity_date,
            ir_exempt=ir_exempt,
            note=note,
        )
        self._session.add(model)
        await self._session.flush()
        logger.info(
            "rf_holding.added",
            holding_id=hid,
            portfolio_id=portfolio_id,
            bond_name=bond_name,
            invested=invested,
        )
        return RFHolding(
            holding_id=hid,
            portfolio_id=portfolio_id,
            bond_id=bond_id,
            bond_name=bond_name,
            bond_type=bond_type,
            indexer=indexer,
            issuer=issuer,
            invested=invested,
            rate_annual=rate_annual,
            rate_pct_indexer=rate_pct_indexer,
            purchase_date=purchase_date,
            maturity_date=maturity_date,
            ir_exempt=ir_exempt,
            note=note,
        )

    async def delete_holding(self, holding_id: str, portfolio_id: str) -> None:
        await self._session.execute(
            delete(RFHoldingModel).where(
                RFHoldingModel.holding_id == holding_id,
                RFHoldingModel.portfolio_id == portfolio_id,
            )
        )

    async def _get_holdings(self, portfolio_id: str) -> list[RFHolding]:
        result = await self._session.execute(
            select(RFHoldingModel).where(RFHoldingModel.portfolio_id == portfolio_id)
        )
        return [
            RFHolding(
                holding_id=r.holding_id,
                portfolio_id=r.portfolio_id,
                bond_id=r.bond_id,
                bond_name=r.bond_name,
                bond_type=r.bond_type,
                indexer=r.indexer,
                issuer=r.issuer,
                invested=r.invested,
                rate_annual=r.rate_annual,
                rate_pct_indexer=r.rate_pct_indexer,
                purchase_date=r.purchase_date,
                maturity_date=r.maturity_date,
                ir_exempt=r.ir_exempt,
                note=r.note or "",
            )
            for r in result.scalars().all()
        ]
