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

from typing import Any

import structlog

from finanalytics_ai.domain.fixed_income.entities import (
    Bond,
    BondType,
    Indexer,
    PaymentFrequency,
    YieldResult,
    calculate_cash_flow,
    calculate_yield,
    compare_bonds,
    goal_investment,
)
from finanalytics_ai.infrastructure.adapters.tesouro_client import (
    TesouroDiretoClient,
    get_tesouro_client,
)

logger = structlog.get_logger(__name__)

# Taxas de referência padrão — substituíveis via parâmetro
DEFAULT_CDI = 0.1065
DEFAULT_SELIC = 0.1065
DEFAULT_IPCA = 0.0483
DEFAULT_IGPM = 0.0620

# Catálogo manual de títulos CDB/LCI/LCA/Debêntures/CRI/CRA representativos
MANUAL_CATALOG: list[Bond] = [
    # ── CDB ────────────────────────────────────────────────────────────────
    Bond(
        "cdb_itau_120cdi",
        "CDB Itaú 120% CDI",
        BondType.CDB,
        Indexer.CDI,
        1.20,
        rate_pct_indexer=True,
        issuer="Itaú",
        min_investment=1000,
        liquidity="No vencimento",
        ir_exempt=False,
    ),
    Bond(
        "cdb_nubank_100cdi",
        "CDB Nubank 100% CDI",
        BondType.CDB,
        Indexer.CDI,
        1.00,
        rate_pct_indexer=True,
        issuer="Nubank",
        min_investment=1,
        liquidity="Diária",
        ir_exempt=False,
    ),
    Bond(
        "cdb_xp_115cdi",
        "CDB XP 115% CDI",
        BondType.CDB,
        Indexer.CDI,
        1.15,
        rate_pct_indexer=True,
        issuer="XP Investimentos",
        min_investment=1000,
        liquidity="No vencimento",
        ir_exempt=False,
    ),
    Bond(
        "cdb_inter_ipca",
        "CDB Inter IPCA+6%",
        BondType.CDB,
        Indexer.IPCA,
        0.06,
        rate_pct_indexer=False,
        issuer="Banco Inter",
        min_investment=500,
        liquidity="No vencimento",
        ir_exempt=False,
    ),
    Bond(
        "cdb_btg_prefixado",
        "CDB BTG Prefixado 12%",
        BondType.CDB,
        Indexer.PREFIXADO,
        0.12,
        issuer="BTG Pactual",
        min_investment=1000,
        liquidity="No vencimento",
        ir_exempt=False,
    ),
    # ── LCI ────────────────────────────────────────────────────────────────
    Bond(
        "lci_bradesco_90cdi",
        "LCI Bradesco 90% CDI",
        BondType.LCI,
        Indexer.CDI,
        0.90,
        rate_pct_indexer=True,
        issuer="Bradesco",
        min_investment=5000,
        liquidity="No vencimento (90 dias)",
        ir_exempt=True,
    ),
    Bond(
        "lci_btg_95cdi",
        "LCI BTG 95% CDI",
        BondType.LCI,
        Indexer.CDI,
        0.95,
        rate_pct_indexer=True,
        issuer="BTG Pactual",
        min_investment=1000,
        liquidity="No vencimento",
        ir_exempt=True,
    ),
    Bond(
        "lci_xp_ipca",
        "LCI XP IPCA+4%",
        BondType.LCI,
        Indexer.IPCA,
        0.04,
        issuer="XP Investimentos",
        min_investment=5000,
        liquidity="No vencimento",
        ir_exempt=True,
    ),
    # ── LCA ────────────────────────────────────────────────────────────────
    Bond(
        "lca_bb_88cdi",
        "LCA Banco do Brasil 88% CDI",
        BondType.LCA,
        Indexer.CDI,
        0.88,
        rate_pct_indexer=True,
        issuer="Banco do Brasil",
        min_investment=5000,
        liquidity="No vencimento (90 dias)",
        ir_exempt=True,
    ),
    Bond(
        "lca_caixa_92cdi",
        "LCA Caixa 92% CDI",
        BondType.LCA,
        Indexer.CDI,
        0.92,
        rate_pct_indexer=True,
        issuer="Caixa Econômica",
        min_investment=1000,
        liquidity="No vencimento",
        ir_exempt=True,
    ),
    # ── Debêntures ──────────────────────────────────────────────────────────
    Bond(
        "deb_infraestrutura_ipca",
        "Debênture Infraestrutura IPCA+7%",
        BondType.DEBENTURE,
        Indexer.IPCA,
        0.07,
        issuer="Petrobras",
        min_investment=1000,
        liquidity="Mercado secundário",
        ir_exempt=True,
        payment_freq=PaymentFrequency.SEMIANNUAL,
    ),
    Bond(
        "deb_corporativa_cdi",
        "Debênture Corporativa CDI+2%",
        BondType.DEBENTURE,
        Indexer.CDI,
        0.02,
        issuer="Vale S.A.",
        min_investment=1000,
        liquidity="Mercado secundário",
        ir_exempt=False,
        payment_freq=PaymentFrequency.SEMIANNUAL,
    ),
    # ── CRI ─────────────────────────────────────────────────────────────────
    Bond(
        "cri_cyrela_ipca",
        "CRI Cyrela IPCA+6.5%",
        BondType.CRI,
        Indexer.IPCA,
        0.065,
        issuer="Cyrela",
        min_investment=1000,
        liquidity="Mercado secundário",
        ir_exempt=True,
    ),
    Bond(
        "cri_mrl_cdi",
        "CRI MRL CDI+2.5%",
        BondType.CRI,
        Indexer.CDI,
        0.025,
        issuer="MRL Engenharia",
        min_investment=1000,
        liquidity="Mercado secundário",
        ir_exempt=True,
    ),
    # ── CRA ─────────────────────────────────────────────────────────────────
    Bond(
        "cra_brf_ipca",
        "CRA BRF IPCA+5.8%",
        BondType.CRA,
        Indexer.IPCA,
        0.058,
        issuer="BRF",
        min_investment=1000,
        liquidity="Mercado secundário",
        ir_exempt=True,
    ),
    Bond(
        "cra_jbs_cdi",
        "CRA JBS CDI+1.8%",
        BondType.CRA,
        Indexer.CDI,
        0.018,
        issuer="JBS",
        min_investment=1000,
        liquidity="Mercado secundário",
        ir_exempt=True,
    ),
]


class FixedIncomeService:
    def __init__(self, tesouro_client: TesouroDiretoClient) -> None:
        self._tesouro = tesouro_client

    async def list_bonds(
        self,
        bond_types: list[str] | None = None,
        indexers: list[str] | None = None,
        issuer: str | None = None,
        min_days: int | None = None,
        max_days: int | None = None,
    ) -> list[dict[str, Any]]:
        """Lista títulos com filtros opcionais."""
        td_bonds = await self._tesouro.fetch_bonds()
        all_bonds = td_bonds + MANUAL_CATALOG

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
            all_bonds = [b for b in all_bonds if b.days_to_maturity is None or b.days_to_maturity >= min_days]
        if max_days is not None:
            all_bonds = [b for b in all_bonds if b.days_to_maturity is None or b.days_to_maturity <= max_days]

        return [_bond_to_dict(b) for b in all_bonds]

    async def calculate(
        self,
        bond_id: str,
        principal: float,
        days: int | None = None,
        cdi_rate: float = DEFAULT_CDI,
        ipca_rate: float = DEFAULT_IPCA,
        selic_rate: float = DEFAULT_SELIC,
    ) -> dict[str, Any]:
        """Calcula rendimento de um título específico."""
        bond = await self._find_bond(bond_id)
        if bond is None:
            raise ValueError(f"Título não encontrado: {bond_id}")

        calc_days = days or bond.days_to_maturity or 365
        idx_rate = _indexer_rate(bond.indexer, cdi_rate, selic_rate, ipca_rate)
        result = calculate_yield(bond, principal, calc_days, idx_rate, ipca_rate)
        return _yield_to_dict(result)

    async def compare(
        self,
        bond_ids: list[str],
        principal: float,
        days: int,
        cdi_rate: float = DEFAULT_CDI,
        ipca_rate: float = DEFAULT_IPCA,
        selic_rate: float = DEFAULT_SELIC,
        igpm_rate: float = DEFAULT_IGPM,
    ) -> dict[str, Any]:
        """Compara múltiplos títulos pelo rendimento líquido."""
        all_bonds = await self._all_bonds()
        bond_map = {b.bond_id: b for b in all_bonds}
        selected = [bond_map[bid] for bid in bond_ids if bid in bond_map]

        if not selected:
            raise ValueError("Nenhum título válido para comparar.")

        result = compare_bonds(selected, principal, days, cdi_rate, selic_rate, ipca_rate, igpm_rate)
        return {
            "principal": result.principal,
            "period_days": result.period_days,
            "best": result.best_net_name,
            "bonds": [_yield_to_dict(y) for y in result.bonds],
        }

    async def cash_flow(
        self,
        bond_id: str,
        principal: float,
        cdi_rate: float = DEFAULT_CDI,
        ipca_rate: float = DEFAULT_IPCA,
        selic_rate: float = DEFAULT_SELIC,
    ) -> dict[str, Any]:
        """Retorna fluxo de caixa do título."""
        bond = await self._find_bond(bond_id)
        if bond is None:
            raise ValueError(f"Título não encontrado: {bond_id}")

        idx_rate = _indexer_rate(bond.indexer, cdi_rate, selic_rate, ipca_rate)
        result = calculate_cash_flow(bond, principal, idx_rate, ipca_rate)
        return {
            "bond_id": result.bond_id,
            "bond_name": result.bond_name,
            "principal": result.principal,
            "total_gross": result.total_gross,
            "total_ir": result.total_ir,
            "total_net": result.total_net,
            "net_return_pct": result.net_return_pct,
            "items": [
                {
                    "date": i.date,
                    "period": i.period,
                    "gross_coupon": i.gross_coupon,
                    "ir_amount": i.ir_amount,
                    "net_coupon": i.net_coupon,
                    "balance": i.balance,
                }
                for i in result.items
            ],
        }

    async def goal_calc(
        self,
        bond_id: str,
        target_amount: float,
        days: int,
        cdi_rate: float = DEFAULT_CDI,
        ipca_rate: float = DEFAULT_IPCA,
        selic_rate: float = DEFAULT_SELIC,
    ) -> dict[str, Any]:
        """Calcula quanto investir para atingir um valor alvo."""
        bond = await self._find_bond(bond_id)
        if bond is None:
            raise ValueError(f"Título não encontrado: {bond_id}")

        idx_rate = _indexer_rate(bond.indexer, cdi_rate, selic_rate, ipca_rate)
        principal = goal_investment(target_amount, bond, days, idx_rate, ipca_rate)
        result = calculate_yield(bond, principal, days, idx_rate, ipca_rate)
        return {
            "principal_needed": principal,
            "target_amount": target_amount,
            "days": days,
            "bond": _yield_to_dict(result),
        }

    async def rates_reference(self) -> dict[str, float]:
        return {
            "cdi": DEFAULT_CDI * 100,
            "selic": DEFAULT_SELIC * 100,
            "ipca": DEFAULT_IPCA * 100,
            "igpm": DEFAULT_IGPM * 100,
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
        Indexer.CDI: cdi,
        Indexer.SELIC: selic,
        Indexer.IPCA: ipca,
        Indexer.PREFIXADO: 0.0,
    }.get(indexer, 0.0)


def _bond_to_dict(b: Bond) -> dict[str, Any]:
    return {
        "bond_id": b.bond_id,
        "name": b.name,
        "bond_type": b.bond_type.value,
        "indexer": b.indexer.value,
        "rate_annual": round(b.rate_annual * 100, 4),
        "rate_pct_indexer": b.rate_pct_indexer,
        "maturity_date": b.maturity_date.isoformat() if b.maturity_date else None,
        "days_to_maturity": b.days_to_maturity,
        "issuer": b.issuer,
        "min_investment": b.min_investment,
        "payment_freq": b.payment_freq.value,
        "ir_exempt": b.ir_exempt,
        "liquidity": b.liquidity,
        "risk_rating": b.risk_rating,
        "source": b.source,
    }


def _yield_to_dict(y: YieldResult) -> dict[str, Any]:
    return {
        "bond_id": y.bond_id,
        "bond_name": y.bond_name,
        "bond_type": y.bond_type,
        "indexer": y.indexer,
        "principal": y.principal,
        "days": y.days,
        "gross_amount": y.gross_amount,
        "gross_return_pct": y.gross_return_pct,
        "iof_amount": y.iof_amount,
        "ir_amount": y.ir_amount,
        "custody_fee_amount": y.custody_fee_amount,
        "net_amount": y.net_amount,
        "net_return_pct": y.net_return_pct,
        "net_annual_return_pct": y.net_annual_return_pct,
        "effective_rate_annual": y.effective_rate_annual,
        "ir_rate_pct": y.ir_rate_pct,
        "iof_rate_pct": y.iof_rate_pct,
        "ir_exempt": y.ir_exempt,
    }


# ── Yield Curve + Stress Test ─────────────────────────────────────────────────
# Métodos adicionados na Sprint 28b (Curva de Juros + Stress Test)

from finanalytics_ai.domain.fixed_income.yield_curve import (
    STANDARD_SCENARIOS,
    StressScenario,
    YieldCurve,
)
from finanalytics_ai.infrastructure.adapters.anbima_client import get_anbima_client


async def get_yield_curve_with_stress(
    selic: float = DEFAULT_SELIC,
    cdi: float = DEFAULT_CDI,
    ipca: float = DEFAULT_IPCA,
) -> dict[str, Any]:
    """
    Retorna curva DI Futuro + análise contextual.
    Usa ANBIMA real com fallback sintético.
    """
    client = get_anbima_client()
    curve = await client.get_yield_curve(selic, cdi, ipca)

    points_out = [
        {
            "maturity_days": p.maturity_days,
            "maturity_years": p.maturity_years,
            "maturity_date": p.maturity_date.isoformat() if p.maturity_date else None,
            "rate_annual": p.rate_annual,
            "rate_pct": p.rate_pct,
            "contract": p.contract,
            "source": p.source,
        }
        for p in curve.points
    ]

    return {
        "reference_date": curve.reference_date.isoformat(),
        "source": curve.source,
        "selic": round(curve.selic * 100, 4),
        "cdi": round(curve.cdi * 100, 4),
        "ipca": round(curve.ipca * 100, 4),
        "short_rate_pct": round(curve.short_rate * 100, 4),
        "long_rate_pct": round(curve.long_rate * 100, 4),
        "slope_pp": curve.slope,
        "is_inverted": curve.is_inverted,
        "shape": "invertida" if curve.is_inverted else ("plana" if abs(curve.slope) < 0.5 else "normal"),
        "points": points_out,
        "context": {
            "interpretation": _curve_interpretation(curve),
            "positioning": _curve_positioning(curve),
        },
    }


async def run_stress_test(
    bond_ids: list[str],
    principal: float,
    days: int,
    scenarios: list[StressScenario] | None = None,
    base_selic: float = DEFAULT_SELIC,
    base_cdi: float = DEFAULT_CDI,
    base_ipca: float = DEFAULT_IPCA,
    base_igpm: float = DEFAULT_IGPM,
) -> list[dict[str, Any]]:
    """
    Executa stress test para os bonds selecionados × cenários.

    Para cada bond × cenário, calcula o yield líquido com as taxas estressadas
    e retorna a comparação completa.
    """
    _svc = FixedIncomeService(get_tesouro_client())
    if scenarios is None:
        scenarios = STANDARD_SCENARIOS

    comparisons: list[dict[str, Any]] = []

    for bond_id in bond_ids:
        bond = await _svc._find_bond(bond_id)
        if bond is None:
            continue

        results: list[dict[str, Any]] = []

        for scenario in scenarios:
            stressed = scenario.apply_to_rates(base_selic, base_cdi, base_ipca, base_igpm)
            idx_rate = _indexer_rate(bond.indexer, stressed["cdi"], stressed["selic"], stressed["ipca"])
            yr = calculate_yield(
                bond=bond,
                principal=principal,
                days=days,
                indexer_rate=idx_rate,
                inflation_rate=stressed["ipca"],
            )
            results.append(
                {
                    "scenario_name": scenario.name,
                    "color": scenario.color,
                    "selic_applied": round(stressed["selic"] * 100, 4),
                    "cdi_applied": round(stressed["cdi"] * 100, 4),
                    "ipca_applied": round(stressed["ipca"] * 100, 4),
                    "net_return_pct": yr.net_return_pct,
                    "gross_return_pct": yr.gross_return_pct,
                    "net_amount": yr.net_amount,
                    "ir_amount": yr.ir_amount,
                    "iof_amount": yr.iof_amount,
                    "effective_rate_annual": yr.effective_rate_annual,
                    "net_annual_return_pct": yr.net_annual_return_pct,
                }
            )

        # Calcula drawdown máximo vs cenário base
        base_r = next((r for r in results if r["scenario_name"] == "Base"), results[0])
        worst_r = min(results, key=lambda r: r["net_return_pct"])
        best_r = max(results, key=lambda r: r["net_return_pct"])

        comparisons.append(
            {
                "bond_id": bond.bond_id,
                "bond_name": bond.name,
                "bond_type": bond.bond_type.value,
                "indexer": bond.indexer.value,
                "ir_exempt": bond.ir_exempt,
                "principal": principal,
                "days": days,
                "base_net_return_pct": base_r["net_return_pct"],
                "max_drawdown_pp": round(base_r["net_return_pct"] - worst_r["net_return_pct"], 4),
                "max_upside_pp": round(best_r["net_return_pct"] - base_r["net_return_pct"], 4),
                "worst_scenario": worst_r["scenario_name"],
                "best_scenario": best_r["scenario_name"],
                "results": results,
            }
        )

    return comparisons


def _curve_interpretation(curve: YieldCurve) -> str:
    if curve.is_inverted:
        return (
            "Curva invertida: taxas longas abaixo das curtas. "
            "O mercado precifica queda da SELIC no médio prazo. "
            "Favorável para prefixados longos."
        )
    if abs(curve.slope) < 0.5:
        return (
            "Curva plana: mercado sem consenso claro sobre juros futuros. "
            "Incerteza elevada. Prefira liquidez ou pós-fixados."
        )
    return (
        "Curva normal: taxas longas acima das curtas. "
        "Prêmio por prazo positivo. "
        "CDI/SELIC remunera bem no curto prazo sem risco de duration."
    )


def _curve_positioning(curve: YieldCurve) -> list[dict[str, str]]:
    tips: list[dict[str, str]] = []
    if curve.is_inverted:
        tips.append(
            {
                "type": "opportunity",
                "msg": "Prefixados longos estão atrativos — trave a taxa antes da queda da SELIC.",
            }
        )
        tips.append({"type": "risk", "msg": "CDBs pós-fixados perdem com a queda do CDI. Avalie duration."})
    else:
        tips.append(
            {
                "type": "neutral",
                "msg": "Pós-fixados (CDI/SELIC) remuneram bem sem risco de marcação a mercado.",
            }
        )
    if curve.selic > 0.12:
        tips.append(
            {
                "type": "opportunity",
                "msg": f"SELIC em {curve.selic * 100:.2f}%: LCI/LCA isentas superam muitos fundos.",
            }
        )
    if curve.long_rate > curve.selic + 0.02:
        tips.append(
            {
                "type": "opportunity",
                "msg": f"Prêmio longo de {curve.slope:.1f} p.p.: Tesouro IPCA+ longo vale análise.",
            }
        )
    return tips
