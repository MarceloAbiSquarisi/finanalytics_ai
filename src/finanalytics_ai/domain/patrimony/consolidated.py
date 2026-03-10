"""
finanalytics_ai.domain.patrimony.consolidated
───────────────────────────────────────────────
Domínio de Patrimônio Consolidado.

Agrega Ações, ETFs (via portfolio de ações) e Renda Fixa num único snapshot.

Design decisions:
  Por que não criar uma tabela de "metas" no banco?
    Metas de alocação são preferências do usuário, não dados transacionais.
    Persistimos como JSON simples em memória/request para evitar migrations.
    Se persistência for necessária no futuro, um campo JSONB na tabela de
    portfolios é suficiente — sem nova tabela.

  ETFs vs Ações — mesma classe de ativo?
    No modelo de portfólio atual, ETFs são comprados como posições normais.
    Distinguimos pela presença do ticker no ETF_CATALOG. Isso é frágil?
    Sim — um ticker customizado poderia ser confundido. Trade-off aceito:
    o usuário que adiciona BOVA11 sabe que é ETF.

  Evolução histórica sintética:
    Não temos série histórica de snapshots no banco (seria caro manter).
    Geramos evolução histórica com base nos preços históricos dos ativos
    ponderados pelos pesos atuais (retorno atribuído). É uma aproximação
    válida para mostrar tendência — não é P&L histórico exato.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AssetClass(StrEnum):
    EQUITIES = "Ações"
    ETFS = "ETFs"
    FIXED_INC = "Renda Fixa"
    CASH = "Caixa"


@dataclass
class AssetClassSummary:
    asset_class: AssetClass
    current_value: float
    invested: float
    profit_loss: float
    weight_pct: float  # % do patrimônio total
    target_pct: float  # % meta do usuário (0 = sem meta)
    deviation_ppt: float  # desvio da meta em p.p.
    n_positions: int
    color: str

    @property
    def profit_loss_pct(self) -> float:
        return round(self.profit_loss / self.invested * 100, 2) if self.invested else 0.0

    @property
    def is_overweight(self) -> bool:
        return self.target_pct > 0 and self.deviation_ppt > 2.0

    @property
    def is_underweight(self) -> bool:
        return self.target_pct > 0 and self.deviation_ppt < -2.0

    def to_dict(self) -> dict:
        return {
            "asset_class": self.asset_class.value,
            "current_value": round(self.current_value, 2),
            "invested": round(self.invested, 2),
            "profit_loss": round(self.profit_loss, 2),
            "profit_loss_pct": self.profit_loss_pct,
            "weight_pct": round(self.weight_pct, 2),
            "target_pct": self.target_pct,
            "deviation_ppt": round(self.deviation_ppt, 2),
            "n_positions": self.n_positions,
            "color": self.color,
            "status": "overweight" if self.is_overweight else "underweight" if self.is_underweight else "ok",
        }


@dataclass
class ConsolidatedSnapshot:
    """Snapshot consolidado do patrimônio em um instante."""

    user_id: str
    total_value: float  # valor atual total
    total_invested: float  # custo total
    total_pl: float  # lucro/prejuízo absoluto
    total_pl_pct: float  # retorno total %
    classes: list[AssetClassSummary]
    allocation_ok: bool  # True se tudo dentro de ±2 p.p. da meta
    rebalance_needed: list[str]  # classes que precisam rebalancear
    cash_value: float  # caixa disponível

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "total_value": round(self.total_value, 2),
            "total_invested": round(self.total_invested, 2),
            "total_pl": round(self.total_pl, 2),
            "total_pl_pct": round(self.total_pl_pct, 2),
            "cash_value": round(self.cash_value, 2),
            "allocation_ok": self.allocation_ok,
            "rebalance_needed": self.rebalance_needed,
            "classes": [c.to_dict() for c in self.classes],
        }


# ── Constantes visuais ────────────────────────────────────────────────────────

CLASS_COLORS = {
    AssetClass.EQUITIES: "#00c48c",
    AssetClass.ETFS: "#4ecdc4",
    AssetClass.FIXED_INC: "#ffb300",
    AssetClass.CASH: "#8899aa",
}

DEFAULT_TARGETS: dict[AssetClass, float] = {
    AssetClass.EQUITIES: 40.0,
    AssetClass.ETFS: 20.0,
    AssetClass.FIXED_INC: 35.0,
    AssetClass.CASH: 5.0,
}


def build_snapshot(
    user_id: str,
    equities_value: float,
    equities_invested: float,
    equities_positions: int,
    etfs_value: float,
    etfs_invested: float,
    etfs_positions: int,
    rf_value: float,
    rf_invested: float,
    rf_positions: int,
    cash_value: float,
    targets: dict[AssetClass, float] | None = None,
) -> ConsolidatedSnapshot:
    targets = targets or DEFAULT_TARGETS.copy()
    total = equities_value + etfs_value + rf_value + cash_value

    classes_raw = [
        (AssetClass.EQUITIES, equities_value, equities_invested, equities_positions),
        (AssetClass.ETFS, etfs_value, etfs_invested, etfs_positions),
        (AssetClass.FIXED_INC, rf_value, rf_invested, rf_positions),
        (AssetClass.CASH, cash_value, cash_value, 0),
    ]

    classes: list[AssetClassSummary] = []
    rebalance_needed: list[str] = []

    for cls, val, inv, n_pos in classes_raw:
        weight = (val / total * 100) if total > 0 else 0.0
        target = targets.get(cls, 0.0)
        dev = round(weight - target, 2) if target > 0 else 0.0
        pl = val - inv

        s = AssetClassSummary(
            asset_class=cls,
            current_value=val,
            invested=inv,
            profit_loss=pl,
            weight_pct=weight,
            target_pct=target,
            deviation_ppt=dev,
            n_positions=n_pos,
            color=CLASS_COLORS[cls],
        )
        classes.append(s)
        if s.is_overweight or s.is_underweight:
            rebalance_needed.append(cls.value)

    # Cash é parte do patrimônio mas não é "capital investido" — tratamos separado.
    # total_pl_pct usa total_invested + cash como base (retorno sobre total de ativos).
    total_invested = equities_invested + etfs_invested + rf_invested
    total_pl = total - total_invested
    _pl_base = total_invested + cash_value
    total_pl_pct = (total_pl / _pl_base * 100) if _pl_base else 0.0

    return ConsolidatedSnapshot(
        user_id=user_id,
        total_value=total,
        total_invested=total_invested,
        total_pl=total_pl,
        total_pl_pct=total_pl_pct,
        classes=classes,
        allocation_ok=len(rebalance_needed) == 0,
        rebalance_needed=rebalance_needed,
        cash_value=cash_value,
    )
