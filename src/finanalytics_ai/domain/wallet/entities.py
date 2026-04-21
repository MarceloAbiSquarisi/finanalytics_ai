"""
domain/wallet/entities.py — entidades de domínio para carteira multi-usuário
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
import uuid

# ── Enums ──────────────────────────────────────────────────────────────────


class AssetClass(StrEnum):
    STOCK = "stock"
    ETF = "etf"
    CRYPTO = "crypto"
    FII = "fii"
    BDR = "bdr"
    OTHER = "other"


class TradeOperation(StrEnum):
    BUY = "buy"
    SELL = "sell"
    SPLIT = "split"
    BONUS = "bonus"


class AccountType(StrEnum):
    CORRETORA = "corretora"
    BANCO = "banco"
    EXCHANGE = "exchange"  # cripto
    PREVIDENCIA = "previdencia"
    OUTRO = "outro"


class OtherAssetType(StrEnum):
    IMOVEL = "imovel"
    PREVIDENCIA = "previdencia"
    COE = "coe"
    DEBENTURE = "debenture"
    OUTRO = "outro"


# ── Investment Account ─────────────────────────────────────────────────────


@dataclass
class InvestmentAccount:
    id: str
    user_id: str
    institution_name: str
    country: str = "BRA"
    currency: str = "BRL"
    account_type: AccountType = AccountType.CORRETORA
    institution_code: str | None = None
    agency: str | None = None
    account_number: str | None = None
    is_active: bool = True
    note: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @staticmethod
    def new(
        user_id: str,
        institution_name: str,
        country: str = "BRA",
        currency: str = "BRL",
        account_type: AccountType = AccountType.CORRETORA,
        institution_code: str | None = None,
        agency: str | None = None,
        account_number: str | None = None,
        note: str | None = None,
    ) -> InvestmentAccount:
        return InvestmentAccount(
            id=str(uuid.uuid4()),
            user_id=user_id,
            institution_name=institution_name,
            country=country,
            currency=currency,
            account_type=account_type,
            institution_code=institution_code,
            agency=agency,
            account_number=account_number,
            note=note,
        )


# ── Trade ─────────────────────────────────────────────────────────────────


@dataclass
class Trade:
    id: str
    user_id: str
    ticker: str
    asset_class: AssetClass
    operation: TradeOperation
    quantity: Decimal
    unit_price: Decimal
    total_cost: Decimal
    trade_date: date
    fees: Decimal = Decimal("0")
    currency: str = "BRL"
    investment_account_id: str | None = None
    portfolio_id: str | None = None
    note: str | None = None
    created_at: datetime | None = None

    @staticmethod
    def new(
        user_id: str,
        ticker: str,
        asset_class: AssetClass,
        operation: TradeOperation,
        quantity: Decimal,
        unit_price: Decimal,
        trade_date: date,
        fees: Decimal = Decimal("0"),
        currency: str = "BRL",
        investment_account_id: str | None = None,
        portfolio_id: str | None = None,
        note: str | None = None,
    ) -> Trade:
        total_cost = quantity * unit_price + fees
        return Trade(
            id=str(uuid.uuid4()),
            user_id=user_id,
            ticker=ticker,
            asset_class=asset_class,
            operation=operation,
            quantity=quantity,
            unit_price=unit_price,
            total_cost=total_cost,
            fees=fees,
            trade_date=trade_date,
            currency=currency,
            investment_account_id=investment_account_id,
            portfolio_id=portfolio_id,
            note=note,
        )

    @staticmethod
    def calc_average_price(trades: list[Trade]) -> Decimal:
        """
        Calcula preço médio ponderado considerando compras e vendas.
        Venda reduz posição; split/bonus ajusta quantidade sem custo.
        """
        total_qty = Decimal("0")
        total_cost = Decimal("0")
        for t in sorted(trades, key=lambda x: x.trade_date):
            if t.operation == TradeOperation.BUY:
                total_qty += t.quantity
                total_cost += t.total_cost
            elif t.operation == TradeOperation.SELL:
                if total_qty > 0:
                    avg = total_cost / total_qty
                    total_qty -= t.quantity
                    total_cost = avg * total_qty
            elif t.operation == TradeOperation.SPLIT:
                total_qty += t.quantity  # bonus de split
            elif t.operation == TradeOperation.BONUS:
                total_qty += t.quantity  # bonificação sem custo
        if total_qty <= 0:
            return Decimal("0")
        return total_cost / total_qty


# ── CryptoHolding ─────────────────────────────────────────────────────────


@dataclass
class CryptoHolding:
    id: str
    user_id: str
    symbol: str
    quantity: Decimal
    average_price_brl: Decimal
    average_price_usd: Decimal | None = None
    investment_account_id: str | None = None
    portfolio_id: str | None = None
    exchange: str | None = None
    wallet_address: str | None = None
    note: str | None = None
    updated_at: datetime | None = None

    @staticmethod
    def new(
        user_id: str,
        symbol: str,
        quantity: Decimal,
        average_price_brl: Decimal,
        average_price_usd: Decimal | None = None,
        investment_account_id: str | None = None,
        portfolio_id: str | None = None,
        exchange: str | None = None,
        wallet_address: str | None = None,
        note: str | None = None,
    ) -> CryptoHolding:
        return CryptoHolding(
            id=str(uuid.uuid4()),
            user_id=user_id,
            symbol=symbol.upper(),
            quantity=quantity,
            average_price_brl=average_price_brl,
            average_price_usd=average_price_usd,
            investment_account_id=investment_account_id,
            portfolio_id=portfolio_id,
            exchange=exchange,
            wallet_address=wallet_address,
            note=note,
        )


# ── OtherAsset ────────────────────────────────────────────────────────────


@dataclass
class OtherAsset:
    id: str
    user_id: str
    name: str
    asset_type: OtherAssetType
    current_value: Decimal
    currency: str = "BRL"
    invested_value: Decimal | None = None
    acquisition_date: date | None = None
    maturity_date: date | None = None
    ir_exempt: bool = False
    investment_account_id: str | None = None
    portfolio_id: str | None = None
    note: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @staticmethod
    def new(
        user_id: str,
        name: str,
        asset_type: OtherAssetType,
        current_value: Decimal,
        currency: str = "BRL",
        invested_value: Decimal | None = None,
        acquisition_date: date | None = None,
        maturity_date: date | None = None,
        ir_exempt: bool = False,
        investment_account_id: str | None = None,
        portfolio_id: str | None = None,
        note: str | None = None,
    ) -> OtherAsset:
        return OtherAsset(
            id=str(uuid.uuid4()),
            user_id=user_id,
            name=name,
            asset_type=asset_type,
            current_value=current_value,
            currency=currency,
            invested_value=invested_value,
            acquisition_date=acquisition_date,
            maturity_date=maturity_date,
            ir_exempt=ir_exempt,
            investment_account_id=investment_account_id,
            portfolio_id=portfolio_id,
            note=note,
        )

    @property
    def gain(self) -> Decimal | None:
        if self.invested_value and self.current_value:
            return self.current_value - self.invested_value
        return None

    @property
    def gain_pct(self) -> Decimal | None:
        if self.invested_value and self.invested_value > 0 and self.gain is not None:
            return self.gain / self.invested_value * 100
        return None
