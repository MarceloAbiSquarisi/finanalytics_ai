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

from datetime import date
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import (
    Boolean,
    Date,
    Float,
    Integer,
    String,
    Text,
    delete,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
import structlog

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
    # Feature C5 (24/abr): dias para liquidar resgate. Default 1 (CDB LD).
    liquidity_days: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")


# ── Repository ────────────────────────────────────────────────────────────────


class RFPortfolioRepository:
    """CRUD assíncrono para carteiras de Renda Fixa."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Portfolio ──────────────────────────────────────────────────────────────

    async def create_portfolio(
        self, user_id: str, name: str, investment_account_id: str | None = None
    ) -> RFPortfolio:
        # Bug fix (21/abr): rf_holdings.portfolio_id tem FK para portfolios.id
        # (sistema unificado), nao para rf_portfolios. Antes, rf_portfolios
        # gerava UUID novo que nunca existia em portfolios — qualquer add_holding
        # subsequente violava a FK. Solucao: criar entry em portfolios com
        # mesmo PK e marcar nome como "RF: <name>" para distinguir na UI
        # /portfolios. Followup ideal: migrar /fixed-income para usar
        # portfolios direto e dropar rf_portfolios.
        # BUG11 fix (26/abr): aceita investment_account_id pra cash hooks
        # rf_apply/rf_redeem encontrarem a conta dona e atualizarem cash_balance.
        from finanalytics_ai.infrastructure.database.repositories.portfolio_repo import (
            PortfolioModel,
        )

        pid = str(uuid.uuid4())
        # Cria portfolio "espelho" no sistema unificado primeiro
        portfolio_mirror = PortfolioModel(
            id=pid,
            user_id=user_id,
            investment_account_id=investment_account_id,
            name=f"RF: {name}",
            description="Carteira de Renda Fixa (criada via /fixed-income)",
            benchmark="CDI",
            is_active=True,
            currency="BRL",
            cash=0,
        )
        self._session.add(portfolio_mirror)
        await self._session.flush()
        # Cria rf_portfolios com mesmo PK
        model = RFPortfolioModel(
            portfolio_id=pid,
            user_id=user_id,
            name=name,
            created_at=date.today(),
        )
        self._session.add(model)
        await self._session.flush()
        logger.info("rf_portfolio.created", portfolio_id=pid, user_id=user_id, mirror=True)
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
        await self._session.execute(
            delete(RFHoldingModel).where(RFHoldingModel.portfolio_id == portfolio_id)
        )
        await self._session.execute(
            delete(RFPortfolioModel).where(RFPortfolioModel.portfolio_id == portfolio_id)
        )

    # ── Holdings ───────────────────────────────────────────────────────────────

    # Feature C5 (24/abr): liquidity_days default por bond_type
    _LIQUIDITY_DEFAULTS: dict[str, int] = {
        "poupanca": 0,
        "tesouro_direto": 1,
        "tesouro": 1,
        "cdb": 1,
        "lci": 30,
        "lca": 30,
        "lcd": 30,
        "cra": 30,
        "cri": 30,
        "debenture": 3,
        "debentures": 3,
        "fundo_rf": 30,
        "fundo": 30,
    }

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
        liquidity_days: int | None = None,
    ) -> RFHolding:
        hid = str(uuid.uuid4())
        if liquidity_days is None:
            liquidity_days = self._LIQUIDITY_DEFAULTS.get(bond_type.lower(), 1)
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
            liquidity_days=liquidity_days,
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
        # Feature C5: hook cash — rf_apply settled D+0 (debita na data da aplicacao)
        await self._emit_rf_cash_tx(
            portfolio_id=portfolio_id,
            holding_id=hid,
            tx_type="rf_apply",
            amount=-abs(invested),
            reference_date=purchase_date,
            settlement_date=purchase_date,
            status="settled",
            note=f"Aplicação {bond_name}",
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

    async def _emit_rf_cash_tx(
        self,
        *,
        portfolio_id: str,
        holding_id: str,
        tx_type: str,
        amount: float,
        reference_date: date,
        settlement_date: date,
        status: str,
        note: str,
    ) -> None:
        """Feature C5: gera account_transaction vinculada ao holding RF.
        Resolve (user_id, investment_account_id) via portfolio."""
        from decimal import Decimal
        from sqlalchemy import text as sql_text
        from finanalytics_ai.infrastructure.database.repositories.wallet_repo import (
            WalletRepository,
        )

        try:
            row = (
                (
                    await self._session.execute(
                        sql_text(
                            "SELECT user_id, investment_account_id FROM portfolios WHERE id = :pid"
                        ),
                        {"pid": portfolio_id},
                    )
                )
                .mappings()
                .first()
            )
            if not row or not row.get("investment_account_id"):
                logger.debug(
                    "rf_holding.cash_tx.skipped",
                    reason="no_investment_account",
                    portfolio_id=portfolio_id,
                )
                return
            await WalletRepository().create_transaction(
                user_id=str(row["user_id"]),
                account_id=str(row["investment_account_id"]),
                tx_type=tx_type,
                amount=Decimal(str(amount)),
                reference_date=reference_date,
                settlement_date=settlement_date,
                status=status,
                related_type="rf_holding",
                related_id=holding_id,
                note=note,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("rf_holding.cash_tx.failed", holding_id=holding_id, error=str(exc))

    async def delete_holding(self, holding_id: str, portfolio_id: str) -> None:
        # C5: cancela tx rf_apply (pending ou settled → reverte cash)
        from finanalytics_ai.infrastructure.database.repositories.wallet_repo import (
            WalletRepository,
            AccountTransactionModel,
        )

        repo = WalletRepository()
        # Busca user_id antes de deletar
        pf_row = (
            await self._session.execute(
                select(RFHoldingModel).where(RFHoldingModel.holding_id == holding_id)
            )
        ).scalar_one_or_none()
        user_id = None
        if pf_row:
            from sqlalchemy import text as sql_text

            r = (
                (
                    await self._session.execute(
                        sql_text("SELECT user_id FROM portfolios WHERE id = :pid"),
                        {"pid": pf_row.portfolio_id},
                    )
                )
                .mappings()
                .first()
            )
            user_id = r["user_id"] if r else None

        await self._session.execute(
            delete(RFHoldingModel).where(
                RFHoldingModel.holding_id == holding_id,
                RFHoldingModel.portfolio_id == portfolio_id,
            )
        )

        # Cancela todas as tx vinculadas
        if user_id:
            txs = (
                (
                    await self._session.execute(
                        select(AccountTransactionModel).where(
                            AccountTransactionModel.related_type == "rf_holding",
                            AccountTransactionModel.related_id == holding_id,
                            AccountTransactionModel.status != "cancelled",
                        )
                    )
                )
                .scalars()
                .all()
            )
            for tx in txs:
                await repo.cancel_transaction(tx.id, str(user_id))

    async def redeem_holding(
        self, holding_id: str, portfolio_id: str, amount: float
    ) -> dict | None:
        """Decrementa invested. Se zerar/negativo, deleta o holding.
        Feature C5: cria tx rf_redeem pending com settlement D+liquidity_days."""
        from datetime import timedelta as _td

        result = await self._session.execute(
            select(RFHoldingModel).where(
                RFHoldingModel.holding_id == holding_id,
                RFHoldingModel.portfolio_id == portfolio_id,
            )
        )
        m = result.scalar_one_or_none()
        if m is None:
            return None
        bond_name = m.bond_name
        liq_days = int(getattr(m, "liquidity_days", 1) or 1)
        new_invested = float(m.invested) - amount
        if new_invested <= 0:
            await self._session.delete(m)
            await self._session.flush()
            status = "removed"
            remaining = 0.0
        else:
            m.invested = new_invested
            await self._session.flush()
            status = "redeemed"
            remaining = float(m.invested)

        # C5: tx rf_redeem pending com settlement_date = today + liquidity_days
        today = date.today()
        settle_date = today + _td(days=liq_days)
        await self._emit_rf_cash_tx(
            portfolio_id=portfolio_id,
            holding_id=holding_id,
            tx_type="rf_redeem",
            amount=abs(amount),
            reference_date=today,
            settlement_date=settle_date,
            status="pending",
            note=f"Resgate {bond_name} (D+{liq_days})",
        )
        return {
            "status": status,
            "remaining_invested": remaining,
            "liquidity_days": liq_days,
            "settlement_date": settle_date.isoformat(),
        }

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
