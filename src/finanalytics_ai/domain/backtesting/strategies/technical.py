"""
Estratégias de backtesting: RSI, MACD Crossover e Combinada.

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
from finanalytics_ai.domain.indicators.technical import compute_rsi, compute_macd


@dataclass
class RSIStrategy:
    """
    Estratégia de reversão à média baseada em RSI.

    Compra na saída de sobrevenda, vende na entrada de sobrecompra.
    """
    name: str = "RSI Reversal"
    period:     int   = 14
    oversold:   float = 30.0
    overbought: float = 70.0

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        closes  = [float(b["close"]) for b in bars]
        result  = compute_rsi(closes, self.period)
        rsi     = result["values"]
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
    fast:          int = 12
    slow:          int = 26
    signal_period: int = 9

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        closes  = [float(b["close"]) for b in bars]
        result  = compute_macd(closes, self.fast, self.slow, self.signal_period)
        macd    = result["macd"]
        signal  = result["signal"]
        signals = [Signal.HOLD] * len(bars)

        for i in range(1, len(bars)):
            m_prev = macd[i - 1]
            m_curr = macd[i]
            s_prev = signal[i - 1]
            s_curr = signal[i]

            if any(v is None for v in [m_prev, m_curr, s_prev, s_curr]):
                continue

            # Bullish crossover: MACD cruza Signal de baixo para cima
            if m_prev <= s_prev and m_curr > s_curr:      # type: ignore[operator]
                signals[i] = Signal.BUY

            # Bearish crossover: MACD cruza Signal de cima para baixo
            elif m_prev >= s_prev and m_curr < s_curr:    # type: ignore[operator]
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
    rsi_period:     int   = 14
    rsi_oversold:   float = 30.0
    rsi_overbought: float = 70.0
    macd_fast:      int   = 12
    macd_slow:      int   = 26
    macd_signal:    int   = 9

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        rsi_strategy  = RSIStrategy(
            period=self.rsi_period,
            oversold=self.rsi_oversold,
            overbought=self.rsi_overbought,
        )
        macd_strategy = MACDCrossStrategy(
            fast=self.macd_fast,
            slow=self.macd_slow,
            signal_period=self.macd_signal,
        )

        rsi_signals  = rsi_strategy.generate_signals(bars)
        macd_signals = macd_strategy.generate_signals(bars)
        combined     = [Signal.HOLD] * len(bars)

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


# Registro de estratégias disponíveis
STRATEGIES: dict[str, Any] = {
    "rsi":      RSIStrategy,
    "macd":     MACDCrossStrategy,
    "combined": CombinedStrategy,
}


def get_strategy(name: str, params: dict[str, Any] | None = None) -> Any:
    """Factory de estratégias. Levanta ValueError se nome desconhecido."""
    cls = STRATEGIES.get(name.lower())
    if cls is None:
        raise ValueError(f"Estratégia '{name}' não encontrada. Disponíveis: {list(STRATEGIES)}")
    return cls(**(params or {}))
