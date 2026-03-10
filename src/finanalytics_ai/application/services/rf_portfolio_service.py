"""
finanalytics_ai.application.services.rf_portfolio_service
───────────────────────────────────────────────────────────
Orquestração de casos de uso da carteira de Renda Fixa.

Responsabilidades:
  - Criar/listar carteiras de RF por usuário
  - Adicionar posições (holdings) com lookup no catálogo de bonds
  - Calcular posição atual de cada holding (rendimento acumulado)
  - Gerar relatório de diversificação da carteira
  - Retornar projeção de vencimentos (maturities timeline)

Design decisions:
  Enriquecimento no serviço, não no domínio:
    O domínio (RFHolding) armazena apenas o que foi contratado.
    O serviço enriquece com cálculos point-in-time (rendimento acumulado,
    valor atual) usando o motor de cálculo existente em entities.py.

  Lookup do bond no catálogo:
    Ao adicionar um holding, buscamos o Bond no catálogo para obter
    issuer, ir_exempt, etc. Se não encontrado, aceitamos dados manuais
    enviados pelo cliente (bond_id = "custom").
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import structlog

from finanalytics_ai.domain.fixed_income.entities import Indexer, calculate_yield

DEFAULT_SELIC: float = 0.1065
DEFAULT_CDI: float = 0.1065
DEFAULT_IPCA: float = 0.0483
from finanalytics_ai.domain.fixed_income.portfolio import (
    DiversificationReport,
    RFHolding,
    RFPortfolio,
)
from finanalytics_ai.infrastructure.adapters.tesouro_client import get_tesouro_client
from finanalytics_ai.infrastructure.database.repositories.rf_repo import RFPortfolioRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


def _indexer_enum(s: str) -> str:
    """Normaliza string de indexador para o valor do Enum."""
    mapping = {
        "cdi": Indexer.CDI.value,
        "selic": Indexer.SELIC.value,
        "ipca": Indexer.IPCA.value,
        "igpm": "IGPM",
        "prefixado": Indexer.PREFIXADO.value,
    }
    return mapping.get(s.lower(), s.upper())


class RFPortfolioService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = RFPortfolioRepository(session)

    # ── Carteiras ──────────────────────────────────────────────────────────────

    async def create_portfolio(self, user_id: str, name: str) -> dict[str, Any]:
        p = await self._repo.create_portfolio(user_id, name)
        return {
            "portfolio_id": p.portfolio_id,
            "user_id": p.user_id,
            "name": p.name,
            "created_at": p.created_at.isoformat() if p.created_at is not None else None,
        }

    async def list_portfolios(self, user_id: str) -> list[dict[str, Any]]:
        portfolios = await self._repo.list_portfolios(user_id)
        return [self._portfolio_summary(p) for p in portfolios]

    async def get_portfolio(
        self,
        portfolio_id: str,
        selic: float = DEFAULT_SELIC,
        cdi: float = DEFAULT_CDI,
        ipca: float = DEFAULT_IPCA,
    ) -> dict[str, Any] | None:
        p = await self._repo.get_portfolio(portfolio_id)
        if p is None:
            return None
        return self._portfolio_detail(p, selic, cdi, ipca)

    async def delete_portfolio(self, portfolio_id: str) -> None:
        await self._repo.delete_portfolio(portfolio_id)

    # ── Holdings ───────────────────────────────────────────────────────────────

    async def add_holding(
        self,
        portfolio_id: str,
        bond_id: str,
        invested: float,
        purchase_date: date,
        # Campos opcionais: populados do catálogo se não fornecidos
        bond_name: str | None = None,
        bond_type: str | None = None,
        indexer: str | None = None,
        issuer: str | None = None,
        rate_annual: float | None = None,
        rate_pct_indexer: bool = False,
        maturity_date: date | None = None,
        ir_exempt: bool | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        # Tenta enriquecer com dados do catálogo
        from finanalytics_ai.application.services.fixed_income_service import FixedIncomeService

        svc = FixedIncomeService(get_tesouro_client())
        bond = await svc._find_bond(bond_id)

        if bond is not None:
            bond_name = bond_name or bond.name
            bond_type = bond_type or bond.bond_type.value
            indexer = indexer or bond.indexer.value
            issuer = issuer or bond.issuer
            rate_annual = rate_annual if rate_annual is not None else bond.rate_annual
            rate_pct_indexer = rate_pct_indexer or bond.rate_pct_indexer
            maturity_date = maturity_date or bond.maturity_date
            ir_exempt = ir_exempt if ir_exempt is not None else bond.ir_exempt
        else:
            # Bond personalizado — campos obrigatórios
            if not all([bond_name, bond_type, indexer, rate_annual is not None]):
                raise ValueError(
                    "Bond não encontrado no catálogo. Forneça: bond_name, bond_type, indexer, rate_annual."
                )
            ir_exempt = ir_exempt if ir_exempt is not None else False

        holding = await self._repo.add_holding(
            portfolio_id=portfolio_id,
            bond_id=bond_id,
            bond_name=bond_name,  # type: ignore
            bond_type=bond_type,  # type: ignore
            indexer=indexer,  # type: ignore
            issuer=issuer or "",
            invested=invested,
            rate_annual=rate_annual,  # type: ignore
            rate_pct_indexer=rate_pct_indexer,
            purchase_date=purchase_date,
            maturity_date=maturity_date,
            ir_exempt=ir_exempt,
            note=note,
        )
        return holding.to_dict()

    async def delete_holding(self, holding_id: str, portfolio_id: str) -> None:
        await self._repo.delete_holding(holding_id, portfolio_id)

    # ── Diversificação ─────────────────────────────────────────────────────────

    async def diversification_report(self, portfolio_id: str) -> dict[str, Any] | None:
        p = await self._repo.get_portfolio(portfolio_id)
        if p is None:
            return None
        report = DiversificationReport.build(p)
        return {
            "portfolio_id": report.portfolio_id,
            "portfolio_name": report.portfolio_name,
            "total_invested": report.total_invested,
            "n_holdings": report.n_holdings,
            "n_indexers": report.n_indexers,
            "n_issuers": report.n_issuers,
            "n_types": report.n_types,
            "ir_exempt_pct": report.ir_exempt_pct,
            "avg_rate_pct": report.avg_rate_pct,
            "avg_duration_days": report.avg_duration_days,
            "score": report.score,
            "score_label": report.score_label,
            "by_indexer": report.by_indexer,
            "by_type": report.by_type,
            "by_issuer": report.by_issuer,
            "alerts": [
                {
                    "alert_type": a.alert_type,
                    "name": a.name,
                    "pct": a.pct,
                    "severity": a.severity,
                    "message": a.message,
                }
                for a in report.alerts
            ],
            "recommendations": report.recommendations,
        }

    # ── Projeção de vencimentos ────────────────────────────────────────────────

    async def maturities_timeline(
        self,
        portfolio_id: str,
        selic: float = DEFAULT_SELIC,
        cdi: float = DEFAULT_CDI,
        ipca: float = DEFAULT_IPCA,
    ) -> list[dict[str, Any]] | None:
        p = await self._repo.get_portfolio(portfolio_id)
        if p is None:
            return None
        result = []
        for h in sorted(p.active_holdings, key=lambda x: x.maturity_date or date(2099, 12, 31)):
            idx_rate = {
                "CDI": cdi,
                "SELIC": selic,
                "IPCA": ipca,
            }.get(h.indexer, cdi)
            yr = calculate_yield(
                bond=_holding_to_bond(h),
                principal=h.invested,
                days=max(1, h.days_held),
                indexer_rate=idx_rate,
                inflation_rate=ipca,
            )
            result.append(
                {
                    "holding_id": h.holding_id,
                    "bond_name": h.bond_name,
                    "indexer": h.indexer,
                    "invested": h.invested,
                    "purchase_date": h.purchase_date.isoformat(),
                    "maturity_date": h.maturity_date.isoformat() if h.maturity_date else None,
                    "days_to_maturity": h.days_to_maturity,
                    "is_liquid": h.maturity_date is None,
                    "current_net_value": yr.net_amount,
                    "net_return_pct": yr.net_return_pct,
                    "ir_exempt": h.ir_exempt,
                }
            )
        return result

    # ── Análise FGC ───────────────────────────────────────────────────────────

    async def fgc_analysis(self, portfolio_id: str) -> dict[str, Any] | None:
        """
        Analisa cobertura FGC da carteira.
        Retorna status por holding, por instituição e alertas globais.
        """
        from finanalytics_ai.domain.fixed_income.fgc import analyze_fgc

        p = await self._repo.get_portfolio(portfolio_id)
        if p is None:
            return None
        analysis = analyze_fgc(portfolio_id, p.active_holdings)
        return {
            "portfolio_id": analysis.portfolio_id,
            "summary": analysis.summary,
            "score": analysis.score,
            "alerts": analysis.alerts,
            "institutions": [
                {
                    "issuer": i.issuer,
                    "total_invested": round(i.total_invested, 2),
                    "fgc_covered": i.fgc_covered,
                    "fgc_uncovered": i.fgc_uncovered,
                    "within_limit": i.within_limit,
                    "excess_amount": i.excess_amount,
                    "alert_level": i.alert_level,
                    "alert_message": i.alert_message,
                    "n_holdings": len(i.holdings),
                }
                for i in analysis.institutions
            ],
            "holdings": [
                {
                    "holding_id": s.holding_id,
                    "bond_name": s.bond_name,
                    "bond_type": s.bond_type,
                    "issuer": s.issuer,
                    "invested": round(s.invested, 2),
                    "coverage": s.coverage,
                    "coverage_label": s.coverage_label,
                    "is_within_limit": s.is_within_limit,
                    "excess_amount": s.excess_amount,
                    "alert_level": s.alert_level,
                    "alert_message": s.alert_message,
                }
                for s in analysis.holding_statuses
            ],
        }

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _portfolio_summary(self, p: RFPortfolio) -> dict[str, Any]:
        return {
            "portfolio_id": p.portfolio_id,
            "name": p.name,
            "user_id": p.user_id,
            "n_holdings": len(p.active_holdings),
            "total_invested": p.total_invested,
            "avg_rate_pct": p.avg_rate(),
            "ir_exempt_pct": p.ir_exempt_pct(),
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }

    def _portfolio_detail(
        self,
        p: RFPortfolio,
        selic: float,
        cdi: float,
        ipca: float,
    ) -> dict[str, Any]:
        summary = self._portfolio_summary(p)
        holdings_out = []
        total_current = 0.0
        for h in p.active_holdings:
            idx_rate = {"CDI": cdi, "SELIC": selic, "IPCA": ipca}.get(h.indexer, cdi)
            yr = calculate_yield(
                bond=_holding_to_bond(h),
                principal=h.invested,
                days=max(1, h.days_held),
                indexer_rate=idx_rate,
                inflation_rate=ipca,
            )
            total_current += yr.net_amount
            holdings_out.append(
                {
                    **h.to_dict(),
                    "current_net_value": yr.net_amount,
                    "net_return_pct": yr.net_return_pct,
                    "net_annual_return_pct": yr.net_annual_return_pct,
                    "ir_amount_accrued": yr.ir_amount,
                }
            )
        total_gain = total_current - p.total_invested
        summary["holdings"] = holdings_out
        summary["total_current"] = round(total_current, 2)
        summary["total_gain"] = round(total_gain, 2)
        summary["total_return_pct"] = (
            round(total_gain / p.total_invested * 100, 4) if p.total_invested else 0
        )
        summary["by_indexer"] = p.allocation_by_indexer()
        summary["by_type"] = p.allocation_by_type()
        summary["avg_duration_days"] = p.avg_duration_days()
        return summary


def _holding_to_bond(h: RFHolding):
    """Converte RFHolding em Bond para cálculo de yield."""
    from finanalytics_ai.domain.fixed_income.entities import (
        Bond,
        BondType,
        Indexer,
        PaymentFrequency,
    )

    try:
        bt = BondType(h.bond_type)
    except ValueError:
        bt = BondType.CDB
    try:
        idx = Indexer(h.indexer)
    except ValueError:
        idx = Indexer.CDI
    return Bond(
        bond_id=h.bond_id,
        name=h.bond_name,
        bond_type=bt,
        indexer=idx,
        rate_annual=h.rate_annual,
        rate_pct_indexer=h.rate_pct_indexer,
        maturity_date=h.maturity_date,
        issuer=h.issuer,
        ir_exempt=h.ir_exempt,
        payment_freq=PaymentFrequency.AT_MATURITY,
    )
