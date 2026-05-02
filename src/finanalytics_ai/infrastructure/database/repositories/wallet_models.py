"""
SQLAlchemy models de carteira/wallet — extraidos de wallet_repo.py em 01/mai/2026.

Models que mapeiam tabelas multi-tenant da hierarquia
User -> InvestmentAccount -> Portfolio -> {trades, crypto_holdings, rf_holdings,
other_assets, account_transactions, etf_metadata}.

Helper _model_to_dict + _SENSITIVE_FIELDS movidos junto pq sao tightly coupled
com os Models (re-imported em wallet_repo.py).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
import uuid

from sqlalchemy import Boolean, Date, DateTime, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from finanalytics_ai.infrastructure.database.connection import Base

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
    dll_account_type: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # 'real' | 'simulator'
    is_dll_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Flag ADMIN-only: libera envio de ordens REAIS para esta conta (C3 24/abr).
    # Default FALSE — conta recem-criada so pode operar simulador ate admin liberar
    # (evita acidente de rodar estrategia em conta real sem autorizacao).
    real_operations_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Saldo cash settled (Feature C, 24/abr). Mantido pelo AccountTransactionService.
    cash_balance: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, default=Decimal("0")
    )
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
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
