"""
finanalytics_ai.application.services.fixed_income_service
───────────────────────────────────────────────────────────
Orquestra busca de títulos (Tesouro + manuais) e delegação
de cálculos ao domínio.

Taxas de referência padrão (atualizadas se BACEN API disponível):
  CDI/SELIC: 10.65% a.a. (referência mar/2026 — ajustar no .env)
  IPCA:       4.83% a.a.
  IGPM:       6.20% a.a.
"""
from __future__ import annotations

import uuid
from typing import Any

import structlog

from finanalytics_ai.domain.fixed_income.entities import (
    Bond, BondType, Indexer, PaymentFrequency,
    YieldResult, ComparisonResult, CashFlowResult,
    calculate_yield, compare_bonds, calculate_cash_flow, goal_investment,
)
from finanalytics_ai.infrastructure.adapters.tesouro_client import TesouroDiretoClient

logger = structlog.get_logger(__name__)

# Taxas de referência padrão — substituíveis via parâmetro
DEFAULT_CDI   = 0.1065
DEFAULT_SELIC = 0.1065
DEFAULT_IPCA  = 0.0483
DEFAULT_IGPM  = 0.0620

# Catálogo manual de títulos CDB/LCI/LCA/Debêntures/CRI/CRA representativos
MANUAL_CATALOG: list[Bond] = [
    # ── CDB ────────────────────────────────────────────────────────────────
    Bond("cdb_itau_120cdi", "CDB Itaú 120% CDI", BondType.CDB, Indexer.CDI,
         1.20, rate_pct_indexer=True, issuer="Itaú", min_investment=1000,
         liquidity="No vencimento", ir_exempt=False),
    Bond("cdb_nubank_100cdi", "CDB Nubank 100% CDI", BondType.CDB, Indexer.CDI,
         1.00, rate_pct_indexer=True, issuer="Nubank", min_investment=1,
         liquidity="Diária", ir_exempt=False),
    Bond("cdb_xp_115cdi", "CDB XP 115% CDI", BondType.CDB, Indexer.CDI,
         1.15, rate_pct_indexer=True, issuer="XP Investimentos", min_investment=1000,
         liquidity="No vencimento", ir_exempt=False),
    Bond("cdb_inter_ipca", "CDB Inter IPCA+6%", BondType.CDB, Indexer.IPCA,
         0.06, rate_pct_indexer=False, issuer="Banco Inter", min_investment=500,
         liquidity="No vencimento", ir_exempt=False),
    Bond("cdb_btg_prefixado", "CDB BTG Prefixado 12%", BondType.CDB, Indexer.PREFIXADO,
         0.12, issuer="BTG Pactual", min_investment=1000,
         liquidity="No vencimento", ir_exempt=False),

    # ── LCI ────────────────────────────────────────────────────────────────
    Bond("lci_bradesco_90cdi", "LCI Bradesco 90% CDI", BondType.LCI, Indexer.CDI,
         0.90, rate_pct_indexer=True, issuer="Bradesco", min_investment=5000,
         liquidity="No vencimento (90 dias)", ir_exempt=True),
    Bond("lci_btg_95cdi", "LCI BTG 95% CDI", BondType.LCI, Indexer.CDI,
         0.95, rate_pct_indexer=True, issuer="BTG Pactual", min_investment=1000,
         liquidity="No vencimento", ir_exempt=True),
    Bond("lci_xp_ipca", "LCI XP IPCA+4%", BondType.LCI, Indexer.IPCA,
         0.04, issuer="XP Investimentos", min_investment=5000,
         liquidity="No vencimento", ir_exempt=True),

    # ── LCA ────────────────────────────────────────────────────────────────
    Bond("lca_bb_88cdi", "LCA Banco do Brasil 88% CDI", BondType.LCA, Indexer.CDI,
         0.88, rate_pct_indexer=True, issuer="Banco do Brasil", min_investment=5000,
         liquidity="No vencimento (90 dias)", ir_exempt=True),
    Bond("lca_caixa_92cdi", "LCA Caixa 92% CDI", BondType.LCA, Indexer.CDI,
         0.92, rate_pct_indexer=True, issuer="Caixa Econômica", min_investment=1000,
         liquidity="No vencimento", ir_exempt=True),

    # ── Debêntures ──────────────────────────────────────────────────────────
    Bond("deb_infraestrutura_ipca", "Debênture Infraestrutura IPCA+7%", BondType.DEBENTURE,
         Indexer.IPCA, 0.07, issuer="Petrobras", min_investment=1000,
         liquidity="Mercado secundário", ir_exempt=True,
         payment_freq=PaymentFrequency.SEMIANNUAL),
    Bond("deb_corporativa_cdi", "Debênture Corporativa CDI+2%", BondType.DEBENTURE,
         Indexer.CDI, 0.02, issuer="Vale S.A.", min_investment=1000,
         liquidity="Mercado secundário", ir_exempt=False,
         payment_freq=PaymentFrequency.SEMIANNUAL),

    # ── CRI ─────────────────────────────────────────────────────────────────
    Bond("cri_cyrela_ipca", "CRI Cyrela IPCA+6.5%", BondType.CRI, Indexer.IPCA,
         0.065, issuer="Cyrela", min_investment=1000,
         liquidity="Mercado secundário", ir_exempt=True),
    Bond("cri_mrl_cdi", "CRI MRL CDI+2.5%", BondType.CRI, Indexer.CDI,
         0.025, issuer="MRL Engenharia", min_investment=1000,
         liquidity="Mercado secundário", ir_exempt=True),

    # ── CRA ─────────────────────────────────────────────────────────────────
    Bond("cra_brf_ipca", "CRA BRF IPCA+5.8%", BondType.CRA, Indexer.IPCA,
         0.058, issuer="BRF", min_investment=1000,
         liquidity="Mercado secundário", ir_exempt=True),
    Bond("cra_jbs_cdi", "CRA JBS CDI+1.8%", BondType.CRA, Indexer.CDI,
         0.018, issuer="JBS", min_investment=1000,
         liquidity="Mercado secundário", ir_exempt=True),
]


class FixedIncomeService:
    def __init__(self, tesouro_client: TesouroDiretoClient) -> None:
        self._tesouro = tesouro_client

    async def list_bonds(
        self,
        bond_types: list[str] | None = None,
        indexers:   list[str] | None = None,
        issuer:     str | None = None,
        min_days:   int | None = None,
        max_days:   int | None = None,
    ) -> list[dict[str, Any]]:
        """Lista títulos com filtros opcionais."""
        td_bonds   = await self._tesouro.fetch_bonds()
        all_bonds  = td_bonds + MANUAL_CATALOG

        # Filtros
        if bond_types:
            bt_set = {b.lower() for b in bond_types}
            all_bonds = [b for b in all_bonds if b.bond_type.value.lower() in bt_set]
        if indexers:
            idx_set = {i.lower() for i in indexers}
            all_bonds = [b for b in all_bonds if b.indexer.value.lower() in idx_set]
        if issuer:
            all_bonds = [b for b in all_bonds if issuer.lower() in b.issuer.lower()]
        if min_days is not None:
            all_bonds = [b for b in all_bonds
                         if b.days_to_maturity is None or b.days_to_maturity >= min_days]
        if max_days is not None:
            all_bonds = [b for b in all_bonds
                         if b.days_to_maturity is None or b.days_to_maturity <= max_days]

        return [_bond_to_dict(b) for b in all_bonds]

    async def calculate(
        self,
        bond_id:      str,
        principal:    float,
        days:         int | None = None,
        cdi_rate:     float = DEFAULT_CDI,
        ipca_rate:    float = DEFAULT_IPCA,
        selic_rate:   float = DEFAULT_SELIC,
    ) -> dict[str, Any]:
        """Calcula rendimento de um título específico."""
        bond = await self._find_bond(bond_id)
        if bond is None:
            raise ValueError(f"Título não encontrado: {bond_id}")

        calc_days = days or bond.days_to_maturity or 365
        idx_rate  = _indexer_rate(bond.indexer, cdi_rate, selic_rate, ipca_rate)
        result    = calculate_yield(bond, principal, calc_days, idx_rate, ipca_rate)
        return _yield_to_dict(result)

    async def compare(
        self,
        bond_ids:   list[str],
        principal:  float,
        days:       int,
        cdi_rate:   float = DEFAULT_CDI,
        ipca_rate:  float = DEFAULT_IPCA,
        selic_rate: float = DEFAULT_SELIC,
        igpm_rate:  float = DEFAULT_IGPM,
    ) -> dict[str, Any]:
        """Compara múltiplos títulos pelo rendimento líquido."""
        all_bonds = await self._all_bonds()
        bond_map  = {b.bond_id: b for b in all_bonds}
        selected  = [bond_map[bid] for bid in bond_ids if bid in bond_map]

        if not selected:
            raise ValueError("Nenhum título válido para comparar.")

        result = compare_bonds(selected, principal, days,
                               cdi_rate, selic_rate, ipca_rate, igpm_rate)
        return {
            "principal":    result.principal,
            "period_days":  result.period_days,
            "best":         result.best_net_name,
            "bonds": [_yield_to_dict(y) for y in result.bonds],
        }

    async def cash_flow(
        self,
        bond_id:    str,
        principal:  float,
        cdi_rate:   float = DEFAULT_CDI,
        ipca_rate:  float = DEFAULT_IPCA,
        selic_rate: float = DEFAULT_SELIC,
    ) -> dict[str, Any]:
        """Retorna fluxo de caixa do título."""
        bond = await self._find_bond(bond_id)
        if bond is None:
            raise ValueError(f"Título não encontrado: {bond_id}")

        idx_rate = _indexer_rate(bond.indexer, cdi_rate, selic_rate, ipca_rate)
        result   = calculate_cash_flow(bond, principal, idx_rate, ipca_rate)
        return {
            "bond_id":      result.bond_id,
            "bond_name":    result.bond_name,
            "principal":    result.principal,
            "total_gross":  result.total_gross,
            "total_ir":     result.total_ir,
            "total_net":    result.total_net,
            "net_return_pct": result.net_return_pct,
            "items": [
                {"date": i.date, "period": i.period,
                 "gross_coupon": i.gross_coupon, "ir_amount": i.ir_amount,
                 "net_coupon": i.net_coupon, "balance": i.balance}
                for i in result.items
            ],
        }

    async def goal_calc(
        self,
        bond_id:       str,
        target_amount: float,
        days:          int,
        cdi_rate:      float = DEFAULT_CDI,
        ipca_rate:     float = DEFAULT_IPCA,
        selic_rate:    float = DEFAULT_SELIC,
    ) -> dict[str, Any]:
        """Calcula quanto investir para atingir um valor alvo."""
        bond = await self._find_bond(bond_id)
        if bond is None:
            raise ValueError(f"Título não encontrado: {bond_id}")

        idx_rate  = _indexer_rate(bond.indexer, cdi_rate, selic_rate, ipca_rate)
        principal = goal_investment(target_amount, bond, days, idx_rate, ipca_rate)
        result    = calculate_yield(bond, principal, days, idx_rate, ipca_rate)
        return {
            "principal_needed": principal,
            "target_amount":    target_amount,
            "days":             days,
            "bond":             _yield_to_dict(result),
        }

    async def rates_reference(self) -> dict[str, float]:
        return {
            "cdi":   DEFAULT_CDI * 100,
            "selic": DEFAULT_SELIC * 100,
            "ipca":  DEFAULT_IPCA * 100,
            "igpm":  DEFAULT_IGPM * 100,
        }

    async def _find_bond(self, bond_id: str) -> Bond | None:
        all_b = await self._all_bonds()
        return next((b for b in all_b if b.bond_id == bond_id), None)

    async def _all_bonds(self) -> list[Bond]:
        td = await self._tesouro.fetch_bonds()
        return td + MANUAL_CATALOG


# ── Helpers ───────────────────────────────────────────────────────────────────

def _indexer_rate(indexer: Indexer, cdi: float, selic: float, ipca: float) -> float:
    return {
        Indexer.CDI:  cdi,
        Indexer.SELIC: selic,
        Indexer.IPCA:  ipca,
        Indexer.PREFIXADO: 0.0,
    }.get(indexer, 0.0)


def _bond_to_dict(b: Bond) -> dict[str, Any]:
    return {
        "bond_id":          b.bond_id,
        "name":             b.name,
        "bond_type":        b.bond_type.value,
        "indexer":          b.indexer.value,
        "rate_annual":      round(b.rate_annual * 100, 4),
        "rate_pct_indexer": b.rate_pct_indexer,
        "maturity_date":    b.maturity_date.isoformat() if b.maturity_date else None,
        "days_to_maturity": b.days_to_maturity,
        "issuer":           b.issuer,
        "min_investment":   b.min_investment,
        "payment_freq":     b.payment_freq.value,
        "ir_exempt":        b.ir_exempt,
        "liquidity":        b.liquidity,
        "risk_rating":      b.risk_rating,
        "source":           b.source,
    }


def _yield_to_dict(y: YieldResult) -> dict[str, Any]:
    return {
        "bond_id":              y.bond_id,
        "bond_name":            y.bond_name,
        "bond_type":            y.bond_type,
        "indexer":              y.indexer,
        "principal":            y.principal,
        "days":                 y.days,
        "gross_amount":         y.gross_amount,
        "gross_return_pct":     y.gross_return_pct,
        "iof_amount":           y.iof_amount,
        "ir_amount":            y.ir_amount,
        "custody_fee_amount":   y.custody_fee_amount,
        "net_amount":           y.net_amount,
        "net_return_pct":       y.net_return_pct,
        "net_annual_return_pct": y.net_annual_return_pct,
        "effective_rate_annual": y.effective_rate_annual,
        "ir_rate_pct":          y.ir_rate_pct,
        "iof_rate_pct":         y.iof_rate_pct,
        "ir_exempt":            y.ir_exempt,
    }
