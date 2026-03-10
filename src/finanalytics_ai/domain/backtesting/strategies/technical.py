"""
Estratégias de backtesting: RSI, MACD Crossover, Combinada,
Bollinger Bands, EMA Cross e Momentum.

Cada estratégia implementa o Protocol Strategy:
  - name: str
  - generate_signals(bars) -> list[Signal]

Design decisions:

  RSI Reversal:
    BUY  quando RSI cruza de baixo para cima o nível oversold (ex: 30)
    SELL quando RSI cruza de cima para baixo o nível overbought (ex: 70)
    Lógica de cruzamento (crossover) evita sinal contínuo em zona extrema.

  MACD Crossover:
    BUY  quando MACD line cruza acima da Signal line (bullish crossover)
    SELL quando MACD line cruza abaixo da Signal line (bearish crossover)
    Sinal clássico e amplamente validado na literatura.

  Combined (RSI + MACD):
    BUY  somente se AMBOS dão BUY na mesma barra (AND lógico)
    SELL se QUALQUER um dá SELL (OR lógico — gestão de risco)
    Trade-off: menos trades, maior precisão.

  Sem reentrada imediata:
    Após um SELL, exige novo ciclo antes de reentrar.
    Evita whipsawing em mercados laterais.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from finanalytics_ai.domain.backtesting.engine import Signal
from finanalytics_ai.domain.indicators.technical import (
    compute_bollinger,
    compute_macd,
    compute_rsi,
)


def _compute_ema(values: list[float], period: int) -> list[float | None]:
    """EMA local para EMA Cross e Momentum — evita importar privado."""
    n = len(values)
    result: list[float | None] = [None] * n
    if n < period:
        return result
    k = 2.0 / (period + 1)
    sma = sum(values[:period]) / period
    result[period - 1] = sma
    for i in range(period, n):
        prev = result[i - 1]
        result[i] = values[i] * k + prev * (1 - k)  # type: ignore[operator]
    return result


@dataclass
class RSIStrategy:
    """
    Estratégia de reversão à média baseada em RSI.

    Compra na saída de sobrevenda, vende na entrada de sobrecompra.
    """

    name: str = "RSI Reversal"
    period: int = 14
    oversold: float = 30.0
    overbought: float = 70.0

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        closes = [float(b["close"]) for b in bars]
        result = compute_rsi(closes, self.period)
        rsi = result["values"]
        signals = [Signal.HOLD] * len(bars)

        for i in range(1, len(rsi)):
            prev = rsi[i - 1]
            curr = rsi[i]
            if prev is None or curr is None:
                continue

            # Crossover: de abaixo para acima do nível oversold → BUY
            if prev <= self.oversold and curr > self.oversold:
                signals[i] = Signal.BUY

            # Crossover: de abaixo para acima do nível overbought → SELL
            elif prev <= self.overbought and curr > self.overbought:
                signals[i] = Signal.SELL

        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self.period, "oversold": self.oversold, "overbought": self.overbought}


@dataclass
class MACDCrossStrategy:
    """
    Estratégia de cruzamento MACD/Signal line.

    Crossover bullish (MACD cruza Signal de baixo pra cima) → BUY
    Crossover bearish (MACD cruza Signal de cima pra baixo) → SELL
    """

    name: str = "MACD Crossover"
    fast: int = 12
    slow: int = 26
    signal_period: int = 9

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        closes = [float(b["close"]) for b in bars]
        result = compute_macd(closes, self.fast, self.slow, self.signal_period)
        macd = result["macd"]
        signal = result["signal"]
        signals = [Signal.HOLD] * len(bars)

        for i in range(1, len(bars)):
            m_prev = macd[i - 1]
            m_curr = macd[i]
            s_prev = signal[i - 1]
            s_curr = signal[i]

            if any(v is None for v in [m_prev, m_curr, s_prev, s_curr]):
                continue

            # Bullish crossover: MACD cruza Signal de baixo para cima
            if m_prev <= s_prev and m_curr > s_curr:  # type: ignore[operator]
                signals[i] = Signal.BUY

            # Bearish crossover: MACD cruza Signal de cima para baixo
            elif m_prev >= s_prev and m_curr < s_curr:  # type: ignore[operator]
                signals[i] = Signal.SELL

        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {"fast": self.fast, "slow": self.slow, "signal_period": self.signal_period}


@dataclass
class CombinedStrategy:
    """
    Estratégia combinada RSI + MACD.

    BUY:  RSI dá BUY E MACD dá BUY (confirmação dupla)
    SELL: RSI dá SELL OU MACD dá SELL (saída rápida)

    Reduz operações mas aumenta qualidade dos trades.
    """

    name: str = "RSI + MACD Combined"
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        rsi_strategy = RSIStrategy(
            period=self.rsi_period,
            oversold=self.rsi_oversold,
            overbought=self.rsi_overbought,
        )
        macd_strategy = MACDCrossStrategy(
            fast=self.macd_fast,
            slow=self.macd_slow,
            signal_period=self.macd_signal,
        )

        rsi_signals = rsi_strategy.generate_signals(bars)
        macd_signals = macd_strategy.generate_signals(bars)
        combined = [Signal.HOLD] * len(bars)

        for i in range(len(bars)):
            r = rsi_signals[i]
            m = macd_signals[i]

            if r == Signal.BUY and m == Signal.BUY:
                combined[i] = Signal.BUY
            elif r == Signal.SELL or m == Signal.SELL:
                combined[i] = Signal.SELL

        return combined

    @property
    def params(self) -> dict[str, Any]:
        return {
            "rsi_period": self.rsi_period,
            "rsi_oversold": self.rsi_oversold,
            "rsi_overbought": self.rsi_overbought,
            "macd_fast": self.macd_fast,
            "macd_slow": self.macd_slow,
            "macd_signal": self.macd_signal,
        }


@dataclass
class BollingerBandsStrategy:
    """
    Estrategia de reversao com Bollinger Bands.

    BUY:  preco fecha abaixo da banda inferior (sobrevenda) e na barra
          seguinte fecha de volta acima dela -- confirma reversao.
    SELL: preco fecha acima da banda superior (sobrecompra) e na barra
          seguinte fecha abaixo -- confirma reversao.

    Design decision: espera confirmacao de 1 barra para evitar
    entrar em breakouts falsos no meio de uma tendencia forte.
    """

    period: int = 20
    std_dev: float = 2.0
    name: str = "Bollinger Bands"

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        closes = [float(b["close"]) for b in bars]
        bb = compute_bollinger(closes, self.period, self.std_dev)
        lower = bb["lower"]
        upper = bb["upper"]
        signals = [Signal.HOLD] * len(bars)

        for i in range(1, len(bars)):
            lo_prev = lower[i - 1]
            lo_curr = lower[i]
            up_prev = upper[i - 1]
            up_curr = upper[i]

            if any(v is None for v in [lo_prev, lo_curr, up_prev, up_curr]):
                continue

            p_prev = closes[i - 1]
            p_curr = closes[i]

            # BUY: fechou abaixo da banda inferior na barra anterior
            # e agora fechou acima — reversao confirmada
            if p_prev < lo_prev and p_curr >= lo_curr:  # type: ignore[operator]
                signals[i] = Signal.BUY

            # SELL: fechou acima da banda superior na barra anterior
            # e agora fechou abaixo — reversao confirmada
            elif p_prev > up_prev and p_curr <= up_curr:  # type: ignore[operator]
                signals[i] = Signal.SELL

        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self.period, "std_dev": self.std_dev}


@dataclass
class EMACrossStrategy:
    """
    Estrategia de cruzamento de medias moveis exponenciais (EMA Cross).

    BUY:  EMA rapida cruza acima da EMA lenta (golden cross)
    SELL: EMA rapida cruza abaixo da EMA lenta (death cross)

    Classica estrategia de seguimento de tendencia.
    Defaults: EMA9 x EMA21 — sensiveis o suficiente para dados diarios.

    Design decision: EMA vs SMA — EMA reage mais rapido a mudancas
    recentes de preco, reduzindo o lag inerente de SMA em tendencias fortes.
    Trade-off: mais whipsaws em mercados laterais.
    """

    fast: int = 9
    slow: int = 21
    name: str = "EMA Cross"

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        closes = [float(b["close"]) for b in bars]
        ema_f = _compute_ema(closes, self.fast)
        ema_s = _compute_ema(closes, self.slow)
        signals = [Signal.HOLD] * len(bars)

        for i in range(1, len(bars)):
            f_prev = ema_f[i - 1]
            f_curr = ema_f[i]
            s_prev = ema_s[i - 1]
            s_curr = ema_s[i]

            if any(v is None for v in [f_prev, f_curr, s_prev, s_curr]):
                continue

            # Golden cross: EMA rapida cruza acima da EMA lenta
            if f_prev <= s_prev and f_curr > s_curr:  # type: ignore[operator]
                signals[i] = Signal.BUY

            # Death cross: EMA rapida cruza abaixo da EMA lenta
            elif f_prev >= s_prev and f_curr < s_curr:  # type: ignore[operator]
                signals[i] = Signal.SELL

        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {"fast": self.fast, "slow": self.slow}


@dataclass
class MomentumStrategy:
    """
    Estrategia de momentum baseada em ROC (Rate of Change).

    BUY:  ROC cruza de negativo para positivo (momentum virando para alta)
    SELL: ROC cruza de positivo para negativo (momentum virando para baixa)

    Filtro de RSI opcional: so entra em compra se RSI < filtro_rsi
    (evita comprar em topos de sobrecompra).

    ROC(n) = (close[i] / close[i-n] - 1) * 100

    Design decision: ROC e mais simples e interpretavel que outros
    osciladores de momentum (TRIX, PPO). O cruzamento do zero
    e um sinal limpo de inversao de forca relativa.
    """

    period: int = 10
    rsi_filter: float = 65.0
    name: str = "Momentum (ROC)"

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        closes = [float(b["close"]) for b in bars]
        n = len(closes)
        signals = [Signal.HOLD] * n

        # Calcula ROC
        roc: list[float | None] = [None] * n
        for i in range(self.period, n):
            base = closes[i - self.period]
            if base != 0:
                roc[i] = (closes[i] / base - 1.0) * 100.0

        # Calcula RSI para filtro (se habilitado)
        rsi_values: list[float | None] = [None] * n
        if self.rsi_filter > 0:
            rsi_result = compute_rsi(closes, 14)
            rsi_values = rsi_result["values"]

        for i in range(1, n):
            r_prev = roc[i - 1]
            r_curr = roc[i]
            if r_prev is None or r_curr is None:
                continue

            # BUY: ROC cruza zero de baixo para cima
            if r_prev <= 0 and r_curr > 0:
                # Filtro RSI: nao comprar em sobrecompra
                rsi_ok = True
                if self.rsi_filter > 0 and rsi_values[i] is not None:
                    # Escala o limiar com o período do ROC: quando period > 14 o zero-crossing
                    # do ROC ocorre em fases de preço mais extremas → RSI naturalmente mais alto.
                    effective_filter = self.rsi_filter + max(0.0, (self.period - 14) * 3.0)
                    rsi_ok = rsi_values[i] < effective_filter  # type: ignore[operator]
                if rsi_ok:
                    signals[i] = Signal.BUY

            # SELL: ROC cruza zero de cima para baixo
            elif r_prev >= 0 and r_curr < 0:
                signals[i] = Signal.SELL

        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self.period, "rsi_filter": self.rsi_filter}


# Registro de estratégias disponíveis
STRATEGIES: dict[str, Any] = {
    "rsi": RSIStrategy,
    "macd": MACDCrossStrategy,
    "combined": CombinedStrategy,
    "bollinger": BollingerBandsStrategy,
    "ema_cross": EMACrossStrategy,
    "momentum": MomentumStrategy,
}


def get_strategy(name: str, params: dict[str, Any] | None = None) -> Any:
    """Factory de estratégias. Levanta ValueError se nome desconhecido."""
    cls = STRATEGIES.get(name.lower())
    if cls is None:
        raise ValueError(f"Estratégia '{name}' não encontrada. Disponíveis: {list(STRATEGIES)}")
    return cls(**(params or {}))
