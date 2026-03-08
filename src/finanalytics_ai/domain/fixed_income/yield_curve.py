"""
finanalytics_ai.domain.fixed_income.yield_curve
─────────────────────────────────────────────────
Entidades de domínio para curva de juros DI Futuro e stress test.

Design decisions:
  YieldCurvePoint representa um vértice da curva: prazo + taxa.
  A curva é construída a partir de contratos DI Futuro (B3/ANBIMA).
  Cada ponto é independente — sem interpolação no domínio (responsabilidade
  da camada de visualização).

  StressScenario define um choque nos indexadores:
    delta_selic, delta_cdi, delta_ipca são variações em pontos percentuais
    absolutos (não relativos). Ex: delta_selic=0.01 = +1 p.p.

  StressResult é o resultado de calcular um bond sob um cenário:
    Contém yield original e yield estressado para comparação direta.

  ScenarioComparison agrega múltiplos StressResult para um bond:
    Um bond × N cenários → um ScenarioComparison.

  Convenção de sinal nos deltas:
    Positivo = choque adverso de alta (SELIC sobe).
    Negativo = choque de queda. Neutro (0.0) = cenário base.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


# ── Curva de Juros ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class YieldCurvePoint:
    """
    Vértice da curva de juros DI Futuro.

    maturity_days: dias corridos até o vencimento do contrato
    maturity_date: data de vencimento do contrato DI Futuro
    rate_annual:   taxa anual pré (decimal, ex: 0.1285 = 12,85%)
    contract:      código do contrato (ex: "DI1F26")
    source:        "anbima" | "b3" | "synthetic"
    """
    maturity_days: int
    rate_annual:   float
    maturity_date: Optional[date] = None
    contract:      str            = ""
    source:        str            = "synthetic"

    @property
    def rate_pct(self) -> float:
        """Taxa em % (ex: 12.85)."""
        return round(self.rate_annual * 100, 4)

    @property
    def maturity_years(self) -> float:
        return round(self.maturity_days / 252, 2)


@dataclass
class YieldCurve:
    """
    Curva de juros completa — conjunto de vértices DI Futuro.

    reference_date: data de referência da curva
    selic:          taxa SELIC atual (usada como âncora do curto prazo)
    cdi:            taxa CDI diária anualizada
    points:         lista de YieldCurvePoint ordenados por prazo
    """
    reference_date: date
    selic:          float
    cdi:            float
    ipca:           float
    points:         list[YieldCurvePoint] = field(default_factory=list)
    source:         str                   = "synthetic"

    @property
    def short_rate(self) -> float:
        """Taxa do vértice mais curto da curva."""
        return self.points[0].rate_annual if self.points else self.selic

    @property
    def long_rate(self) -> float:
        """Taxa do vértice mais longo."""
        return self.points[-1].rate_annual if self.points else self.selic

    @property
    def is_inverted(self) -> bool:
        """Curva invertida: taxa longa < taxa curta."""
        if len(self.points) < 2:
            return False
        return self.long_rate < self.short_rate

    @property
    def slope(self) -> float:
        """Inclinação: longa - curta (p.p.). Positivo = normal, negativo = invertida."""
        if len(self.points) < 2:
            return 0.0
        return round((self.long_rate - self.short_rate) * 100, 2)


# ── Stress Test ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StressScenario:
    """
    Define um cenário de stress aplicado aos indexadores.

    name:         nome descritivo (ex: "SELIC +1 p.p.")
    delta_selic:  variação em pontos decimais (0.01 = +1 p.p.)
    delta_cdi:    variação no CDI
    delta_ipca:   variação no IPCA
    delta_igpm:   variação no IGPM
    color:        cor para o gráfico frontend
    """
    name:        str
    delta_selic: float = 0.0
    delta_cdi:   float = 0.0
    delta_ipca:  float = 0.0
    delta_igpm:  float = 0.0
    color:       str   = "#8899aa"

    def apply_to_rates(
        self,
        base_selic: float,
        base_cdi:   float,
        base_ipca:  float,
        base_igpm:  float,
    ) -> dict[str, float]:
        """Retorna taxas estressadas."""
        return {
            "selic": max(0.0, base_selic + self.delta_selic),
            "cdi":   max(0.0, base_cdi   + self.delta_cdi),
            "ipca":  max(0.0, base_ipca  + self.delta_ipca),
            "igpm":  max(0.0, base_igpm  + self.delta_igpm),
        }


# Cenários padrão pré-definidos
STANDARD_SCENARIOS: list[StressScenario] = [
    StressScenario("Base",          color="#00c48c"),
    StressScenario("SELIC +1 p.p.", delta_selic=0.01, delta_cdi=0.01, color="#ffb300"),
    StressScenario("SELIC +2 p.p.", delta_selic=0.02, delta_cdi=0.02, color="#ff6b35"),
    StressScenario("SELIC -1 p.p.", delta_selic=-0.01, delta_cdi=-0.01, color="#4ecdc4"),
    StressScenario("SELIC -2 p.p.", delta_selic=-0.02, delta_cdi=-0.02, color="#45b7d1"),
    StressScenario("IPCA +2 p.p.", delta_ipca=0.02,  color="#e056fd"),
    StressScenario("IPCA -2 p.p.", delta_ipca=-0.02, color="#a29bfe"),
    StressScenario("Crise: SELIC +3 p.p. / IPCA +3 p.p.",
                   delta_selic=0.03, delta_cdi=0.03, delta_ipca=0.03, color="#ff4757"),
    StressScenario("Desinflação: SELIC -3 p.p. / IPCA -2 p.p.",
                   delta_selic=-0.03, delta_cdi=-0.03, delta_ipca=-0.02, color="#26de81"),
]


@dataclass
class StressResult:
    """Resultado de um único bond × um único cenário."""
    scenario_name:    str
    bond_id:          str
    bond_name:        str
    principal:        float
    days:             int

    # Taxas aplicadas neste cenário
    selic_applied:    float
    cdi_applied:      float
    ipca_applied:     float

    # Resultados
    gross_return:     float   # rendimento bruto %
    net_return:       float   # rendimento líquido % (após IR/IOF)
    net_value:        float   # valor final líquido R$
    ir_amount:        float   # IR pago R$
    iof_amount:       float   # IOF pago R$
    effective_rate:   float   # taxa efetiva anual %
    color:            str     = "#8899aa"

    @property
    def net_return_pct(self) -> float:
        return round(self.net_return * 100, 4)

    @property
    def gross_return_pct(self) -> float:
        return round(self.gross_return * 100, 4)


@dataclass
class ScenarioComparison:
    """Comparação de um bond através de múltiplos cenários de stress."""
    bond_id:    str
    bond_name:  str
    principal:  float
    days:       int
    results:    list[StressResult] = field(default_factory=list)

    @property
    def base_result(self) -> Optional[StressResult]:
        for r in self.results:
            if r.scenario_name == "Base":
                return r
        return self.results[0] if self.results else None

    @property
    def worst_result(self) -> Optional[StressResult]:
        if not self.results:
            return None
        return min(self.results, key=lambda r: r.net_return)

    @property
    def best_result(self) -> Optional[StressResult]:
        if not self.results:
            return None
        return max(self.results, key=lambda r: r.net_return)

    @property
    def max_drawdown_pct(self) -> float:
        """Queda máxima do rendimento líquido vs cenário base (p.p.)."""
        base = self.base_result
        worst = self.worst_result
        if not base or not worst:
            return 0.0
        return round((base.net_return - worst.net_return) * 100, 4)
