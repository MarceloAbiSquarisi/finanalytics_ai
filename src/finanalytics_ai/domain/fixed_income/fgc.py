"""
finanalytics_ai.domain.fixed_income.fgc
─────────────────────────────────────────
Regras do FGC (Fundo Garantidor de Créditos) aplicadas à carteira de RF.

Regras vigentes (Resolução CMN 4.222/2013 e alterações):
  Cobertura por CPF por instituição: R$ 250.000,00
  Cobertura global (todas as instituições): R$ 1.000.000,00 por período de 4 anos
  Renovação do limite global: a cada 4 anos o limite é zerado

Títulos cobertos pelo FGC:
  ✅ CDB  — Certificado de Depósito Bancário
  ✅ LCI  — Letra de Crédito Imobiliário
  ✅ LCA  — Letra de Crédito do Agronegócio
  ✅ RDB  — Recibo de Depósito Bancário
  ✅ LC   — Letra de Câmbio (financeiras)
  ✅ Poupança

Títulos NÃO cobertos pelo FGC:
  ❌ CRI  — Certificado de Recebíveis Imobiliários (garantia do estruturador)
  ❌ CRA  — Certificado de Recebíveis do Agronegócio (garantia do estruturador)
  ❌ Debêntures (risco do emissor)
  ❌ Tesouro Direto (garantia soberana — Tesouro Nacional, superior ao FGC)
  ❌ Fundos de Investimento (garantia do FI, não do FGC)

Fonte: https://www.fgc.org.br/garantia-do-fgc/sobre-a-garantia

Design decisions:
  Separamos as regras do FGC do domínio de portfólio (portfolio.py):
    FGC é uma preocupação transversal que pode ser aplicada a qualquer
    carteira, não apenas ao DiversificationReport.
    Ao manter em módulo separado, facilitamos reutilização e teste isolado.

  Tratamento do Tesouro Direto:
    Não coberto pelo FGC, mas possui garantia soberana (risco zero de crédito
    na prática). Geramos alerta INFORMATIVO, não de risco, para que o usuário
    saiba que o mecanismo de proteção é diferente, não ausente.

  Limite global de R$ 1M:
    O período de 4 anos é controlado pelo próprio FGC e não temos como
    saber quanto o usuário já utilizou em outras instituições/períodos.
    Alertamos quando o total coberto na carteira atual supera R$ 1M como
    proxy conservador.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from finanalytics_ai.domain.fixed_income.portfolio import RFHolding

# ── Limites FGC ───────────────────────────────────────────────────────────────

FGC_LIMIT_PER_INSTITUTION: float = 250_000.00
FGC_GLOBAL_LIMIT:          float = 1_000_000.00

# ── Classificação por tipo de título ─────────────────────────────────────────

# Tipos cobertos pelo FGC
FGC_COVERED_TYPES = {
    "CDB", "LCI", "LCA", "RDB", "LC", "Poupança",
}

# Tipos com garantia diferente (não FGC — informativo)
SOVEREIGN_TYPES = {
    "Tesouro SELIC", "Tesouro IPCA+", "Tesouro Prefixado",
}

# Tipos sem cobertura de garantia institucional
UNCOVERED_TYPES = {
    "CRI", "CRA", "Debênture",
}


def fgc_coverage(bond_type: str) -> str:
    """
    Retorna o tipo de cobertura de um título:
      'fgc'       — coberto pelo FGC (R$ 250k/inst., R$ 1M global)
      'sovereign' — garantia soberana do Tesouro Nacional
      'none'      — sem garantia institucional (risco do emissor/estruturador)
    """
    bt = bond_type.strip()
    if bt in FGC_COVERED_TYPES:
        return "fgc"
    if bt in SOVEREIGN_TYPES:
        return "sovereign"
    return "none"


# ── Resultado da análise FGC ──────────────────────────────────────────────────

@dataclass
class FGCHoldingStatus:
    """Status FGC de uma posição individual."""
    holding_id:    str
    bond_name:     str
    bond_type:     str
    issuer:        str
    invested:      float
    coverage:      str   # "fgc" | "sovereign" | "none"
    coverage_label:str
    is_within_limit:bool
    excess_amount: float  # valor acima do limite (0 se dentro)
    alert_level:   str   # "ok" | "info" | "warning" | "critical"
    alert_message: str


@dataclass
class FGCInstitutionStatus:
    """Exposição total a uma instituição + status FGC."""
    issuer:             str
    total_invested:     float
    fgc_covered:        float   # valor coberto pelo FGC nesta inst.
    fgc_uncovered:      float   # valor acima do limite
    within_limit:       bool
    excess_amount:      float
    holdings:           list[str]  # holding_ids
    alert_level:        str
    alert_message:      str


@dataclass
class FGCAnalysis:
    """Análise completa de cobertura FGC de uma carteira."""
    portfolio_id:          str
    total_invested:        float
    total_fgc_covered:     float   # soma de todos os valores cobertos
    total_sovereign:       float   # Tesouro Direto
    total_uncovered:       float   # CRI/CRA/Debêntures
    total_at_risk:         float   # acima do limite FGC (por inst.)
    institutions:          list[FGCInstitutionStatus]
    holding_statuses:      list[FGCHoldingStatus]
    alerts:                list[dict]
    summary:               dict
    score:                 int     # 0–100: 100 = tudo coberto e dentro dos limites


def analyze_fgc(portfolio_id: str, holdings: "list[RFHolding]") -> FGCAnalysis:
    """
    Analisa a cobertura FGC de uma lista de holdings ativos.
    """
    active = [h for h in holdings if not h.is_matured]
    if not active:
        return FGCAnalysis(
            portfolio_id=portfolio_id,
            total_invested=0, total_fgc_covered=0, total_sovereign=0,
            total_uncovered=0, total_at_risk=0,
            institutions=[], holding_statuses=[], alerts=[],
            summary={"message": "Carteira sem posições ativas."},
            score=100,
        )

    # Agrupa por instituição (apenas holdings FGC)
    inst_map: dict[str, dict] = {}
    for h in active:
        cov = fgc_coverage(h.bond_type)
        issuer = h.issuer.strip() or "Desconhecido"
        if cov == "fgc":
            if issuer not in inst_map:
                inst_map[issuer] = {"total": 0.0, "holdings": []}
            inst_map[issuer]["total"]    += h.invested
            inst_map[issuer]["holdings"].append(h.holding_id)

    # Status por instituição
    institutions: list[FGCInstitutionStatus] = []
    total_at_risk = 0.0
    for issuer, data in inst_map.items():
        total     = data["total"]
        covered   = min(total, FGC_LIMIT_PER_INSTITUTION)
        excess    = max(0.0, total - FGC_LIMIT_PER_INSTITUTION)
        within    = excess == 0.0
        total_at_risk += excess

        if excess == 0.0:
            level = "ok"
            msg   = f"{issuer}: R$ {total:,.0f} cobertos (limite: R$ {FGC_LIMIT_PER_INSTITUTION:,.0f})"
        elif total <= FGC_LIMIT_PER_INSTITUTION * 1.2:
            level = "warning"
            msg   = (f"{issuer}: R$ {total:,.0f} investidos — "
                     f"R$ {excess:,.0f} ACIMA do limite de R$ {FGC_LIMIT_PER_INSTITUTION:,.0f}/inst.")
        else:
            level = "critical"
            msg   = (f"{issuer}: R$ {total:,.0f} investidos — "
                     f"R$ {excess:,.0f} SEM COBERTURA FGC (excede limite em "
                     f"{excess/FGC_LIMIT_PER_INSTITUTION*100:.0f}%)")

        institutions.append(FGCInstitutionStatus(
            issuer=issuer, total_invested=total,
            fgc_covered=round(covered, 2), fgc_uncovered=round(excess, 2),
            within_limit=within, excess_amount=round(excess, 2),
            holdings=data["holdings"], alert_level=level, alert_message=msg,
        ))

    institutions.sort(key=lambda i: -i.total_invested)

    # Status por holding
    holding_statuses: list[FGCHoldingStatus] = []
    for h in active:
        cov = fgc_coverage(h.bond_type)
        issuer = h.issuer.strip() or "Desconhecido"

        if cov == "fgc":
            inst_total = inst_map.get(issuer, {}).get("total", 0.0)
            excess = max(0.0, inst_total - FGC_LIMIT_PER_INSTITUTION)
            # Proporção deste holding no excesso da instituição
            holding_excess = excess * (h.invested / inst_total) if inst_total > 0 else 0.0
            within = excess == 0.0
            label  = "✅ Coberto pelo FGC"
            level  = "ok" if within else ("warning" if excess < 50_000 else "critical")
            msg    = (
                f"Coberto pelo FGC (R$ {FGC_LIMIT_PER_INSTITUTION:,.0f}/inst.)"
                if within else
                f"Parte sem cobertura FGC: ~R$ {holding_excess:,.0f} desprotegidos "
                f"(total em {issuer}: R$ {inst_total:,.0f})"
            )
        elif cov == "sovereign":
            within, holding_excess = True, 0.0
            label  = "🏛️ Garantia Tesouro Nacional"
            level  = "info"
            msg    = ("Tesouro Direto: garantia soberana do Governo Federal. "
                      "Não usa FGC — proteção superior para títulos públicos.")
        else:
            within, holding_excess = False, h.invested
            label  = "⚠️ Sem cobertura FGC"
            level  = "critical"
            msg    = (f"{h.bond_type}: não coberto pelo FGC. "
                      "O risco de crédito é do emissor/estruturador. "
                      "Avalie rating e garantias específicas do papel.")

        holding_statuses.append(FGCHoldingStatus(
            holding_id=h.holding_id, bond_name=h.bond_name,
            bond_type=h.bond_type, issuer=issuer, invested=h.invested,
            coverage=cov, coverage_label=label,
            is_within_limit=within, excess_amount=round(holding_excess, 2),
            alert_level=level, alert_message=msg,
        ))

    # Totais
    total_invested  = sum(h.invested for h in active)
    total_fgc       = sum(h.invested for h in active if fgc_coverage(h.bond_type) == "fgc")
    total_sovereign = sum(h.invested for h in active if fgc_coverage(h.bond_type) == "sovereign")
    total_uncovered = sum(h.invested for h in active if fgc_coverage(h.bond_type) == "none")

    # Alertas globais
    alerts: list[dict] = []

    # 1. Títulos sem cobertura
    uncovered_holdings = [s for s in holding_statuses if s.coverage == "none"]
    if uncovered_holdings:
        names = ", ".join(s.bond_name for s in uncovered_holdings[:3])
        alerts.append({
            "type": "no_fgc_coverage", "level": "critical",
            "message": (f"{len(uncovered_holdings)} título(s) SEM cobertura FGC: {names}. "
                        f"Total exposto: R$ {total_uncovered:,.0f}."),
        })

    # 2. Excesso por instituição
    for inst in institutions:
        if inst.excess_amount > 0:
            alerts.append({
                "type": "institution_limit_exceeded", "level": inst.alert_level,
                "message": inst.alert_message,
            })

    # 3. Limite global
    if total_fgc > FGC_GLOBAL_LIMIT:
        excess_global = total_fgc - FGC_GLOBAL_LIMIT
        alerts.append({
            "type": "global_limit_exceeded", "level": "warning",
            "message": (f"Total coberto pelo FGC (R$ {total_fgc:,.0f}) supera o "
                        f"limite global de R$ {FGC_GLOBAL_LIMIT:,.0f}/4 anos. "
                        f"R$ {excess_global:,.0f} podem não ser cobertos em caso de insolvência múltipla."),
        })

    # 4. Tesouro — informativo
    if total_sovereign > 0:
        alerts.append({
            "type": "sovereign_info", "level": "info",
            "message": (f"R$ {total_sovereign:,.0f} em Tesouro Direto. "
                        "Garantia soberana — não depende do FGC."),
        })

    # Score
    n_critical = sum(1 for a in alerts if a["level"] == "critical")
    n_warning  = sum(1 for a in alerts if a["level"] == "warning")
    score = max(0, 100 - n_critical * 25 - n_warning * 10)

    summary = {
        "total_invested":      round(total_invested, 2),
        "total_fgc_covered":   round(total_fgc, 2),
        "total_sovereign":     round(total_sovereign, 2),
        "total_uncovered":     round(total_uncovered, 2),
        "total_at_risk":       round(total_at_risk, 2),
        "fgc_coverage_pct":    round(total_fgc / total_invested * 100, 1) if total_invested else 0,
        "uncovered_pct":       round(total_uncovered / total_invested * 100, 1) if total_invested else 0,
        "n_institutions":      len(inst_map),
        "score":               score,
    }

    return FGCAnalysis(
        portfolio_id=portfolio_id,
        total_invested=round(total_invested, 2),
        total_fgc_covered=round(total_fgc, 2),
        total_sovereign=round(total_sovereign, 2),
        total_uncovered=round(total_uncovered, 2),
        total_at_risk=round(total_at_risk, 2),
        institutions=institutions,
        holding_statuses=holding_statuses,
        alerts=alerts,
        summary=summary,
        score=score,
    )
