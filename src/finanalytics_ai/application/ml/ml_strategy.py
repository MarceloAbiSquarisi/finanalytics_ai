"""
finanalytics_ai.application.ml.ml_strategy
Combina ReturnForecast + RiskMetrics em sinal de estrategia acionavel.

Score: prob_positive * (p50 / var_consensus)
  Interpreta como retorno esperado ajustado ao risco ponderado pela
  probabilidade de ganho — analogo ao Sharpe Ratio com quantis ML.

Thresholds (calibrados para Ibovespa):
  STRONG_BUY  score > 0.30
  BUY         score > 0.10
  HOLD        -0.10 <= score <= 0.10
  SELL        score < -0.10
  STRONG_SELL score < -0.30
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from finanalytics_ai.domain.ml.entities import ReturnForecast, RiskMetrics

_STRONG_BUY_THRESHOLD  =  0.30
_BUY_THRESHOLD         =  0.10
_SELL_THRESHOLD        = -0.10
_STRONG_SELL_THRESHOLD = -0.30


@dataclass(frozen=True)
class StrategySignal:
    """
    Sinal de estrategia para um ticker.

    score: continuo, nao clampado — preserva magnitude comparativa.
    signal: STRONG_BUY | BUY | HOLD | SELL | STRONG_SELL
    confidence: prob_positive do modelo (0-1)
    reasoning: componentes auditaveis sem recomputacao
    """
    ticker: str
    signal: str
    score: float
    confidence: float
    horizon_days: int
    reasoning: dict[str, Any] = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return self.signal in ("STRONG_BUY", "BUY", "SELL", "STRONG_SELL")

    @property
    def direction(self) -> str:
        if "BUY" in self.signal:  return "LONG"
        if "SELL" in self.signal: return "SHORT"
        return "NEUTRAL"


class MLStrategy:
    """Gera e ranqueia sinais de estrategia. Stateless."""

    def evaluate(self, forecast: ReturnForecast, risk: RiskMetrics) -> StrategySignal:
        var_c = risk.var_consensus or 1e-4
        score = forecast.prob_positive * (forecast.p50 / var_c)
        signal = self._classify(score)

        reasoning: dict[str, Any] = {
            "score_components": {
                "prob_positive": round(forecast.prob_positive, 3),
                "p50_pct": round(forecast.p50 * 100, 2),
                "var_consensus_pct": round(var_c * 100, 2),
            },
            "forecast": {
                "p10_pct": round(forecast.p10 * 100, 2),
                "p50_pct": round(forecast.p50 * 100, 2),
                "p90_pct": round(forecast.p90 * 100, 2),
                "prob_positive_pct": round(forecast.prob_positive * 100, 1),
                "upside_pct": round(forecast.upside_pct * 100, 2),
                "downside_pct": round(forecast.downside_pct * 100, 2),
            },
            "risk": {
                "risk_level": risk.risk_level,
                "var_historical_pct": round(risk.var_95_historical * 100, 2),
                "var_parametric_pct": round(risk.var_95_parametric * 100, 2),
                "var_garch_pct": round(risk.var_95_garch * 100, 2) if risk.var_95_garch else None,
                "volatility_annual_pct": round(risk.volatility_annual * 100, 2),
                "t_df": round(risk.t_degrees_of_freedom, 1),
            },
        }

        return StrategySignal(
            ticker=forecast.ticker,
            signal=signal,
            score=round(score, 4),
            confidence=forecast.prob_positive,
            horizon_days=forecast.horizon_days,
            reasoning=reasoning,
        )

    def rank(
        self,
        signals: list[StrategySignal],
        min_confidence: float = 0.0,
    ) -> list[StrategySignal]:
        """Ordena por score decrescente com filtro opcional de confianca minima."""
        filtered = [s for s in signals if s.confidence >= min_confidence]
        return sorted(filtered, key=lambda s: s.score, reverse=True)

    @staticmethod
    def _classify(score: float) -> str:
        if score >= _STRONG_BUY_THRESHOLD:  return "STRONG_BUY"
        if score >= _BUY_THRESHOLD:         return "BUY"
        if score <= _STRONG_SELL_THRESHOLD: return "STRONG_SELL"
        if score <= _SELL_THRESHOLD:        return "SELL"
        return "HOLD"