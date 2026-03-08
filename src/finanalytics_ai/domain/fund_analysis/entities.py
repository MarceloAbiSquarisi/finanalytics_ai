"""
finanalytics_ai.domain.fund_analysis.entities
──────────────────────────────────────────────
Entidades de domínio para análise de lâminas de fundos de investimento.

Design decision — estrutura orientada à decisão:
  O resultado final é uma RECOMENDAÇÃO binária (investir / não investir)
  com pontuação 0–100 e justificativa estruturada.
  Cada dimensão de análise (rentabilidade, risco, custos, liquidez, gestor)
  contribui com um score parcial que compõe o score final.
  Isso permite rastrear exatamente por que a recomendação foi dada,
  tornando o output auditável — essencial para decisões financeiras.

Separação clara de responsabilidades:
  Este módulo só define as estruturas de dados.
  A chamada à IA e a lógica de parsing ficam no application service.
  O domínio não sabe que existe uma LLM — poderia ser rule-based amanhã.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FundMetrics:
    """Métricas extraídas da lâmina."""
    # Identificação
    fund_name:          str = ""
    cnpj:               str = ""
    manager:            str = ""
    administrator:      str = ""
    fund_type:          str = ""   # Multimercado, Ações, RF, FII, etc.
    benchmark:          str = ""   # CDI, IBOV, IPCA+, etc.
    inception_date:     str = ""

    # Rentabilidade
    return_1m:          Optional[float] = None   # % mês
    return_3m:          Optional[float] = None
    return_6m:          Optional[float] = None
    return_12m:         Optional[float] = None
    return_24m:         Optional[float] = None
    return_since_start: Optional[float] = None
    benchmark_12m:      Optional[float] = None   # retorno do benchmark no mesmo período

    # Risco
    volatility_12m:     Optional[float] = None   # % a.a.
    max_drawdown:       Optional[float] = None   # % (negativo)
    sharpe:             Optional[float] = None
    var_95:             Optional[float] = None   # % (negativo)

    # Custos
    admin_fee:          Optional[float] = None   # % a.a.
    performance_fee:    Optional[float] = None   # % sobre excedente
    performance_hurdle: str = ""                  # ex: "CDI", "IPCA+6%"
    entry_fee:          Optional[float] = None
    exit_fee:           Optional[float] = None

    # Liquidez
    redemption_days:    Optional[int]   = None   # dias para resgate (cotização + liquidação)
    min_investment:     Optional[float] = None   # R$
    min_additional:     Optional[float] = None

    # Patrimônio e estrutura
    aum:                Optional[float] = None   # PL em R$ milhões
    n_shareholders:     Optional[int]   = None
    investment_policy:  str = ""


@dataclass
class AnalysisDimension:
    """Avaliação de uma dimensão específica do fundo."""
    name:       str
    score:      int          # 0–100
    label:      str          # "Excelente" / "Bom" / "Regular" / "Ruim"
    pros:       list[str] = field(default_factory=list)
    cons:       list[str] = field(default_factory=list)
    notes:      str = ""


@dataclass
class FundAnalysis:
    """Resultado completo da análise de uma lâmina de fundo."""
    # Dados extraídos
    metrics:            FundMetrics
    raw_text_excerpt:   str = ""     # trecho da lâmina para auditoria

    # Análise por dimensão
    dimensions:         list[AnalysisDimension] = field(default_factory=list)

    # Score e recomendação
    total_score:        int = 0      # 0–100 (média ponderada das dimensões)
    recommendation:     str = ""     # "INVESTIR" | "NÃO INVESTIR" | "AGUARDAR"
    recommendation_summary: str = "" # 1–2 frases resumindo o veredicto
    key_risks:          list[str] = field(default_factory=list)
    key_strengths:      list[str] = field(default_factory=list)
    red_flags:          list[str] = field(default_factory=list)   # alertas críticos
    suggested_profile:  str = ""     # "Conservador" / "Moderado" / "Arrojado"
    horizon:            str = ""     # "Curto prazo (<1 ano)" / "Médio" / "Longo (>3 anos)"

    # Comparativo contextual
    context_notes:      list[str] = field(default_factory=list)

    # Meta
    analyzed_at:        str = ""
    model_used:         str = ""
    filename:           str = ""

    @property
    def recommendation_color(self) -> str:
        return {"INVESTIR": "#00c48c", "NÃO INVESTIR": "#ff4757"}.get(
            self.recommendation, "#ffb300"
        )

    @property
    def score_label(self) -> str:
        if self.total_score >= 75: return "Excelente"
        if self.total_score >= 60: return "Bom"
        if self.total_score >= 45: return "Regular"
        return "Ruim"

    def to_dict(self) -> dict:
        return {
            "filename":            self.filename,
            "analyzed_at":        self.analyzed_at,
            "model_used":         self.model_used,
            "metrics": {
                "fund_name":       self.metrics.fund_name,
                "cnpj":            self.metrics.cnpj,
                "manager":         self.metrics.manager,
                "administrator":   self.metrics.administrator,
                "fund_type":       self.metrics.fund_type,
                "benchmark":       self.metrics.benchmark,
                "inception_date":  self.metrics.inception_date,
                "return_1m":       self.metrics.return_1m,
                "return_3m":       self.metrics.return_3m,
                "return_6m":       self.metrics.return_6m,
                "return_12m":      self.metrics.return_12m,
                "return_24m":      self.metrics.return_24m,
                "return_since_start": self.metrics.return_since_start,
                "benchmark_12m":   self.metrics.benchmark_12m,
                "volatility_12m":  self.metrics.volatility_12m,
                "max_drawdown":    self.metrics.max_drawdown,
                "sharpe":          self.metrics.sharpe,
                "admin_fee":       self.metrics.admin_fee,
                "performance_fee": self.metrics.performance_fee,
                "performance_hurdle": self.metrics.performance_hurdle,
                "redemption_days": self.metrics.redemption_days,
                "min_investment":  self.metrics.min_investment,
                "aum":             self.metrics.aum,
                "investment_policy": self.metrics.investment_policy,
            },
            "dimensions": [
                {
                    "name":  d.name,
                    "score": d.score,
                    "label": d.label,
                    "pros":  d.pros,
                    "cons":  d.cons,
                    "notes": d.notes,
                }
                for d in self.dimensions
            ],
            "total_score":          self.total_score,
            "score_label":          self.score_label,
            "recommendation":       self.recommendation,
            "recommendation_color": self.recommendation_color,
            "recommendation_summary": self.recommendation_summary,
            "key_risks":            self.key_risks,
            "key_strengths":        self.key_strengths,
            "red_flags":            self.red_flags,
            "suggested_profile":    self.suggested_profile,
            "horizon":              self.horizon,
            "context_notes":        self.context_notes,
        }
