"""
infrastructure/database/repositories/wallet_repo.py
Repositório para contas de investimento, trades, cripto e outros ativos.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
import uuid

from sqlalchemy import Boolean, Date, DateTime, Numeric, String, Text, func, select, text
from sqlalchemy.orm import Mapped, mapped_column
import structlog

from finanalytics_ai.infrastructure.database.connection import Base, get_session

log = structlog.get_logger(__name__)


# ── SQLAlchemy Models ─────────────────────────────────────────────────────


class InvestmentAccountModel(Base):
    __tablename__ = "investment_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    titular: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cpf: Mapped[str | None] = mapped_column(String(14), nullable=True)
    apelido: Mapped[str | None] = mapped_column(String(100), nullable=True)
    institution_name: Mapped[str] = mapped_column(String(200), nullable=False)
    institution_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    agency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    account_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    country: Mapped[str] = mapped_column(String(3), nullable=False, default="BRA")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    account_type: Mapped[str] = mapped_column(String(30), nullable=False, default="corretora")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Credenciais Profit DLL (unificacao U1, 24/abr) — conta pode ou nao ter conexao DLL
    dll_broker_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    dll_account_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    dll_sub_account_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    dll_routing_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    dll_account_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # 'real' | 'simulator'
    is_dll_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Flag ADMIN-only: libera envio de ordens REAIS para esta conta (C3 24/abr).
    # Default FALSE — conta recem-criada so pode operar simulador ate admin liberar
    # (evita acidente de rodar estrategia em conta real sem autorizacao).
    real_operations_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Saldo cash settled (Feature C, 24/abr). Mantido pelo AccountTransactionService.
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TradeModel(Base):
    __tablename__ = "trades"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    investment_account_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    portfolio_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    asset_class: Mapped[str] = mapped_column(String(30), nullable=False)
    operation: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    fees: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CryptoHoldingModel(Base):
    __tablename__ = "crypto_holdings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    investment_account_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    portfolio_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    average_price_brl: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    average_price_usd: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(100), nullable=True)
    wallet_address: Mapped[str | None] = mapped_column(String(200), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OtherAssetModel(Base):
    __tablename__ = "other_assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    investment_account_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    portfolio_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(50), nullable=False)
    current_value: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    invested_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    acquisition_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    maturity_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ir_exempt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EtfMetadataModel(Base):
    """Metadata de ETF por ticker (benchmark, taxa adm/perf). C3b (24/abr).

    Atributo do PAPEL (nao do trade individual). Ao comprar BOVA11 em qualquer
    data, os fees/benchmark sao os mesmos — por isso tabela separada com
    PK=ticker, consultada pelo frontend ao abrir o form de trade ETF.
    """
    __tablename__ = "etf_metadata"
    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    benchmark: Mapped[str | None] = mapped_column(String(100), nullable=True)
    mgmt_fee: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    perf_fee: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    isin: Mapped[str | None] = mapped_column(String(12), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)


class AccountTransactionModel(Base):
    """Feature C (24/abr): movimentacao de caixa por investment_account.

    amount positivo = credito, negativo = debito. status pending|settled|cancelled.
    cash_balance em investment_accounts e mantido pelo AccountTransactionService.
    """

    __tablename__ = "account_transactions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    tx_type: Mapped[str] = mapped_column(String(30), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="settled")
    reference_date: Mapped[date] = mapped_column(Date, nullable=False)
    settlement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    related_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    related_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ── Helper ────────────────────────────────────────────────────────────────


_SENSITIVE_FIELDS: frozenset[str] = frozenset({"dll_routing_password"})


def _model_to_dict(m: Any, *, include_sensitive: bool = False) -> dict:
    d = {}
    for c in m.__table__.columns:
        v = getattr(m, c.name)
        if c.name in _SENSITIVE_FIELDS and not include_sensitive:
            # Ocultar mas indicar presenca (boolean flag util para UI)
            d[f"{c.name}_set"] = bool(v)
            continue
        if isinstance(v, Decimal):
            v = float(v)
        elif isinstance(v, (date, datetime)):
            v = v.isoformat()
        d[c.name] = v
    return d


# ── WalletRepository ──────────────────────────────────────────────────────


class WalletRepository:
    """Repo unificado para carteira multi-usuário."""

    # ── Investment Accounts ───────────────────────────────────────────────

    async def create_account(self, data: dict) -> dict:
        data.setdefault("id", str(uuid.uuid4()))
        async with get_session() as s:
            m = InvestmentAccountModel(
                **{
                    k: v
                    for k, v in data.items()
                    if k in InvestmentAccountModel.__table__.columns.keys()
                }
            )
            s.add(m)
            await s.commit()
            await s.refresh(m)
            acc = _model_to_dict(m)

        # G1 (24/abr): auto-criar portfolio Principal + RF Padrao vinculados
        try:
            await self._ensure_default_portfolios(
                user_id=str(data["user_id"]),
                account_id=acc["id"],
                account_label=acc.get("apelido") or acc.get("institution_name") or "Conta",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("account.auto_portfolios.failed", account_id=acc["id"], error=str(exc))
        return acc

    async def _ensure_default_portfolios(
        self, *, user_id: str, account_id: str, account_label: str
    ) -> None:
        """Cria 1 portfolio chamado 'Portfolio' vinculado a uma conta, se nao existir.

        Refactor 25/abr: modelo simplificado de N para 1 portfolio por conta.
        Chamado em create_account (automatico) e no backfill para contas antigas.
        Idempotente: checa antes de criar.
        """
        from sqlalchemy import text as sql_text

        async with get_session() as s:
            # Checa se ja existe portfolio ativo nesta conta
            pf_row = (await s.execute(
                sql_text(
                    "SELECT id FROM portfolios "
                    "WHERE investment_account_id = :a AND user_id = :u "
                    "AND COALESCE(is_active, true) = true LIMIT 1"
                ),
                {"a": account_id, "u": user_id},
            )).scalar_one_or_none()
            if not pf_row:
                pf_id = str(uuid.uuid4())
                await s.execute(
                    sql_text(
                        "INSERT INTO portfolios "
                        "(id, user_id, name, investment_account_id, currency, cash, is_active, created_at, updated_at) "
                        "VALUES (:id, :u, 'Portfolio', :a, 'BRL', 0, true, NOW(), NOW())"
                    ),
                    {"id": pf_id, "u": user_id, "a": account_id},
                )
                log.info("account.auto_portfolio.created", account_id=account_id, portfolio_id=pf_id)

            await s.commit()

    async def list_accounts(self, user_id: str, include_inactive: bool = False) -> list[dict]:
        async with get_session() as s:
            q = select(InvestmentAccountModel).where(InvestmentAccountModel.user_id == user_id)
            if not include_inactive:
                q = q.where(InvestmentAccountModel.is_active.is_(True))
            q = q.order_by(InvestmentAccountModel.institution_name)
            rows = (await s.execute(q)).scalars().all()
            return [_model_to_dict(r) for r in rows]

    async def get_account(self, account_id: str, user_id: str) -> dict | None:
        async with get_session() as s:
            q = select(InvestmentAccountModel).where(
                InvestmentAccountModel.id == account_id,
                InvestmentAccountModel.user_id == user_id,
            )
            m = (await s.execute(q)).scalar_one_or_none()
            return _model_to_dict(m) if m else None

    async def update_account(self, account_id: str, user_id: str, data: dict) -> dict | None:
        # Campos proibidos no PATCH genérico (usar endpoints dedicados)
        blocked = {"id", "user_id", "created_at",
                   "dll_broker_id", "dll_account_id", "dll_sub_account_id",
                   "dll_routing_password", "dll_account_type", "is_dll_active"}
        async with get_session() as s:
            q = select(InvestmentAccountModel).where(
                InvestmentAccountModel.id == account_id,
                InvestmentAccountModel.user_id == user_id,
            )
            m = (await s.execute(q)).scalar_one_or_none()
            if not m:
                return None
            for k, v in data.items():
                if hasattr(m, k) and k not in blocked:
                    setattr(m, k, v)
            await s.commit()
            await s.refresh(m)
            return _model_to_dict(m)

    # ── Operacoes DLL dedicadas (Unificacao U2, 24/abr) ──────────────────────

    async def connect_dll(
        self,
        account_id: str,
        user_id: str,
        *,
        account_type: str,  # 'real' | 'simulator'
        broker_id: str | None = None,
        dll_account_id: str | None = None,
        routing_password: str | None = None,
        sub_account_id: str | None = None,
    ) -> dict | None:
        """Vincula credenciais Profit DLL a uma investment_account existente.

        Regras:
          - account_type='simulator' NAO requer broker_id/account_id/password
            (usa fallback PROFIT_SIM_* do .env)
          - account_type='real' EXIGE broker_id, dll_account_id e password
          - account_type e imutavel apos primeira conexao (checa no service)
        """
        if account_type not in ("real", "simulator"):
            raise ValueError(f"account_type deve ser 'real' ou 'simulator', recebi {account_type!r}")
        if account_type == "real" and not (broker_id and dll_account_id and routing_password):
            raise ValueError("Conta real requer broker_id, dll_account_id e routing_password")

        async with get_session() as s:
            q = select(InvestmentAccountModel).where(
                InvestmentAccountModel.id == account_id,
                InvestmentAccountModel.user_id == user_id,
            )
            m = (await s.execute(q)).scalar_one_or_none()
            if not m:
                return None
            # account_type imutavel se ja tinha DLL conectada
            if m.dll_account_type and m.dll_account_type != account_type:
                raise ValueError(
                    f"account_type DLL imutavel: conta ja esta como {m.dll_account_type!r}"
                )
            m.dll_account_type = account_type
            m.dll_broker_id = broker_id
            m.dll_account_id = dll_account_id
            m.dll_routing_password = routing_password
            m.dll_sub_account_id = sub_account_id
            await s.commit()
            await s.refresh(m)
            return _model_to_dict(m)

    async def disconnect_dll(self, account_id: str, user_id: str) -> dict | None:
        async with get_session() as s:
            q = select(InvestmentAccountModel).where(
                InvestmentAccountModel.id == account_id,
                InvestmentAccountModel.user_id == user_id,
            )
            m = (await s.execute(q)).scalar_one_or_none()
            if not m:
                return None
            m.dll_broker_id = None
            m.dll_account_id = None
            m.dll_sub_account_id = None
            m.dll_routing_password = None
            m.dll_account_type = None
            m.is_dll_active = False
            await s.commit()
            await s.refresh(m)
            return _model_to_dict(m)

    async def set_dll_active(self, account_id: str, user_id: str) -> dict | None:
        """Marca conta como DLL ativa (unica por user). Transacional."""
        async with get_session() as s:
            # Desativa todas as outras do mesmo user
            await s.execute(
                select(InvestmentAccountModel)
                .where(InvestmentAccountModel.user_id == user_id)
                .where(InvestmentAccountModel.is_dll_active.is_(True))
            )
            # Usar UPDATE direto para evitar ORM overhead / conflito com unique index
            from sqlalchemy import update as sql_update
            await s.execute(
                sql_update(InvestmentAccountModel)
                .where(InvestmentAccountModel.user_id == user_id)
                .where(InvestmentAccountModel.id != account_id)
                .values(is_dll_active=False)
            )
            # Ativa a desejada (exige DLL conectada)
            q = select(InvestmentAccountModel).where(
                InvestmentAccountModel.id == account_id,
                InvestmentAccountModel.user_id == user_id,
            )
            m = (await s.execute(q)).scalar_one_or_none()
            if not m:
                return None
            if not m.dll_account_type:
                raise ValueError("Conta nao tem credenciais DLL conectadas — conecte primeiro via /connect-dll")
            m.is_dll_active = True
            await s.commit()
            await s.refresh(m)
            return _model_to_dict(m)

    async def get_dll_active(self, user_id: str | None = None) -> dict | None:
        """Retorna a conta DLL ativa (para o proxy profit_agent).

        Se user_id for None, retorna qualquer conta dll_active=True (uso em dev/single-user).
        """
        async with get_session() as s:
            q = select(InvestmentAccountModel).where(InvestmentAccountModel.is_dll_active.is_(True))
            if user_id:
                q = q.where(InvestmentAccountModel.user_id == user_id)
            m = (await s.execute(q)).scalar_one_or_none()
            return _model_to_dict(m, include_sensitive=True) if m else None

    async def set_real_operations(self, account_id: str, user_id: str, allowed: bool) -> dict | None:
        """Liga/desliga permissao de envio de ordens REAIS (ADMIN/MASTER-only).
        Validacao de role feita na rota antes de chamar."""
        async with get_session() as s:
            q = select(InvestmentAccountModel).where(
                InvestmentAccountModel.id == account_id,
                InvestmentAccountModel.user_id == user_id,
            )
            m = (await s.execute(q)).scalar_one_or_none()
            if not m:
                return None
            m.real_operations_allowed = bool(allowed)
            await s.commit()
            await s.refresh(m)
            return _model_to_dict(m)

    # ── Feature C: account_transactions (cash ledger) ────────────────────────

    async def create_transaction(
        self,
        *,
        user_id: str,
        account_id: str,
        tx_type: str,
        amount: Decimal,
        reference_date: date,
        settlement_date: date | None = None,
        status: str = "settled",
        related_type: str | None = None,
        related_id: str | None = None,
        note: str | None = None,
    ) -> dict:
        """Cria tx e atualiza cash_balance atomicamente (se settled).

        Para status=pending, cash_balance nao muda ate settle_transaction rodar
        (scheduler diario ou manual).
        """
        from sqlalchemy import update as sql_update

        tx_id = str(uuid.uuid4())
        if settlement_date is None:
            settlement_date = reference_date  # D+0 default
        async with get_session() as s:
            # Insere tx
            s.add(
                AccountTransactionModel(
                    id=tx_id,
                    user_id=user_id,
                    account_id=account_id,
                    tx_type=tx_type,
                    amount=amount,
                    status=status,
                    reference_date=reference_date,
                    settlement_date=settlement_date,
                    related_type=related_type,
                    related_id=related_id,
                    note=note,
                    settled_at=(datetime.now() if status == "settled" else None),
                )
            )
            # Se ja settled, atualiza cash_balance da conta
            if status == "settled":
                await s.execute(
                    sql_update(InvestmentAccountModel)
                    .where(InvestmentAccountModel.id == account_id)
                    .where(InvestmentAccountModel.user_id == user_id)
                    .values(cash_balance=InvestmentAccountModel.cash_balance + amount)
                )
            await s.commit()
            m = await s.get(AccountTransactionModel, tx_id)
            return _model_to_dict(m) if m else {}

    async def settle_transaction(self, tx_id: str, user_id: str) -> dict | None:
        """Marca tx pending como settled e aplica o amount no cash_balance.
        Idempotente (tx ja settled e no-op)."""
        from sqlalchemy import update as sql_update

        async with get_session() as s:
            m = await s.get(AccountTransactionModel, tx_id)
            if not m or m.user_id != user_id:
                return None
            if m.status == "settled":
                return _model_to_dict(m)
            if m.status == "cancelled":
                return _model_to_dict(m)
            m.status = "settled"
            m.settled_at = datetime.now()
            await s.execute(
                sql_update(InvestmentAccountModel)
                .where(InvestmentAccountModel.id == m.account_id)
                .values(cash_balance=InvestmentAccountModel.cash_balance + m.amount)
            )
            await s.commit()
            await s.refresh(m)
            return _model_to_dict(m)

    async def reconcile_transaction(
        self, tx_id: str, user_id: str, ticker: str
    ) -> dict | None:
        """C6 Fase 4 (26/abr): vincula tx unmatched a uma posição via ticker.

        Procura position do user_id que tenha o ticker; se encontra, seta
        related_id=position_id e related_type='position'. Atualiza note com
        marker '[reconciled]'. Retorna dict do tx atualizado, None se não
        achou tx OU None se não há posição para o ticker.
        """
        from finanalytics_ai.infrastructure.database.repositories.portfolio_repo import (
            PortfolioModel,
            PositionModel,
        )

        async with get_session() as s:
            tx = await s.get(AccountTransactionModel, tx_id)
            if not tx or tx.user_id != user_id:
                return None
            # Procura position com ticker em qualquer portfolio do user
            row = await s.execute(
                select(PositionModel.id)
                .join(PortfolioModel, PortfolioModel.id == PositionModel.portfolio_id)
                .where(PortfolioModel.user_id == user_id)
                .where(PositionModel.ticker == ticker.upper())
                .limit(1)
            )
            pos_id_row = row.first()
            if not pos_id_row:
                return {"error": f"Sem posição para ticker '{ticker}' no portfolio do usuário"}
            tx.related_type = "position"
            tx.related_id = str(pos_id_row[0])
            base_note = (tx.note or "").rstrip()
            if "[reconciled" not in base_note:
                tx.note = (base_note + f" [reconciled→{ticker.upper()}]").lstrip()
            await s.commit()
            await s.refresh(tx)
            return _model_to_dict(tx)

    async def cancel_transaction(self, tx_id: str, user_id: str) -> dict | None:
        """Cancela tx. Se ja estava settled, reverte o cash_balance."""
        from sqlalchemy import update as sql_update

        async with get_session() as s:
            m = await s.get(AccountTransactionModel, tx_id)
            if not m or m.user_id != user_id:
                return None
            if m.status == "cancelled":
                return _model_to_dict(m)
            was_settled = m.status == "settled"
            m.status = "cancelled"
            if was_settled:
                # Reverte o efeito no cash_balance
                await s.execute(
                    sql_update(InvestmentAccountModel)
                    .where(InvestmentAccountModel.id == m.account_id)
                    .values(cash_balance=InvestmentAccountModel.cash_balance - m.amount)
                )
            await s.commit()
            await s.refresh(m)
            return _model_to_dict(m)

    async def list_transactions(
        self,
        user_id: str,
        account_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        date_from: date | None = None,
        date_to: date | None = None,
        direction: str | None = None,  # 'debit' | 'credit' | None (todos)
        include_pending: bool = True,
    ) -> list[dict]:
        async with get_session() as s:
            q = (
                select(AccountTransactionModel)
                .where(AccountTransactionModel.user_id == user_id)
                .order_by(
                    AccountTransactionModel.reference_date.desc(),
                    AccountTransactionModel.created_at.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
            if account_id:
                q = q.where(AccountTransactionModel.account_id == account_id)
            if status:
                q = q.where(AccountTransactionModel.status == status)
            if date_from:
                q = q.where(AccountTransactionModel.reference_date >= date_from)
            if date_to:
                q = q.where(AccountTransactionModel.reference_date <= date_to)
            if direction == "debit":
                q = q.where(AccountTransactionModel.amount < 0)
            elif direction == "credit":
                q = q.where(AccountTransactionModel.amount > 0)
            if not include_pending:
                q = q.where(AccountTransactionModel.status != "pending")
            rows = (await s.execute(q)).scalars().all()
            # Calcula running_balance (saldo apos cada tx) por conta.
            # So faz sentido se filtrou por uma unica conta (senao, misturar
            # accounts distintos nao produz saldo util). E precisa considerar
            # TODAS as tx (nao so filtradas) para ter o saldo real — por isso
            # fazemos 2 passes: calcula running com todas, filtra para retorno.
            if account_id:
                # Pega todas as tx da conta (sem filtros de periodo/dir/status)
                # e ordena por data ASC para acumular
                all_q = select(AccountTransactionModel).where(
                    AccountTransactionModel.user_id == user_id,
                    AccountTransactionModel.account_id == account_id,
                ).order_by(
                    AccountTransactionModel.reference_date.asc(),
                    AccountTransactionModel.created_at.asc(),
                )
                all_rows = (await s.execute(all_q)).scalars().all()
                balance_map: dict[str, Decimal] = {}
                acc_bal = Decimal("0")
                for tx in all_rows:
                    # Pending nao entra no saldo real — mas marcamos o saldo atual
                    # (sem aplicar) para o front decidir mostrar projecao
                    if tx.status == "settled":
                        acc_bal += tx.amount
                    balance_map[tx.id] = acc_bal
                out = []
                for r in rows:
                    d = _model_to_dict(r)
                    d["running_balance"] = float(balance_map.get(r.id, Decimal("0")))
                    out.append(d)
                return out
            return [_model_to_dict(r) for r in rows]

    async def get_cash_summary(self, account_id: str, user_id: str) -> dict | None:
        """Retorna cash_balance + pending_in + pending_out + available_to_invest."""
        async with get_session() as s:
            acc = await s.execute(
                select(InvestmentAccountModel).where(
                    InvestmentAccountModel.id == account_id,
                    InvestmentAccountModel.user_id == user_id,
                )
            )
            acc_m = acc.scalar_one_or_none()
            if not acc_m:
                return None

            cash = float(acc_m.cash_balance or Decimal("0"))

            # Soma pending in/out via SQL agregado
            pending = await s.execute(
                text(
                    "SELECT "
                    "COALESCE(SUM(CASE WHEN amount > 0 THEN amount END), 0) AS pending_in, "
                    "COALESCE(SUM(CASE WHEN amount < 0 THEN amount END), 0) AS pending_out "
                    "FROM account_transactions "
                    "WHERE account_id = :aid AND user_id = :uid AND status = 'pending'"
                ),
                {"aid": account_id, "uid": user_id},
            )
            row = pending.mappings().first() or {}
            p_in = float(row.get("pending_in") or 0)
            p_out = float(row.get("pending_out") or 0)  # negativo
            return {
                "account_id": account_id,
                "cash_balance": cash,
                "pending_in": p_in,
                "pending_out": p_out,  # negativo
                "available_to_invest": cash + p_out,  # subtrai saidas agendadas
            }

    async def create_portfolio_in_account(
        self, *, user_id: str, account_id: str, name: str
    ) -> dict | None:
        """G2: cria portfolio vinculado a investment_account via SQL direto.
        Retorna dict com id + name, ou None se conta não existir ou não pertence ao user."""
        from sqlalchemy import text as sql_text

        async with get_session() as s:
            # Valida conta
            acc = (await s.execute(
                sql_text("SELECT id FROM investment_accounts WHERE id = :a AND user_id = :u AND is_active"),
                {"a": account_id, "u": user_id},
            )).scalar_one_or_none()
            if not acc:
                return None
            pf_id = str(uuid.uuid4())
            try:
                await s.execute(
                    sql_text(
                        "INSERT INTO portfolios "
                        "(id, user_id, name, investment_account_id, currency, cash, is_active, created_at, updated_at) "
                        "VALUES (:id, :u, :n, :a, 'BRL', 0, true, NOW(), NOW())"
                    ),
                    {"id": pf_id, "u": user_id, "n": name.strip(), "a": account_id},
                )
                await s.commit()
            except Exception as exc:  # noqa: BLE001
                if "ux_portfolios_one_active_per_account" in str(exc):
                    # Conta ja tem portfolio ativo (invariante 1:1)
                    await s.rollback()
                    return None
                raise
            return {"portfolio_id": pf_id, "name": name.strip(), "investment_account_id": account_id}

    # ── Feature C3b: ETF metadata ────────────────────────────────────────────

    async def list_etf_metadata(self, tickers: list[str] | None = None) -> list[dict]:
        async with get_session() as s:
            q = select(EtfMetadataModel).order_by(EtfMetadataModel.ticker)
            if tickers:
                q = q.where(EtfMetadataModel.ticker.in_([t.upper() for t in tickers]))
            rows = (await s.execute(q)).scalars().all()
            return [_model_to_dict(r) for r in rows]

    async def get_etf_metadata(self, ticker: str) -> dict | None:
        async with get_session() as s:
            m = await s.get(EtfMetadataModel, ticker.upper())
            return _model_to_dict(m) if m else None

    async def upsert_etf_metadata(self, ticker: str, data: dict, updated_by: str | None = None) -> dict:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        ticker = ticker.upper()
        payload = {
            "ticker": ticker,
            "name": data.get("name"),
            "benchmark": data.get("benchmark"),
            "mgmt_fee": Decimal(str(data["mgmt_fee"])) if data.get("mgmt_fee") is not None else None,
            "perf_fee": Decimal(str(data["perf_fee"])) if data.get("perf_fee") is not None else None,
            "isin": data.get("isin"),
            "note": data.get("note"),
            "updated_by": updated_by,
        }
        async with get_session() as s:
            stmt = pg_insert(EtfMetadataModel).values(**payload)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ticker"],
                set_={k: v for k, v in payload.items() if k != "ticker"},
            )
            await s.execute(stmt)
            await s.commit()
            m = await s.get(EtfMetadataModel, ticker)
            return _model_to_dict(m) if m else {}

    async def delete_etf_metadata(self, ticker: str) -> bool:
        async with get_session() as s:
            m = await s.get(EtfMetadataModel, ticker.upper())
            if not m:
                return False
            await s.delete(m)
            await s.commit()
            return True

    async def settle_due_transactions(self, today: date | None = None) -> int:
        """Varre pending com settlement_date <= hoje e marca como settled.
        Usa UPDATE com RETURNING + UPDATE atomico de cash_balance por conta."""
        from sqlalchemy import update as sql_update

        target = today or date.today()
        async with get_session() as s:
            # 1. Pega ids dos tx a liquidar
            result = await s.execute(
                select(AccountTransactionModel).where(
                    AccountTransactionModel.status == "pending",
                    AccountTransactionModel.settlement_date <= target,
                )
            )
            due = result.scalars().all()
            if not due:
                return 0

            # 2. Agrupa amount por conta
            by_account: dict[str, Decimal] = {}
            for tx in due:
                by_account[tx.account_id] = by_account.get(tx.account_id, Decimal("0")) + tx.amount
                tx.status = "settled"
                tx.settled_at = datetime.now()

            # 3. Atualiza cash_balance das contas
            for acc_id, delta in by_account.items():
                await s.execute(
                    sql_update(InvestmentAccountModel)
                    .where(InvestmentAccountModel.id == acc_id)
                    .values(cash_balance=InvestmentAccountModel.cash_balance + delta)
                )

            await s.commit()
            log.info("account_transactions.settled", count=len(due), accounts=len(by_account))
            return len(due)

    async def delete_account(self, account_id: str, user_id: str) -> bool:
        """Soft-delete: is_active=False. Bloqueia se cash_balance != 0 (F7) ou holdings (BUG14)."""
        from sqlalchemy import text as sql_text

        async with get_session() as s:
            q = select(InvestmentAccountModel).where(
                InvestmentAccountModel.id == account_id,
                InvestmentAccountModel.user_id == user_id,
            )
            m = (await s.execute(q)).scalar_one_or_none()
            if not m:
                return False
            if m.cash_balance and Decimal(str(m.cash_balance)) != Decimal("0"):
                # Rota converte para HTTPException 409 via ValueError
                raise ValueError(
                    f"Saldo R$ {m.cash_balance} diferente de zero. Zere via saque/depósito antes de excluir."
                )
            # BUG14 fix: bloqueia se há holdings ativos (trades, crypto, RF, outros)
            counts = (await s.execute(
                sql_text("""
                    SELECT
                      (SELECT COUNT(*) FROM trades WHERE investment_account_id = :acc) AS trades,
                      (SELECT COUNT(*) FROM crypto_holdings WHERE investment_account_id = :acc) AS crypto,
                      (SELECT COUNT(*) FROM other_assets WHERE investment_account_id = :acc) AS other,
                      (SELECT COUNT(*) FROM rf_holdings rh
                         JOIN portfolios p ON p.id = rh.portfolio_id
                         WHERE p.investment_account_id = :acc) AS rf
                """),
                {"acc": account_id},
            )).mappings().first() or {}
            total = sum(int(counts.get(k, 0) or 0) for k in ("trades", "crypto", "other", "rf"))
            if total > 0:
                raise ValueError(
                    f"Há investimentos vinculados ({total}: {dict(counts)}). Remova-os antes de excluir."
                )
            m.is_active = False
            await s.commit()
            return True

    # ── Variantes Master/Admin (sem filtro user_id) ───────────────────────

    async def list_all_accounts(self, include_inactive: bool = False) -> list[dict]:
        async with get_session() as s:
            q = select(InvestmentAccountModel)
            if not include_inactive:
                q = q.where(InvestmentAccountModel.is_active.is_(True))
            q = q.order_by(InvestmentAccountModel.user_id, InvestmentAccountModel.institution_name)
            rows = (await s.execute(q)).scalars().all()
            return [_model_to_dict(r) for r in rows]

    async def get_account_any_user(self, account_id: str) -> dict | None:
        async with get_session() as s:
            m = await s.get(InvestmentAccountModel, account_id)
            return _model_to_dict(m) if m else None

    async def update_account_any_user(self, account_id: str, data: dict) -> dict | None:
        async with get_session() as s:
            m = await s.get(InvestmentAccountModel, account_id)
            if not m:
                return None
            for k, v in data.items():
                if hasattr(m, k) and k not in ("id", "user_id", "created_at"):
                    setattr(m, k, v)
            await s.commit()
            await s.refresh(m)
            return _model_to_dict(m)

    async def delete_account_any_user(self, account_id: str) -> bool:
        async with get_session() as s:
            m = await s.get(InvestmentAccountModel, account_id)
            if not m:
                return False
            m.is_active = False
            await s.commit()
            return True

    # ── Portfolio resolution ─────────────────────────────────────────────

    async def get_default_portfolio_id(self, user_id: str) -> str | None:
        """Retorna id do portfolio mais antigo do usuario (1 por conta).
        Refactor 25/abr: removeu coluna is_default; ordem por created_at."""
        async with get_session() as s:
            r = await s.execute(
                text(
                    "SELECT id FROM portfolios WHERE user_id=:u "
                    "AND is_active=true ORDER BY created_at LIMIT 1"
                ),
                {"u": user_id},
            )
            row = r.first()
            return row[0] if row else None

    async def ensure_default_portfolio(self, user_id: str, name: str = "Portfolio") -> str:
        """Garante 1 portfolio para o usuario sem conta vinculada (legacy fallback).
        Refactor 25/abr: o caminho normal e _ensure_default_portfolios (com conta)."""
        existing = await self.get_default_portfolio_id(user_id)
        if existing:
            return existing
        new_id = str(uuid.uuid4())
        async with get_session() as s:
            await s.execute(
                text(
                    "INSERT INTO portfolios (id, user_id, name, currency, cash, is_active) "
                    "VALUES (:id, :u, :n, 'BRL', 0, true)"
                ),
                {"id": new_id, "u": user_id, "n": name},
            )
            await s.commit()
        return new_id

    async def validate_portfolio_belongs_to_user(self, portfolio_id: str, user_id: str) -> bool:
        async with get_session() as s:
            r = await s.execute(
                text("SELECT 1 FROM portfolios WHERE id=:p AND user_id=:u"),
                {"p": portfolio_id, "u": user_id},
            )
            return r.first() is not None

    # ── Trades ────────────────────────────────────────────────────────────

    async def create_trade(self, data: dict) -> dict:
        from datetime import timedelta as _td

        data.setdefault("id", str(uuid.uuid4()))
        if "total_cost" not in data:
            data["total_cost"] = float(data["quantity"]) * float(data["unit_price"]) + float(
                data.get("fees", 0)
            )
        async with get_session() as s:
            m = TradeModel(
                **{k: v for k, v in data.items() if k in TradeModel.__table__.columns.keys()}
            )
            s.add(m)
            await s.commit()
            await s.refresh(m)
            trade_dict = _model_to_dict(m)

        # Feature C — Hook C3: cria account_transaction pending D+1 (B3 T+1).
        # Aplica so quando trade vinculado a uma investment_account + operation
        # gera fluxo de caixa (buy/sell). Splits/bonus nao mexem em cash.
        op = (data.get("operation") or "").lower()
        acc_id = data.get("investment_account_id")
        if acc_id and op in ("buy", "sell"):
            trade_date = data.get("trade_date")
            if isinstance(trade_date, str):
                trade_date = date.fromisoformat(trade_date[:10])
            total_cost = Decimal(str(data["total_cost"]))
            amount = -total_cost if op == "buy" else total_cost
            settlement = (trade_date or date.today()) + _td(days=1)
            ticker = (data.get("ticker") or "").upper()
            qty = data.get("quantity")
            unit = data.get("unit_price")
            note = f"{op.upper()} {ticker} x{qty} @ R$ {unit}"
            try:
                await self.create_transaction(
                    user_id=str(data["user_id"]),
                    account_id=acc_id,
                    tx_type=f"trade_{op}",
                    amount=amount,
                    reference_date=trade_date or date.today(),
                    settlement_date=settlement,
                    status="pending",
                    related_type="trade",
                    related_id=trade_dict.get("id"),
                    note=note,
                )
                trade_dict["cash_tx_created"] = True
                # Checa saldo para warning (nao bloqueante)
                summary = await self.get_cash_summary(acc_id, str(data["user_id"]))
                if summary and op == "buy":
                    after = summary["cash_balance"] + summary["pending_out"]  # pending_out e negativo
                    if after < 0:
                        trade_dict["warning"] = (
                            f"Saldo ficará negativo (R$ {after:.2f}) após esta compra liquidar em D+1. "
                            f"Considere aportar antes."
                        )
            except Exception as exc:  # noqa: BLE001
                log.warning("trade.cash_tx.failed", trade_id=trade_dict.get("id"), error=str(exc))
                trade_dict["cash_tx_created"] = False

        return trade_dict

    async def list_trades(
        self,
        user_id: str,
        ticker: str | None = None,
        asset_class: str | None = None,
        account_id: str | None = None,
        portfolio_id: str | None = None,
    ) -> list[dict]:
        async with get_session() as s:
            q = select(TradeModel).where(TradeModel.user_id == user_id)
            if ticker:
                q = q.where(TradeModel.ticker == ticker.upper())
            if asset_class:
                q = q.where(TradeModel.asset_class == asset_class)
            if account_id:
                q = q.where(TradeModel.investment_account_id == account_id)
            if portfolio_id:
                q = q.where(TradeModel.portfolio_id == portfolio_id)
            q = q.order_by(TradeModel.trade_date.desc())
            rows = (await s.execute(q)).scalars().all()
            return [_model_to_dict(r) for r in rows]

    async def delete_trade(self, trade_id: str, user_id: str) -> bool:
        async with get_session() as s:
            q = select(TradeModel).where(TradeModel.id == trade_id, TradeModel.user_id == user_id)
            m = (await s.execute(q)).scalar_one_or_none()
            if not m:
                return False
            await s.delete(m)
            await s.commit()

        # Feature C: cancela tx(s) vinculada(s) a este trade (reverte cash_balance se settled)
        async with get_session() as s2:
            txs = (await s2.execute(
                select(AccountTransactionModel).where(
                    AccountTransactionModel.related_type == "trade",
                    AccountTransactionModel.related_id == trade_id,
                    AccountTransactionModel.user_id == user_id,
                    AccountTransactionModel.status != "cancelled",
                )
            )).scalars().all()
            for tx in txs:
                await self.cancel_transaction(tx.id, user_id)
        return True

    async def get_positions_summary(
        self, user_id: str, asset_class: str | None = None, portfolio_id: str | None = None
    ) -> list[dict]:
        """Calcula posição consolidada (preço médio) por ticker."""
        trades = await self.list_trades(user_id, asset_class=asset_class, portfolio_id=portfolio_id)
        from collections import defaultdict

        by_ticker: dict[str, list] = defaultdict(list)
        for t in trades:
            by_ticker[t["ticker"]].append(t)
        result = []
        for ticker, tlist in sorted(by_ticker.items()):
            total_qty = Decimal("0")
            total_cost = Decimal("0")
            for t in sorted(tlist, key=lambda x: x["trade_date"]):
                qty = Decimal(str(t["quantity"]))
                cost = Decimal(str(t["total_cost"]))
                op = t["operation"]
                if op == "buy":
                    total_qty += qty
                    total_cost += cost
                elif op == "sell" and total_qty > 0:
                    avg = total_cost / total_qty
                    total_qty -= qty
                    total_cost = avg * total_qty
                elif op in ("split", "bonus"):
                    total_qty += qty
            if total_qty > 0:
                avg_price = float(total_cost / total_qty)
                result.append(
                    {
                        "ticker": ticker,
                        "asset_class": tlist[0]["asset_class"],
                        "quantity": float(total_qty),
                        "average_price": round(avg_price, 6),
                        "total_invested": float(total_cost),
                        "trade_count": len(tlist),
                    }
                )
        return result

    # ── Crypto ────────────────────────────────────────────────────────────

    async def upsert_crypto(self, data: dict) -> dict:
        data.setdefault("id", str(uuid.uuid4()))
        async with get_session() as s:
            q = select(CryptoHoldingModel).where(
                CryptoHoldingModel.user_id == data["user_id"],
                CryptoHoldingModel.symbol == data["symbol"].upper(),
                CryptoHoldingModel.investment_account_id == data.get("investment_account_id"),
            )
            m = (await s.execute(q)).scalar_one_or_none()
            old_qty = Decimal(str(m.quantity)) if m else Decimal("0")
            if m:
                for k, v in data.items():
                    if hasattr(m, k) and k not in ("id", "user_id", "created_at"):
                        setattr(m, k, v)
            else:
                data["symbol"] = data["symbol"].upper()
                m = CryptoHoldingModel(
                    **{
                        k: v
                        for k, v in data.items()
                        if k in CryptoHoldingModel.__table__.columns.keys()
                    }
                )
                s.add(m)
            await s.commit()
            await s.refresh(m)
            holding = _model_to_dict(m)

        # Feature C4: hook cash transaction D+0 (cripto e OTC, liquidacao imediata).
        acc_id = data.get("investment_account_id")
        if acc_id:
            try:
                new_qty = Decimal(str(holding.get("quantity", 0)))
                delta_qty = new_qty - old_qty
                price = Decimal(str(holding.get("average_price_brl") or 0))
                if delta_qty != Decimal("0") and price > 0:
                    total = (delta_qty * price).quantize(Decimal("0.01"))
                    tx_type = "crypto_buy" if delta_qty > 0 else "crypto_sell"
                    amount = -total if delta_qty > 0 else abs(total)
                    today = date.today()
                    symbol = (holding.get("symbol") or data.get("symbol") or "?").upper()
                    await self.create_transaction(
                        user_id=str(data["user_id"]),
                        account_id=acc_id,
                        tx_type=tx_type,
                        amount=amount,
                        reference_date=today,
                        settlement_date=today,
                        status="settled",
                        related_type="crypto",
                        related_id=holding.get("id"),
                        note=f"{'Aporte' if delta_qty > 0 else 'Redução'} {symbol} {delta_qty:+}",
                    )
                    holding["cash_tx_created"] = True
            except Exception as exc:  # noqa: BLE001
                log.warning("crypto.cash_tx.failed", holding_id=holding.get("id"), error=str(exc))

        return holding

    async def list_crypto(self, user_id: str, portfolio_id: str | None = None) -> list[dict]:
        async with get_session() as s:
            q = select(CryptoHoldingModel).where(CryptoHoldingModel.user_id == user_id)
            if portfolio_id:
                q = q.where(CryptoHoldingModel.portfolio_id == portfolio_id)
            q = q.order_by(CryptoHoldingModel.symbol)
            rows = (await s.execute(q)).scalars().all()
            return [_model_to_dict(r) for r in rows]

    async def delete_crypto(self, crypto_id: str, user_id: str) -> bool:
        # Captura holding ANTES do delete para computar tx (sell = fechar posicao)
        acc_id = None
        symbol = None
        qty = Decimal("0")
        price = Decimal("0")
        async with get_session() as s:
            q = select(CryptoHoldingModel).where(
                CryptoHoldingModel.id == crypto_id, CryptoHoldingModel.user_id == user_id
            )
            m = (await s.execute(q)).scalar_one_or_none()
            if not m:
                return False
            acc_id = m.investment_account_id
            symbol = m.symbol
            qty = Decimal(str(m.quantity))
            price = Decimal(str(m.average_price_brl or 0))
            await s.delete(m)
            await s.commit()

        # Feature C4: crypto_sell settled D+0 se fecha posicao > 0
        if acc_id and qty > 0 and price > 0:
            try:
                total = (qty * price).quantize(Decimal("0.01"))
                today = date.today()
                await self.create_transaction(
                    user_id=user_id,
                    account_id=acc_id,
                    tx_type="crypto_sell",
                    amount=total,
                    reference_date=today,
                    settlement_date=today,
                    status="settled",
                    related_type="crypto",
                    related_id=crypto_id,
                    note=f"Venda total {symbol} x{qty}",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("crypto.delete_cash_tx.failed", crypto_id=crypto_id, error=str(exc))
        return True

    async def redeem_crypto(self, crypto_id: str, user_id: str, qty: float) -> dict | None:
        """Decrementa quantity. Se chegar a zero ou negativo, remove o holding.
        Feature C4: hook cash (crypto_sell settled D+0 = +qty * avg_price_brl)."""
        acc_id = None
        symbol = None
        price = Decimal("0")
        result: dict | None = None
        async with get_session() as s:
            q = select(CryptoHoldingModel).where(
                CryptoHoldingModel.id == crypto_id, CryptoHoldingModel.user_id == user_id
            )
            m = (await s.execute(q)).scalar_one_or_none()
            if not m:
                return None
            acc_id = m.investment_account_id
            symbol = m.symbol
            price = Decimal(str(m.average_price_brl or 0))
            new_qty = float(m.quantity) - qty
            if new_qty <= 0:
                await s.delete(m)
                await s.commit()
                result = {"removed": True, "remaining_quantity": 0}
            else:
                m.quantity = new_qty
                await s.commit()
                await s.refresh(m)
                result = {"removed": False, "remaining_quantity": float(m.quantity)}

        # Cash tx: crypto_sell settled D+0
        if acc_id and price > 0 and qty > 0:
            try:
                total = (Decimal(str(qty)) * price).quantize(Decimal("0.01"))
                today = date.today()
                await self.create_transaction(
                    user_id=user_id,
                    account_id=acc_id,
                    tx_type="crypto_sell",
                    amount=total,
                    reference_date=today,
                    settlement_date=today,
                    status="settled",
                    related_type="crypto",
                    related_id=crypto_id,
                    note=f"Resgate {symbol} x{qty}",
                )
                result["cash_credit"] = float(total)
            except Exception as exc:  # noqa: BLE001
                log.warning("crypto.redeem_cash_tx.failed", crypto_id=crypto_id, error=str(exc))
        return result

    # ── Other Assets ──────────────────────────────────────────────────────

    async def create_other_asset(self, data: dict) -> dict:
        data.setdefault("id", str(uuid.uuid4()))
        async with get_session() as s:
            m = OtherAssetModel(
                **{k: v for k, v in data.items() if k in OtherAssetModel.__table__.columns.keys()}
            )
            s.add(m)
            await s.commit()
            await s.refresh(m)
            return _model_to_dict(m)

    async def list_other_assets(
        self, user_id: str, asset_type: str | None = None, portfolio_id: str | None = None
    ) -> list[dict]:
        async with get_session() as s:
            q = select(OtherAssetModel).where(OtherAssetModel.user_id == user_id)
            if asset_type:
                q = q.where(OtherAssetModel.asset_type == asset_type)
            if portfolio_id:
                q = q.where(OtherAssetModel.portfolio_id == portfolio_id)
            q = q.order_by(OtherAssetModel.name)
            rows = (await s.execute(q)).scalars().all()
            return [_model_to_dict(r) for r in rows]

    async def update_other_asset(self, asset_id: str, user_id: str, data: dict) -> dict | None:
        async with get_session() as s:
            q = select(OtherAssetModel).where(
                OtherAssetModel.id == asset_id, OtherAssetModel.user_id == user_id
            )
            m = (await s.execute(q)).scalar_one_or_none()
            if not m:
                return None
            for k, v in data.items():
                if hasattr(m, k) and k not in ("id", "user_id", "created_at"):
                    setattr(m, k, v)
            await s.commit()
            await s.refresh(m)
            return _model_to_dict(m)

    async def delete_other_asset(self, asset_id: str, user_id: str) -> bool:
        async with get_session() as s:
            q = select(OtherAssetModel).where(
                OtherAssetModel.id == asset_id, OtherAssetModel.user_id == user_id
            )
            m = (await s.execute(q)).scalar_one_or_none()
            if not m:
                return False
            await s.delete(m)
            await s.commit()
            return True

    # ── Master view ───────────────────────────────────────────────────────

    async def list_all_users_summary(self, target_user_id: str | None = None) -> list[dict]:
        """Visão master: totais consolidados por usuário (somente leitura)."""
        async with get_session() as s:
            from sqlalchemy import text

            sql = text("""
                SELECT u.user_id,
                       COUNT(DISTINCT ia.id) as num_accounts,
                       COUNT(DISTINCT t.id) as num_trades,
                       COUNT(DISTINCT ch.id) as num_crypto,
                       COUNT(DISTINCT oa.id) as num_other_assets
                FROM (SELECT DISTINCT user_id FROM investment_accounts) u
                LEFT JOIN investment_accounts ia ON ia.user_id = u.user_id AND ia.is_active
                LEFT JOIN trades t ON t.user_id = u.user_id
                LEFT JOIN crypto_holdings ch ON ch.user_id = u.user_id
                LEFT JOIN other_assets oa ON oa.user_id = u.user_id
                WHERE (:uid IS NULL OR u.user_id = :uid)
                GROUP BY u.user_id
                ORDER BY u.user_id
            """)
            rows = (await s.execute(sql, {"uid": target_user_id})).fetchall()
            return [dict(r._mapping) for r in rows]
