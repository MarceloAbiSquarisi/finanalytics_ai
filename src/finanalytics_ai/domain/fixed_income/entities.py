"""
finanalytics_ai.domain.fixed_income.entities
─────────────────────────────────────────────
Entidades e cálculos de renda fixa. Puro: sem I/O.

Tipos cobertos:
  Tesouro Direto  — SELIC, IPCA+, Prefixado
  CDB / LCI / LCA — pós-fixado (% CDI), IPCA+, prefixado
  Debêntures      — IPCA+, CDI+, prefixado
  CRI / CRA       — isentos de IR (pessoa física)

Cálculos:
  Rendimento bruto                — juros compostos/simples
  IR regressivo (tabela RF)       — 22.5% / 20% / 17.5% / 15%
  IOF (primeiros 30 dias)         — tabela regressiva MF
  Rendimento líquido              — bruto - IR - IOF - taxa custódia
  Comparação entre títulos        — por rendimento líquido anualizado
  Fluxo de caixa                  — parcelas periódicas (NTN-B/NTN-F style)

Design decisions:
  Indexadores dinâmicos (CDI, IPCA, SELIC):
    Usamos taxas anuais fornecidas pelo usuário ou buscadas via API.
    Não "hardcodamos" taxas — o domínio recebe como parâmetro.
    Isso respeita o princípio de injeção e facilita testes.

  IR regressivo:
    Aplicado somente sobre o rendimento (ganho), não sobre o principal.
    LCI/LCA/CRI/CRA são isentos de IR para PF — flag `ir_exempt`.

  IOF:
    Incide somente nos primeiros 30 dias. Tabela oficial do MF.
    Zero após 30 dias.

  Custodia Tesouro:
    Taxa anual de 0.20% a.a. (B3) sobre o valor investido.
    Aplicada proporcionalmente ao período.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


# ── Enums ─────────────────────────────────────────────────────────────────────

class BondType(str, Enum):
    TESOURO_SELIC    = "Tesouro SELIC"
    TESOURO_IPCA     = "Tesouro IPCA+"
    TESOURO_PREFIXADO = "Tesouro Prefixado"
    CDB              = "CDB"
    LCI              = "LCI"
    LCA              = "LCA"
    DEBENTURE        = "Debênture"
    CRI              = "CRI"
    CRA              = "CRA"


class Indexer(str, Enum):
    CDI       = "CDI"
    SELIC     = "SELIC"
    IPCA      = "IPCA"
    PREFIXADO = "Prefixado"
    IGPM      = "IGPM"


class PaymentFrequency(str, Enum):
    BULLET     = "Bullet"        # paga tudo no vencimento
    SEMIANNUAL = "Semestral"     # cupons semestrais (NTN-B, NTN-F)
    ANNUAL     = "Anual"
    MONTHLY    = "Mensal"


# ── Tabela IOF (dias 1-30) ────────────────────────────────────────────────────
# Percentual de imposto sobre rendimento por dia
IOF_TABLE: dict[int, float] = {
    1:96, 2:93, 3:90, 4:86, 5:83, 6:80, 7:76, 8:73, 9:70, 10:66,
    11:63, 12:60, 13:56, 14:53, 15:50, 16:46, 17:43, 18:40, 19:36, 20:33,
    21:30, 22:26, 23:23, 24:20, 25:16, 26:13, 27:10, 28:6, 29:3, 30:0,
}

def iof_rate(days: int) -> float:
    """Alíquota de IOF (%) sobre o rendimento para `days` dias."""
    if days <= 0:  return 100.0
    if days >= 30: return 0.0
    return IOF_TABLE.get(days, 0.0)


# ── Tabela IR Regressivo ──────────────────────────────────────────────────────
def ir_rate(days: int) -> float:
    """Alíquota de IR (%) para renda fixa tributada."""
    if days <= 180:  return 22.5
    if days <= 360:  return 20.0
    if days <= 720:  return 17.5
    return 15.0


# ── Taxa de custódia Tesouro ──────────────────────────────────────────────────
TESOURO_CUSTODY_RATE = 0.0020   # 0.20% a.a.
TESOURO_TYPES = {BondType.TESOURO_SELIC, BondType.TESOURO_IPCA, BondType.TESOURO_PREFIXADO}
IR_EXEMPT_TYPES = {BondType.LCI, BondType.LCA, BondType.CRI, BondType.CRA}


# ── Entidade Bond ─────────────────────────────────────────────────────────────

@dataclass
class Bond:
    """
    Representa um título de renda fixa.

    Para títulos pós-fixados:
      rate_annual = spread sobre o indexador (ex: 0.02 = CDI + 2% ou 102% CDI)
      rate_pct_indexer = quando True, rate é % do indexador (ex: 110% CDI)

    Para títulos prefixados:
      rate_annual = taxa fixa anual (ex: 0.1175 = 11.75%)
    """
    bond_id:          str
    name:             str
    bond_type:        BondType
    indexer:          Indexer
    rate_annual:      float          # taxa anual (decimal)
    rate_pct_indexer: bool  = False  # True = % do indexador; False = indexador + spread
    maturity_date:    Optional[date] = None
    issuer:           str  = ""
    min_investment:   float = 0.0
    payment_freq:     PaymentFrequency = PaymentFrequency.BULLET
    ir_exempt:        bool = False
    liquidity:        str  = "No vencimento"   # "Diária", "D+1", etc.
    risk_rating:      str  = ""                # "AAA", "AA+", etc.
    available:        bool = True
    source:           str  = "manual"          # "tesouro_direto" | "manual"

    @property
    def is_tesouro(self) -> bool:
        return self.bond_type in TESOURO_TYPES

    @property
    def days_to_maturity(self) -> Optional[int]:
        if self.maturity_date is None:
            return None
        delta = self.maturity_date - date.today()
        return max(delta.days, 0)

    @property
    def years_to_maturity(self) -> Optional[float]:
        d = self.days_to_maturity
        return d / 365.0 if d is not None else None


# ── Resultado de cálculo ──────────────────────────────────────────────────────

@dataclass
class YieldResult:
    bond_id:              str
    bond_name:            str
    bond_type:            str
    indexer:              str
    principal:            float
    days:                 int
    gross_amount:         float    # montante bruto no vencimento
    gross_return_pct:     float    # rendimento bruto %
    iof_amount:           float
    ir_amount:            float
    custody_fee_amount:   float
    net_amount:           float    # montante líquido
    net_return_pct:       float    # rendimento líquido %
    net_annual_return_pct: float   # rendimento líquido anualizado
    effective_rate_annual: float   # taxa efetiva anual pré-IR (decimal)
    ir_rate_pct:          float
    iof_rate_pct:         float
    ir_exempt:            bool


@dataclass
class ComparisonResult:
    bonds:        list[YieldResult]
    best_net_id:  str
    best_net_name: str
    period_days:  int
    principal:    float


@dataclass
class CashFlowItem:
    date:         str
    period:       int
    gross_coupon: float
    ir_amount:    float
    net_coupon:   float
    balance:      float


@dataclass
class CashFlowResult:
    bond_id:      str
    bond_name:    str
    principal:    float
    items:        list[CashFlowItem]
    total_gross:  float
    total_ir:     float
    total_net:    float
    net_return_pct: float


# ── Motor de cálculo ──────────────────────────────────────────────────────────

def _effective_annual_rate(bond: Bond, indexer_rate: float) -> float:
    """
    Calcula a taxa efetiva anual do título dado o indexador atual.

    Para % do indexador (ex: 110% CDI):
        taxa = 110% × CDI = 1.10 × 0.1065

    Para spread (ex: CDI + 2%):
        taxa = CDI + 0.02

    Para prefixado:
        taxa = rate_annual diretamente
    """
    if bond.indexer == Indexer.PREFIXADO:
        return bond.rate_annual
    if bond.rate_pct_indexer:
        return bond.rate_annual * indexer_rate
    return indexer_rate + bond.rate_annual


def calculate_yield(
    bond: Bond,
    principal: float,
    days: int,
    indexer_rate: float = 0.0,    # taxa anual do indexador (decimal)
    inflation_rate: float = 0.0,  # IPCA anual (decimal) — para atualização do principal
) -> YieldResult:
    """
    Calcula rendimento bruto e líquido de um título.

    Para IPCA+: o principal é corrigido pela inflação antes de aplicar a taxa real.
    """
    years = days / 365.0

    # Taxa efetiva anual
    eff_rate = _effective_annual_rate(bond, indexer_rate)

    # Montante bruto
    if bond.indexer == Indexer.IPCA and not bond.rate_pct_indexer:
        # IPCA+: principal corrigido × (1 + taxa_real)^anos
        corrected_principal = principal * (1 + inflation_rate) ** years
        gross_amount = corrected_principal * (1 + bond.rate_annual) ** years
    else:
        gross_amount = principal * (1 + eff_rate) ** years

    gross_gain   = gross_amount - principal
    gross_return = gross_gain / principal * 100

    # IOF (somente primeiros 30 dias)
    iof_pct    = iof_rate(days) / 100
    iof_amount = gross_gain * iof_pct

    # IR
    is_exempt = bond.ir_exempt or bond.bond_type in IR_EXEMPT_TYPES
    ir_pct    = 0.0
    if not is_exempt:
        ir_pct    = ir_rate(days) / 100
        ir_amount = (gross_gain - iof_amount) * ir_pct
    else:
        ir_amount = 0.0

    # Taxa de custódia Tesouro
    custody = 0.0
    if bond.is_tesouro:
        # 0.20% a.a. sobre valor médio investido
        custody = principal * TESOURO_CUSTODY_RATE * years

    net_amount    = gross_amount - iof_amount - ir_amount - custody
    net_gain      = net_amount - principal
    net_return    = net_gain / principal * 100
    net_annual    = ((1 + net_gain / principal) ** (1 / years) - 1) * 100 if years > 0 else 0.0

    return YieldResult(
        bond_id              = bond.bond_id,
        bond_name            = bond.name,
        bond_type            = bond.bond_type.value,
        indexer              = bond.indexer.value,
        principal            = principal,
        days                 = days,
        gross_amount         = round(gross_amount, 2),
        gross_return_pct     = round(gross_return, 4),
        iof_amount           = round(iof_amount, 2),
        ir_amount            = round(ir_amount, 2),
        custody_fee_amount   = round(custody, 2),
        net_amount           = round(net_amount, 2),
        net_return_pct       = round(net_return, 4),
        net_annual_return_pct = round(net_annual, 4),
        effective_rate_annual = round(eff_rate * 100, 4),
        ir_rate_pct          = round(ir_pct * 100, 2),
        iof_rate_pct         = round(iof_pct * 100, 2),
        ir_exempt            = is_exempt,
    )


def compare_bonds(
    bonds: list[Bond],
    principal: float,
    days: int,
    cdi_rate: float = 0.0,
    selic_rate: float = 0.0,
    ipca_rate: float = 0.0,
    igpm_rate: float = 0.0,
) -> ComparisonResult:
    """Compara lista de títulos pelo rendimento líquido anualizado."""
    results: list[YieldResult] = []
    rate_map = {
        Indexer.CDI:  cdi_rate,
        Indexer.SELIC: selic_rate,
        Indexer.IPCA:  ipca_rate,
        Indexer.IGPM:  igpm_rate,
        Indexer.PREFIXADO: 0.0,
    }
    for bond in bonds:
        idx_rate = rate_map.get(bond.indexer, 0.0)
        yr = calculate_yield(bond, principal, days, idx_rate, ipca_rate)
        results.append(yr)

    results.sort(key=lambda r: r.net_annual_return_pct, reverse=True)
    best = results[0] if results else None

    return ComparisonResult(
        bonds        = results,
        best_net_id  = best.bond_id if best else "",
        best_net_name = best.bond_name if best else "",
        period_days  = days,
        principal    = principal,
    )


def calculate_cash_flow(
    bond: Bond,
    principal: float,
    indexer_rate: float = 0.0,
    inflation_rate: float = 0.0,
) -> CashFlowResult:
    """
    Gera o fluxo de caixa do título ao longo do tempo.

    Bullet: único pagamento no vencimento.
    Semestral/Anual/Mensal: cupons periódicos + principal no final.
    """
    if bond.days_to_maturity is None or bond.days_to_maturity == 0:
        return CashFlowResult(bond.bond_id, bond.name, principal, [], 0, 0, 0, 0)

    eff_rate = _effective_annual_rate(bond, indexer_rate)
    total_days = bond.days_to_maturity

    # Periodicidade em dias
    freq_days_map = {
        PaymentFrequency.BULLET:     total_days,
        PaymentFrequency.SEMIANNUAL: 182,
        PaymentFrequency.ANNUAL:     365,
        PaymentFrequency.MONTHLY:    30,
    }
    period_days = freq_days_map[bond.payment_freq]

    # Gera períodos
    items: list[CashFlowItem] = []
    balance = principal
    current_day = 0
    period = 0
    total_gross = 0.0
    total_ir    = 0.0
    total_net   = 0.0

    while current_day < total_days:
        period += 1
        next_day = min(current_day + period_days, total_days)
        days_in_period = next_day - current_day
        years_in_period = days_in_period / 365.0

        # Juros do período
        if bond.indexer == Indexer.IPCA and not bond.rate_pct_indexer:
            corr = balance * (1 + inflation_rate) ** years_in_period
            gross_coupon = corr * (1 + bond.rate_annual) ** years_in_period - balance
        else:
            gross_coupon = balance * ((1 + eff_rate) ** years_in_period - 1)

        # No último período, inclui o principal
        is_last = next_day >= total_days
        repayment = balance if is_last else 0.0

        ir_pct    = ir_rate(next_day) / 100
        is_exempt = bond.ir_exempt or bond.bond_type in IR_EXEMPT_TYPES
        ir_coupon = 0.0 if is_exempt else gross_coupon * ir_pct
        net_coupon = gross_coupon - ir_coupon + repayment

        dt = date.today()
        from datetime import timedelta
        payment_date = dt + timedelta(days=next_day)

        items.append(CashFlowItem(
            date         = payment_date.strftime("%Y-%m-%d"),
            period       = period,
            gross_coupon = round(gross_coupon + repayment, 2),
            ir_amount    = round(ir_coupon, 2),
            net_coupon   = round(net_coupon, 2),
            balance      = round(balance, 2),
        ))

        total_gross += gross_coupon
        total_ir    += ir_coupon
        total_net   += gross_coupon - ir_coupon

        if not is_last:
            balance = balance  # cupons sem amortização
        current_day = next_day

    net_return = total_net / principal * 100

    return CashFlowResult(
        bond_id     = bond.bond_id,
        bond_name   = bond.name,
        principal   = principal,
        items       = items,
        total_gross = round(total_gross, 2),
        total_ir    = round(total_ir, 2),
        total_net   = round(total_net, 2),
        net_return_pct = round(net_return, 4),
    )


def goal_investment(
    target_amount: float,
    bond: Bond,
    days: int,
    indexer_rate: float = 0.0,
    inflation_rate: float = 0.0,
) -> float:
    """
    Calcula quanto investir hoje para atingir `target_amount` líquido.
    Busca binária no principal.
    """
    lo, hi = 1.0, target_amount * 2
    for _ in range(60):
        mid = (lo + hi) / 2
        result = calculate_yield(bond, mid, days, indexer_rate, inflation_rate)
        if result.net_amount < target_amount:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 2)
