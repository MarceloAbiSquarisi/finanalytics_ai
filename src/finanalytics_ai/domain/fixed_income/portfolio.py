"""
finanalytics_ai.domain.fixed_income.portfolio
───────────────────────────────────────────────
Entidades de domínio para carteira de Renda Fixa.

Design decisions:
  RFHolding representa uma posição concreta em um título:
    Um usuário comprou X reais em um bond em uma data.
    O holding carrega o bond original + valor investido + data de compra.
    A partir disso, calculamos posição atual, rendimento acumulado e vencimento.

  RFPortfolio agrega múltiplos holdings de um usuário:
    Não é apenas uma lista — expõe métricas de diversificação, exposição
    por indexador, concentração por emissor e duration média da carteira.

  DiversificationReport é um value object calculado (não persistido):
    Resultado de uma análise point-in-time da carteira.
    Inclui scores de diversificação e alertas de concentração.

  Regras de negócio no domínio:
    - Concentração máxima saudável por emissor: 25%
    - Concentração máxima por indexador: 60%
    - Carteira bem diversificada: >= 3 indexadores, >= 4 emissores
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


# ── RFHolding ─────────────────────────────────────────────────────────────────

@dataclass
class RFHolding:
    """
    Posição em um título de renda fixa dentro de uma carteira.

    holding_id:    UUID da posição (gerado na criação)
    portfolio_id:  carteira à qual pertence
    bond_id:       referência ao Bond do catálogo
    bond_name:     snapshot do nome (evita JOIN em queries de leitura)
    bond_type:     snapshot do tipo (CDB, LCI, Tesouro SELIC…)
    indexer:       snapshot do indexador (CDI, IPCA, PREFIXADO…)
    issuer:        emissor (banco, governo…)
    invested:      valor aplicado em R$
    rate_annual:   taxa contratada (decimal, ex: 0.12 = 12% a.a.)
    rate_pct_indexer: True se a taxa é % do indexador (ex: 110% CDI)
    purchase_date: data da compra
    maturity_date: vencimento (None = sem vencimento / liquidez diária)
    ir_exempt:     True para LCI/LCA/CRI/CRA
    note:          observação livre do usuário
    """
    holding_id:       str
    portfolio_id:     str
    bond_id:          str
    bond_name:        str
    bond_type:        str
    indexer:          str
    issuer:           str
    invested:         float
    rate_annual:      float
    rate_pct_indexer: bool
    purchase_date:    date
    maturity_date:    Optional[date] = None
    ir_exempt:        bool           = False
    note:             str            = ""

    @property
    def days_held(self) -> int:
        return (date.today() - self.purchase_date).days

    @property
    def days_to_maturity(self) -> Optional[int]:
        if self.maturity_date is None:
            return None
        delta = (self.maturity_date - date.today()).days
        return max(0, delta)

    @property
    def is_matured(self) -> bool:
        if self.maturity_date is None:
            return False
        return self.maturity_date <= date.today()

    @property
    def years_held(self) -> float:
        return self.days_held / 252

    def to_dict(self) -> dict:
        return {
            "holding_id":       self.holding_id,
            "portfolio_id":     self.portfolio_id,
            "bond_id":          self.bond_id,
            "bond_name":        self.bond_name,
            "bond_type":        self.bond_type,
            "indexer":          self.indexer,
            "issuer":           self.issuer,
            "invested":         self.invested,
            "rate_annual":      round(self.rate_annual * 100, 4),
            "rate_pct_indexer": self.rate_pct_indexer,
            "purchase_date":    self.purchase_date.isoformat(),
            "maturity_date":    self.maturity_date.isoformat() if self.maturity_date else None,
            "days_held":        self.days_held,
            "days_to_maturity": self.days_to_maturity,
            "is_matured":       self.is_matured,
            "ir_exempt":        self.ir_exempt,
            "note":             self.note,
        }


# ── RFPortfolio ───────────────────────────────────────────────────────────────

@dataclass
class RFPortfolio:
    """
    Carteira de renda fixa de um usuário.

    portfolio_id: UUID
    user_id:      dono da carteira
    name:         nome dado pelo usuário
    holdings:     posições ativas
    created_at:   data de criação
    """
    portfolio_id: str
    user_id:      str
    name:         str
    holdings:     list[RFHolding] = field(default_factory=list)
    created_at:   Optional[date]  = None

    @property
    def total_invested(self) -> float:
        return sum(h.invested for h in self.holdings)

    @property
    def active_holdings(self) -> list[RFHolding]:
        return [h for h in self.holdings if not h.is_matured]

    @property
    def matured_holdings(self) -> list[RFHolding]:
        return [h for h in self.holdings if h.is_matured]

    def allocation_by_indexer(self) -> dict[str, float]:
        """Alocação percentual por indexador."""
        total = self.total_invested or 1.0
        buckets: dict[str, float] = {}
        for h in self.active_holdings:
            buckets[h.indexer] = buckets.get(h.indexer, 0.0) + h.invested
        return {k: round(v / total * 100, 2) for k, v in sorted(buckets.items(), key=lambda x: -x[1])}

    def allocation_by_type(self) -> dict[str, float]:
        """Alocação percentual por tipo de título."""
        total = self.total_invested or 1.0
        buckets: dict[str, float] = {}
        for h in self.active_holdings:
            buckets[h.bond_type] = buckets.get(h.bond_type, 0.0) + h.invested
        return {k: round(v / total * 100, 2) for k, v in sorted(buckets.items(), key=lambda x: -x[1])}

    def allocation_by_issuer(self) -> dict[str, float]:
        """Alocação percentual por emissor."""
        total = self.total_invested or 1.0
        buckets: dict[str, float] = {}
        for h in self.active_holdings:
            issuer = h.issuer or "Desconhecido"
            buckets[issuer] = buckets.get(issuer, 0.0) + h.invested
        return {k: round(v / total * 100, 2) for k, v in sorted(buckets.items(), key=lambda x: -x[1])}

    def ir_exempt_pct(self) -> float:
        """% da carteira isento de IR."""
        total = self.total_invested or 1.0
        exempt = sum(h.invested for h in self.active_holdings if h.ir_exempt)
        return round(exempt / total * 100, 2)

    def avg_rate(self) -> float:
        """Taxa média ponderada pelo valor investido (% a.a.)."""
        total = self.total_invested or 1.0
        weighted = sum(h.invested * h.rate_annual for h in self.active_holdings)
        return round(weighted / total * 100, 4)

    def avg_duration_days(self) -> Optional[float]:
        """Duration média ponderada até o vencimento."""
        active_with_maturity = [h for h in self.active_holdings if h.days_to_maturity is not None]
        if not active_with_maturity:
            return None
        total = sum(h.invested for h in active_with_maturity) or 1.0
        weighted = sum(h.invested * h.days_to_maturity for h in active_with_maturity)  # type: ignore
        return round(weighted / total, 1)


# ── DiversificationReport ─────────────────────────────────────────────────────

CONCENTRATION_ISSUER_LIMIT = 25.0   # % máximo saudável por emissor
CONCENTRATION_INDEXER_LIMIT = 60.0  # % máximo por indexador
MIN_INDEXERS_DIVERSIFIED = 3
MIN_ISSUERS_DIVERSIFIED = 4


@dataclass
class ConcentrationAlert:
    """Alerta de concentração em um ativo/grupo."""
    alert_type:  str    # "issuer" | "indexer" | "type"
    name:        str
    pct:         float
    limit:       float
    severity:    str    # "warning" | "critical"
    message:     str


@dataclass
class DiversificationReport:
    """
    Relatório de diversificação da carteira RF (value object calculado).

    score: 0–100, calculado com base em 4 critérios:
      1. Quantidade de indexadores distintos (max 30pts)
      2. Quantidade de emissores distintos (max 30pts)
      3. Concentração máxima por emissor (max 20pts)
      4. % isento de IR (max 20pts)
    """
    portfolio_id:      str
    portfolio_name:    str
    total_invested:    float
    n_holdings:        int
    n_indexers:        int
    n_issuers:         int
    n_types:           int
    by_indexer:        dict[str, float]
    by_type:           dict[str, float]
    by_issuer:         dict[str, float]
    ir_exempt_pct:     float
    avg_rate_pct:      float
    avg_duration_days: Optional[float]
    score:             int
    score_label:       str
    alerts:            list[ConcentrationAlert] = field(default_factory=list)
    recommendations:   list[str]               = field(default_factory=list)

    @classmethod
    def build(cls, portfolio: RFPortfolio) -> "DiversificationReport":
        by_indexer = portfolio.allocation_by_indexer()
        by_type    = portfolio.allocation_by_type()
        by_issuer  = portfolio.allocation_by_issuer()
        alerts: list[ConcentrationAlert] = []
        recs:   list[str] = []

        # Alertas de concentração por emissor
        for issuer, pct in by_issuer.items():
            if pct > CONCENTRATION_ISSUER_LIMIT:
                sev = "critical" if pct > 40 else "warning"
                alerts.append(ConcentrationAlert(
                    alert_type="issuer", name=issuer, pct=pct,
                    limit=CONCENTRATION_ISSUER_LIMIT, severity=sev,
                    message=f"{issuer} representa {pct:.1f}% da carteira (limite saudável: {CONCENTRATION_ISSUER_LIMIT}%)",
                ))

        # Alertas por indexador
        for idx, pct in by_indexer.items():
            if pct > CONCENTRATION_INDEXER_LIMIT:
                alerts.append(ConcentrationAlert(
                    alert_type="indexer", name=idx, pct=pct,
                    limit=CONCENTRATION_INDEXER_LIMIT, severity="warning",
                    message=f"{pct:.1f}% da carteira em {idx} — considere diversificar indexadores",
                ))

        # Score
        n_idx    = len(by_indexer)
        n_iss    = len(by_issuer)
        max_iss  = max(by_issuer.values()) if by_issuer else 100.0
        ir_pct   = portfolio.ir_exempt_pct()

        pts_idx  = min(30, n_idx * 10)
        pts_iss  = min(30, n_iss * 7)
        pts_conc = max(0, 20 - max(0, int(max_iss - 25)))
        pts_ir   = min(20, int(ir_pct / 5))
        score    = pts_idx + pts_iss + pts_conc + pts_ir

        if score >= 80:
            label = "Excelente"
        elif score >= 60:
            label = "Boa"
        elif score >= 40:
            label = "Razoável"
        else:
            label = "Concentrada"

        # Recomendações
        if n_idx < MIN_INDEXERS_DIVERSIFIED:
            recs.append(f"Adicione títulos com outros indexadores (atual: {n_idx}). Meta: ≥ {MIN_INDEXERS_DIVERSIFIED}.")
        if n_iss < MIN_ISSUERS_DIVERSIFIED:
            recs.append(f"Diversifique emissores (atual: {n_iss}). Meta: ≥ {MIN_ISSUERS_DIVERSIFIED}.")
        if ir_pct < 20 and portfolio.total_invested > 5000:
            recs.append("Considere LCI/LCA ou CRI/CRA para aumentar a parcela isenta de IR.")
        if "CDI" in by_indexer and by_indexer["CDI"] > 70:
            recs.append("Alta exposição ao CDI. IPCA+ protege contra inflação persistente.")
        if not recs:
            recs.append("Carteira bem diversificada. Continue monitorando os vencimentos.")

        return cls(
            portfolio_id=portfolio.portfolio_id,
            portfolio_name=portfolio.name,
            total_invested=portfolio.total_invested,
            n_holdings=len(portfolio.active_holdings),
            n_indexers=n_idx,
            n_issuers=n_iss,
            n_types=len(by_type),
            by_indexer=by_indexer,
            by_type=by_type,
            by_issuer=by_issuer,
            ir_exempt_pct=ir_pct,
            avg_rate_pct=portfolio.avg_rate(),
            avg_duration_days=portfolio.avg_duration_days(),
            score=score,
            score_label=label,
            alerts=alerts,
            recommendations=recs,
        )
