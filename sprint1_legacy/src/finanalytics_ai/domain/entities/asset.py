"""
Entidades de Ativos: Asset, StockAsset, FixedIncomeAsset, FundAsset.

Design decision: Herança rasa + dataclasses. Evitamos ORM no domínio —
entidades são POPOs (Plain Old Python Objects). Mapeamento para DB é
responsabilidade da infra.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from finanalytics_ai.domain.value_objects.money import Currency, Money, Ticker


class AssetClass(StrEnum):
    STOCK = "stock"  # Renda Variável — Ações
    STOCK_OPTION = "stock_option"  # Opções
    FII = "fii"  # Fundo Imobiliário
    ETF = "etf"  # ETF
    BDR = "bdr"  # BDR
    FIXED_INCOME = "fixed_income"  # Renda Fixa (LCI, LCA, CDB, Tesouro)
    COMMODITY = "commodity"  # Commodities
    CRYPTO = "crypto"  # Criptoativos
    FUND = "fund"  # Fundos de Investimento


class FixedIncomeType(StrEnum):
    TESOURO_SELIC = "tesouro_selic"
    TESOURO_IPCA = "tesouro_ipca"
    TESOURO_PREFIXADO = "tesouro_prefixado"
    LCI = "lci"
    LCA = "lca"
    CDB = "cdb"
    CRI = "cri"
    CRA = "cra"
    DEBENTURE = "debenture"


@dataclass
class Asset:
    """
    Entidade base de um ativo financeiro.

    Contém apenas dados que pertencem ao ativo em si,
    não à posição do investidor nele.
    """

    ticker: Ticker
    name: str
    asset_class: AssetClass
    currency: Currency = Currency.BRL
    isin: str = ""
    sector: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.ticker.symbol

    def is_variable_income(self) -> bool:
        return self.asset_class in {
            AssetClass.STOCK,
            AssetClass.STOCK_OPTION,
            AssetClass.FII,
            AssetClass.ETF,
            AssetClass.BDR,
        }

    def is_fixed_income(self) -> bool:
        return self.asset_class == AssetClass.FIXED_INCOME


@dataclass
class StockAsset(Asset):
    """Ação listada em bolsa."""

    cnpj: str = ""
    exchange: str = "B3"
    free_float: Decimal = Decimal("0")
    market_cap: Money | None = None

    def __post_init__(self) -> None:
        self.asset_class = AssetClass.STOCK


@dataclass
class FixedIncomeAsset(Asset):
    """Título de renda fixa."""

    fixed_income_type: FixedIncomeType = FixedIncomeType.CDB
    issuer: str = ""
    maturity_date: datetime | None = None
    rate: Decimal = Decimal("0")  # taxa em % ao ano
    index: str = ""  # CDI, IPCA, SELIC, etc.
    minimum_investment: Money = field(default_factory=lambda: Money.of("1000"))
    fgc_covered: bool = False  # Coberto pelo FGC até R$ 250k

    def __post_init__(self) -> None:
        self.asset_class = AssetClass.FIXED_INCOME

    @property
    def is_treasury(self) -> bool:
        return self.fixed_income_type in {
            FixedIncomeType.TESOURO_SELIC,
            FixedIncomeType.TESOURO_IPCA,
            FixedIncomeType.TESOURO_PREFIXADO,
        }
