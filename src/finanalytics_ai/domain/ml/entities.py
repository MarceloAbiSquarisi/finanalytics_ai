"""
finanalytics_ai.domain.ml.entities

Value objects do dominio de ML Probabilistico.

Decisoes:
  - frozen=True: previsoes sao imutaveis — resultado de um modelo em t0.
  - Optional[float] para features: dados podem estar ausentes sem corromper
    o pipeline — o modelo ignora NaN via LightGBM nativo.
  - Intervalos de confianca P10/P50/P90:
    P10 = cenario pessimista (10% de chance de retorno abaixo deste valor)
    P50 = retorno mediano esperado
    P90 = cenario otimista (90% de chance de retorno abaixo deste valor)
    Mais intuitivo que media +/- desvio para usuarios nao tecnicos.
  - VaR/CVaR em tres camadas:
    historico: nao-parametrico, base sempre presente
    parametrico: ajuste t-Student (captura fat tails vs Normal)
    garch: volatilidade condicional (captura clustering de risco)
    monte_carlo: simulacao de cenarios correlacionados
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TickerFeatures:
    """
    Matriz de features de um ticker em uma data especifica.

    Technical features (janelas padrao da literatura):
      ret_5d/21d/63d : retorno acumulado (momentum de curto/medio/longo prazo)
      volatility_21d : desvio padrao anualizado dos retornos diarios (21d)
      rsi_14         : RSI(14) — 0-100, overbought/oversold
      beta_60d       : beta vs Ibovespa nos ultimos 60 dias

    Fundamental features (point-in-time, da Fintz):
      pe, pvp        : multiplos de preco
      roe, roic      : retorno sobre capital (eficiencia)
      ev_ebitda      : multiplo enterprise value
      debt_ebitda    : alavancagem
      net_margin     : lucratividade
      revenue_growth : crescimento de receita (YoY)
    """
    ticker: str
    date: datetime

    # Technical
    ret_5d: float | None = None
    ret_21d: float | None = None
    ret_63d: float | None = None
    volatility_21d: float | None = None
    rsi_14: float | None = None
    beta_60d: float | None = None
    volume_ratio_21d: float | None = None  # volume atual / media 21d

    # Fundamental (Fintz PIT)
    pe: float | None = None
    pvp: float | None = None
    roe: float | None = None
    roic: float | None = None
    ev_ebitda: float | None = None
    debt_ebitda: float | None = None
    net_margin: float | None = None
    revenue_growth: float | None = None


@dataclass(frozen=True)
class ReturnForecast:
    """
    Previsao probabilistica de retorno para um ticker.

    p10/p50/p90: percentis da distribuicao de retornos prevista.
    prob_positive: P(retorno > 0) — intuitivo para usuarios.

    horizon_days: horizonte de previsao em dias uteis
      21d ~= 1 mes  (swing trade medio)
      63d ~= 1 tri  (posicao de medio prazo)

    model_version: identificador do modelo treinado (hash do estado).
    """
    ticker: str
    forecast_date: datetime
    horizon_days: int
    p10: float      # pessimista
    p50: float      # mediano
    p90: float      # otimista
    prob_positive: float  # P(ret > 0)
    model_version: str = "lgbm-quantile-v1"

    @property
    def range_80pct(self) -> tuple[float, float]:
        """Intervalo de 80% de confianca (P10..P90)."""
        return (self.p10, self.p90)

    @property
    def upside_pct(self) -> float:
        """Upside do cenario otimista vs mediano."""
        return self.p90 - self.p50

    @property
    def downside_pct(self) -> float:
        """Downside do cenario pessimista vs mediano."""
        return self.p50 - self.p10


@dataclass(frozen=True)
class RiskMetrics:
    """
    Estimativas de risco probabilistico em tres camadas.

    Todas as metricas expressas como retorno negativo (perda esperada).
    Ex: var_95_historical = 0.032 significa perda maxima diaria de 3.2%
    com 95% de confianca (historico simples).

    Camadas:
      historical  : nao-parametrico, robusto, sem suposicoes de distribuicao
      parametric  : t-Student ajustada, captura fat tails melhor que Normal
      garch       : volatilidade condicional (GARCH(1,1)), captura clustering
      monte_carlo : simulacao de 100k cenarios com parametros ajustados
    """
    ticker: str
    date: datetime
    window_days: int

    # Historico
    var_95_historical: float
    cvar_95_historical: float

    # Parametrico (t-Student)
    var_95_parametric: float
    cvar_95_parametric: float
    t_degrees_of_freedom: float  # df ajustado — indica "gordura" das caudas

    # GARCH condicional (None se dados insuficientes)
    var_95_garch: float | None
    cvar_95_garch: float | None
    garch_volatility_forecast: float | None  # vol prevista para amanha

    # Monte Carlo
    var_95_mc: float
    cvar_95_mc: float
    volatility_annual: float

    @property
    def risk_level(self) -> str:
        """
        Classificacao qualitativa baseada no VaR historico diario.
        Usa o historico pois e o mais conservador (sem suposicoes).
        """
        v = abs(self.var_95_historical)
        if v < 0.015:
            return "baixo"
        if v < 0.03:
            return "moderado"
        if v < 0.05:
            return "alto"
        return "muito_alto"

    @property
    def var_consensus(self) -> float:
        """
        VaR consenso: media ponderada das tres estimativas.
        Historico: 40%, Parametrico: 35%, GARCH: 25% (se disponivel).
        """
        if self.var_95_garch is not None:
            return (
                0.40 * self.var_95_historical
                + 0.35 * self.var_95_parametric
                + 0.25 * self.var_95_garch
            )
        return (
            0.55 * self.var_95_historical
            + 0.45 * self.var_95_parametric
        )
