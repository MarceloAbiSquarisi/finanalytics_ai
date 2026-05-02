"""
Estratégias de backtesting — 19 estratégias de day trade e swing.

Categorias:
  Existentes:  RSI, MACD, Combined, Bollinger Bands, EMA Cross, Momentum
  Price Action: Pin Bar, Inside Bar, Engulfing, Fakey
  BR Clássicos: Setup 9.1, Larry Williams, Turtle Soup, Hilo Activator
  Trend/Break:  Breakout, Pullback in Trend, First Pullback
  Outros:       Gap and Go, Bollinger Squeeze

Cada estratégia segue o Protocol Strategy:
  - name: str
  - generate_signals(bars) -> list[Signal]

Notas de design:
  - Todas as estratégias de price action usam OHLC (não só close)
  - Nenhuma estratégia usa look-ahead bias
  - Parâmetros expostos via @property params para serialização
  - None em rsi/sma values tratado com guard explícito
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

# ── Helpers privados ──────────────────────────────────────────────────────────


def _ema(values: list[float], period: int) -> list[float | None]:
    """EMA local — evita importar privado do módulo de indicadores."""
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


def _sma(values: list[float], period: int) -> list[float | None]:
    """SMA simples."""
    n = len(values)
    result: list[float | None] = [None] * n
    for i in range(period - 1, n):
        result[i] = sum(values[i - period + 1 : i + 1]) / period
    return result


def _rolling_max(values: list[float], period: int) -> list[float | None]:
    n = len(values)
    result: list[float | None] = [None] * n
    for i in range(period - 1, n):
        result[i] = max(values[i - period + 1 : i + 1])
    return result


def _rolling_min(values: list[float], period: int) -> list[float | None]:
    n = len(values)
    result: list[float | None] = [None] * n
    for i in range(period - 1, n):
        result[i] = min(values[i - period + 1 : i + 1])
    return result


def _safe_ohlc(bar: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """Extrai OHLC com guard para None/0."""
    try:
        o = float(bar.get("open") or bar["close"])
        h = float(bar.get("high") or bar["close"])
        lo = float(bar.get("low") or bar["close"])
        c = float(bar["close"])
        if h < lo:
            h, lo = lo, h
        return o, h, lo, c
    except (KeyError, TypeError, ValueError):
        return None


# ══════════════════════════════════════════════════════════════════════════════
# ESTRATÉGIAS EXISTENTES
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class RSIStrategy:
    """
    Reversão à média baseada em RSI.

    BUY  quando RSI cruza de baixo para cima o nível oversold (ex: 30)
    SELL quando RSI cruza de cima para baixo o nível overbought (ex: 70)
    """

    name: str = "RSI Reversal"
    period: int = 14
    oversold: float = 30.0
    overbought: float = 70.0

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        closes = [float(b["close"]) for b in bars]
        rsi = compute_rsi(closes, self.period)["values"]
        signals = [Signal.HOLD] * len(bars)
        for i in range(1, len(rsi)):
            prev, curr = rsi[i - 1], rsi[i]
            if prev is None or curr is None:
                continue
            if prev <= self.oversold and curr > self.oversold:
                signals[i] = Signal.BUY
            elif prev <= self.overbought and curr > self.overbought:
                signals[i] = Signal.SELL
        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self.period, "oversold": self.oversold, "overbought": self.overbought}

    def generate_scores(self, bars: list[dict[str, Any]]) -> list[float | None]:
        """Score de convicção p/ ROC/AUC — distância do RSI ao 50 normalizada
        ao intervalo de threshold. Quanto mais profundo o oversold, mais alto
        o score (mais convicção de reversão alta = trade rentável)."""
        closes = [float(b["close"]) for b in bars]
        rsi = compute_rsi(closes, self.period)["values"]
        scores: list[float | None] = []
        for v in rsi:
            if v is None:
                scores.append(None)
            else:
                # Score positivo = oversold profundo (BUY de convicção)
                # Score negativo = overbought (SELL ou contra-sinal)
                # Normaliza pelo range do threshold (default 0..30 = 30 pontos)
                scores.append((self.oversold - float(v)) / max(self.oversold, 1.0))
        return scores


@dataclass
class MACDCrossStrategy:
    """
    Cruzamento MACD/Signal line.

    BUY  quando MACD cruza Signal de baixo pra cima (bullish crossover)
    SELL quando MACD cruza Signal de cima pra baixo (bearish crossover)
    """

    name: str = "MACD Crossover"
    fast: int = 12
    slow: int = 26
    signal_period: int = 9

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        closes = [float(b["close"]) for b in bars]
        result = compute_macd(closes, self.fast, self.slow, self.signal_period)
        macd, signal = result["macd"], result["signal"]
        signals = [Signal.HOLD] * len(bars)
        for i in range(1, len(bars)):
            m_p, m_c = macd[i - 1], macd[i]
            s_p, s_c = signal[i - 1], signal[i]
            if any(v is None for v in [m_p, m_c, s_p, s_c]):
                continue
            if m_p <= s_p and m_c > s_c:  # type: ignore[operator]
                signals[i] = Signal.BUY
            elif m_p >= s_p and m_c < s_c:  # type: ignore[operator]
                signals[i] = Signal.SELL
        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {"fast": self.fast, "slow": self.slow, "signal_period": self.signal_period}

    def generate_scores(self, bars: list[dict[str, Any]]) -> list[float | None]:
        """Score = MACD histogram (macd - signal). Quanto maior, mais
        bullish; convicção de trade rentável proporcional à magnitude."""
        closes = [float(b["close"]) for b in bars]
        result = compute_macd(closes, self.fast, self.slow, self.signal_period)
        macd, sig = result["macd"], result["signal"]
        scores: list[float | None] = []
        for m, s in zip(macd, sig, strict=False):
            if m is None or s is None:
                scores.append(None)
            else:
                scores.append(float(m) - float(s))
        return scores


@dataclass
class CombinedStrategy:
    """
    RSI + MACD combinados.

    BUY:  ambos sinalizam BUY (confirmação dupla)
    SELL: qualquer um sinaliza SELL (saída rápida)
    """

    name: str = "RSI + MACD Combined"
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        rsi_s = RSIStrategy(
            period=self.rsi_period, oversold=self.rsi_oversold, overbought=self.rsi_overbought
        )
        macd_s = MACDCrossStrategy(
            fast=self.macd_fast, slow=self.macd_slow, signal_period=self.macd_signal
        )
        rs = rsi_s.generate_signals(bars)
        ms = macd_s.generate_signals(bars)
        out = [Signal.HOLD] * len(bars)
        for i in range(len(bars)):
            if rs[i] == Signal.BUY and ms[i] == Signal.BUY:
                out[i] = Signal.BUY
            elif rs[i] == Signal.SELL or ms[i] == Signal.SELL:
                out[i] = Signal.SELL
        return out

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
    Reversão com Bollinger Bands.

    BUY:  fecha abaixo da banda inferior → volta acima (confirmação 1 barra)
    SELL: fecha acima da banda superior → volta abaixo
    """

    period: int = 20
    std_dev: float = 2.0
    name: str = "Bollinger Bands"

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        closes = [float(b["close"]) for b in bars]
        bb = compute_bollinger(closes, self.period, self.std_dev)
        lower, upper = bb["lower"], bb["upper"]
        signals = [Signal.HOLD] * len(bars)
        for i in range(1, len(bars)):
            lo_p, lo_c = lower[i - 1], lower[i]
            up_p, up_c = upper[i - 1], upper[i]
            if any(v is None for v in [lo_p, lo_c, up_p, up_c]):
                continue
            p_p, p_c = closes[i - 1], closes[i]
            if p_p < lo_p and p_c >= lo_c:  # type: ignore[operator]
                signals[i] = Signal.BUY
            elif p_p > up_p and p_c <= up_c:  # type: ignore[operator]
                signals[i] = Signal.SELL
        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self.period, "std_dev": self.std_dev}


@dataclass
class EMACrossStrategy:
    """
    Cruzamento de EMAs (golden/death cross).

    BUY:  EMA rápida cruza acima da EMA lenta
    SELL: EMA rápida cruza abaixo da EMA lenta
    """

    fast: int = 9
    slow: int = 21
    name: str = "EMA Cross"

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        closes = [float(b["close"]) for b in bars]
        ema_f = _ema(closes, self.fast)
        ema_s = _ema(closes, self.slow)
        signals = [Signal.HOLD] * len(bars)
        for i in range(1, len(bars)):
            f_p, f_c = ema_f[i - 1], ema_f[i]
            s_p, s_c = ema_s[i - 1], ema_s[i]
            if any(v is None for v in [f_p, f_c, s_p, s_c]):
                continue
            if f_p <= s_p and f_c > s_c:  # type: ignore[operator]
                signals[i] = Signal.BUY
            elif f_p >= s_p and f_c < s_c:  # type: ignore[operator]
                signals[i] = Signal.SELL
        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {"fast": self.fast, "slow": self.slow}


@dataclass
class MomentumStrategy:
    """
    Momentum via ROC (Rate of Change).

    BUY:  ROC cruza zero de baixo para cima
    SELL: ROC cruza zero de cima para baixo
    """

    period: int = 10
    rsi_filter: float = 65.0
    name: str = "Momentum (ROC)"

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        closes = [float(b["close"]) for b in bars]
        n = len(closes)
        signals = [Signal.HOLD] * n
        roc: list[float | None] = [None] * n
        for i in range(self.period, n):
            base = closes[i - self.period]
            if base != 0:
                roc[i] = (closes[i] / base - 1.0) * 100.0
        rsi_values: list[float | None] = [None] * n
        if self.rsi_filter > 0:
            rsi_values = compute_rsi(closes, 14)["values"]
        for i in range(1, n):
            r_p, r_c = roc[i - 1], roc[i]
            if r_p is None or r_c is None:
                continue
            if r_p <= 0 and r_c > 0:
                rsi_ok = True
                if self.rsi_filter > 0 and rsi_values[i] is not None:
                    effective = self.rsi_filter + max(0.0, (self.period - 14) * 3.0)
                    rsi_ok = rsi_values[i] < effective  # type: ignore[operator]
                if rsi_ok:
                    signals[i] = Signal.BUY
            elif r_p >= 0 and r_c < 0:
                signals[i] = Signal.SELL
        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self.period, "rsi_filter": self.rsi_filter}


# ══════════════════════════════════════════════════════════════════════════════
# PRICE ACTION
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class PinBarStrategy:
    """
    Rejeição de nível por Pin Bar (Hammer/Shooting Star).

    Bullish Pin Bar (BUY):
      - Pavio inferior >= wick_ratio do range total
      - Corpo no terço superior da barra
      - Pavio superior < 25% do range

    Bearish Pin Bar (SELL):
      - Pavio superior >= wick_ratio do range total
      - Corpo no terço inferior
      - Pavio inferior < 25% do range

    Filtro de tendência opcional: EMA slow confirma direção.
    """

    name: str = "Pin Bar"
    wick_ratio: float = 0.6  # pavio mínimo como fração do range
    trend_filter: bool = True  # confirmar direção via EMA50
    trend_period: int = 50

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        n = len(bars)
        signals = [Signal.HOLD] * n
        closes = [float(b["close"]) for b in bars]
        ema_trend = _ema(closes, self.trend_period) if self.trend_filter else [None] * n

        for i in range(1, n):
            ohlc = _safe_ohlc(bars[i])
            if ohlc is None:
                continue
            o, h, lo, c = ohlc
            rng = h - lo
            if rng < 1e-8:
                continue

            body = abs(c - o)
            upper_wick = h - max(o, c)
            lower_wick = min(o, c) - lo

            # Bullish pin bar
            bullish_pin = (
                lower_wick >= self.wick_ratio * rng
                and upper_wick <= 0.25 * rng
                and body <= 0.35 * rng
            )
            # Bearish pin bar
            bearish_pin = (
                upper_wick >= self.wick_ratio * rng
                and lower_wick <= 0.25 * rng
                and body <= 0.35 * rng
            )

            trend = ema_trend[i]
            if bullish_pin:
                if not self.trend_filter or trend is None or c >= trend:
                    signals[i] = Signal.BUY
            elif bearish_pin:
                if not self.trend_filter or trend is None or c <= trend:
                    signals[i] = Signal.SELL

        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {
            "wick_ratio": self.wick_ratio,
            "trend_filter": self.trend_filter,
            "trend_period": self.trend_period,
        }


@dataclass
class InsideBarStrategy:
    """
    Compressão de volatilidade por Inside Bar.

    Inside Bar: máxima e mínima dentro da barra anterior (candle mãe).
    BUY:  rompimento acima da máxima da barra mãe
    SELL: rompimento abaixo da mínima da barra mãe

    Implementação: sinal gerado quando o rompimento ocorre
    (barra i+1 fecha acima/abaixo após inside bar na barra i).
    """

    name: str = "Inside Bar"
    trend_filter: bool = True
    trend_period: int = 21

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        n = len(bars)
        signals = [Signal.HOLD] * n
        closes = [float(b["close"]) for b in bars]
        ema_trend = _ema(closes, self.trend_period) if self.trend_filter else [None] * n
        inside_flags = [False] * n  # marca inside bars

        for i in range(1, n):
            curr = _safe_ohlc(bars[i])
            prev = _safe_ohlc(bars[i - 1])
            if curr is None or prev is None:
                continue
            _, h, lo, _ = curr
            _, ph, plo, _ = prev
            if h < ph and lo > plo:
                inside_flags[i] = True

        for i in range(2, n):
            if not inside_flags[i - 1]:
                continue
            # barra mãe é i-2
            mom = _safe_ohlc(bars[i - 2])
            curr = _safe_ohlc(bars[i])
            if mom is None or curr is None:
                continue
            _, mh, mlo, _ = mom
            _, _, _, c = curr
            trend = ema_trend[i]

            if c > mh:
                if not self.trend_filter or trend is None or c >= trend:
                    signals[i] = Signal.BUY
            elif c < mlo:
                if not self.trend_filter or trend is None or c <= trend:
                    signals[i] = Signal.SELL

        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {"trend_filter": self.trend_filter, "trend_period": self.trend_period}


@dataclass
class EngulfingStrategy:
    """
    Padrão de Engulfing (absorção).

    Bullish Engulfing (BUY):
      - Barra anterior: bearish (close < open)
      - Barra atual:    bullish, open <= prev.close, close >= prev.open
      - Corpo atual > corpo anterior

    Bearish Engulfing (SELL): inverso.

    Filtro de volume opcional: confirma se volume > média.
    """

    name: str = "Engulfing"
    body_ratio: float = 1.1  # corpo atual deve ser >= X × corpo anterior
    volume_filter: bool = False
    volume_period: int = 20

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        n = len(bars)
        signals = [Signal.HOLD] * n
        vols = [float(b.get("volume") or 0) for b in bars]
        vol_ma = _sma(vols, self.volume_period) if self.volume_filter else [None] * n

        for i in range(1, n):
            curr = _safe_ohlc(bars[i])
            prev = _safe_ohlc(bars[i - 1])
            if curr is None or prev is None:
                continue
            o, h, lo, c = curr
            po, ph, plo, pc = prev
            curr_body = abs(c - o)
            prev_body = abs(pc - po)
            if prev_body < 1e-8:
                continue

            vol_ok = True
            if self.volume_filter and vol_ma[i] is not None:
                vol_ok = vols[i] >= vol_ma[i]  # type: ignore[operator]

            # Bullish engulfing
            if (
                pc < po
                and c > o
                and o <= pc
                and c >= po
                and curr_body >= self.body_ratio * prev_body
                and vol_ok
            ):
                signals[i] = Signal.BUY

            # Bearish engulfing
            elif (
                pc > po
                and c < o
                and o >= pc
                and c <= po
                and curr_body >= self.body_ratio * prev_body
                and vol_ok
            ):
                signals[i] = Signal.SELL

        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {"body_ratio": self.body_ratio, "volume_filter": self.volume_filter}


@dataclass
class FakeyStrategy:
    """
    Fakey (False Breakout Reversal).

    1. Identifica um Inside Bar
    2. Barra seguinte rompe acima/abaixo do range da barra mãe (falso rompimento)
    3. Barra após isso reverte e fecha DENTRO do range original

    BUY:  falso rompimento para baixo seguido de reversão
    SELL: falso rompimento para cima seguido de reversão

    É um dos setups mais confiáveis de price action — combina compressão,
    falsa ruptura e absorção em apenas 3 barras.
    """

    name: str = "Fakey (False Breakout)"
    confirm_bars: int = 1  # barras para confirmar reversão

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        n = len(bars)
        signals = [Signal.HOLD] * n

        for i in range(2, n - self.confirm_bars):
            # barra mãe = i-2, inside bar = i-1, falso rompimento = i
            mom = _safe_ohlc(bars[i - 2])
            ib = _safe_ohlc(bars[i - 1])
            brk = _safe_ohlc(bars[i])
            if mom is None or ib is None or brk is None:
                continue

            _, mh, mlo, _ = mom
            _, ih, ilo, _ = ib
            _, bh, blo, bc = brk

            # Verifica que i-1 é inside bar
            if not (ih < mh and ilo > mlo):
                continue

            # Falso rompimento para cima → SELL (close volta dentro do range)
            if bh > mh and bc <= mh:
                conf_idx = i + self.confirm_bars
                if conf_idx < n:
                    conf = _safe_ohlc(bars[conf_idx])
                    if conf is not None and conf[3] < bc:
                        signals[conf_idx] = Signal.SELL

            # Falso rompimento para baixo → BUY
            elif blo < mlo and bc >= mlo:
                conf_idx = i + self.confirm_bars
                if conf_idx < n:
                    conf = _safe_ohlc(bars[conf_idx])
                    if conf is not None and conf[3] > bc:
                        signals[conf_idx] = Signal.BUY

        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {"confirm_bars": self.confirm_bars}


# ══════════════════════════════════════════════════════════════════════════════
# SETUPS CLÁSSICOS BRASILEIROS
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class Setup91Strategy:
    """
    Setup 9.1 — Alexandre Wolwacz (Stormer).

    Contexto de tendência: EMA9 > EMA21 (alta) ou < (baixa).
    Gatilho: barra de sinal fecha acima da máxima da barra anterior (BUY)
             ou abaixo da mínima anterior (SELL).

    É o setup mais ensinado no Brasil — simples, objetivo, replicável.
    A versão aqui adiciona filtro de RSI para evitar entradas em sobrecompra.
    """

    name: str = "Setup 9.1 (Stormer)"
    fast_period: int = 9
    slow_period: int = 21
    rsi_filter: float = 70.0  # não compra se RSI > este valor

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        n = len(bars)
        signals = [Signal.HOLD] * n
        closes = [float(b["close"]) for b in bars]
        ema9 = _ema(closes, self.fast_period)
        ema21 = _ema(closes, self.slow_period)
        rsi_vals = compute_rsi(closes, 14)["values"]

        for i in range(1, n):
            e9, e21 = ema9[i], ema21[i]
            if e9 is None or e21 is None:
                continue

            curr = _safe_ohlc(bars[i])
            prev = _safe_ohlc(bars[i - 1])
            if curr is None or prev is None:
                continue

            _, ch, clo, cc = curr
            _, ph, plo, pc = prev
            rsi = rsi_vals[i]

            # Uptrend: EMA9 > EMA21, fecha acima da máxima anterior
            if e9 > e21 and cc > ph:
                rsi_ok = rsi is None or rsi < self.rsi_filter
                if rsi_ok:
                    signals[i] = Signal.BUY

            # Downtrend: EMA9 < EMA21, fecha abaixo da mínima anterior
            elif e9 < e21 and cc < plo:
                signals[i] = Signal.SELL

        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
            "rsi_filter": self.rsi_filter,
        }


@dataclass
class LarryWilliamsStrategy:
    """
    Setup Larry Williams — compra na mínima anterior em uptrend.

    Em tendência de alta (EMA rápida > EMA lenta):
      BUY quando o preço toca ou vai abaixo da mínima da barra anterior
      e fecha acima dela (reversão intraday confirmada no fechamento).

    Lógica: o mercado "testa" o suporte da barra anterior e rejeita.
    Operacionalmente: entra no fechamento, stop na mínima da barra atual.
    """

    name: str = "Larry Williams"
    trend_fast: int = 9
    trend_slow: int = 21
    lookback: int = 1  # barras atrás para buscar a mínima de referência

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        n = len(bars)
        signals = [Signal.HOLD] * n
        closes = [float(b["close"]) for b in bars]
        ema_f = _ema(closes, self.trend_fast)
        ema_s = _ema(closes, self.trend_slow)

        for i in range(self.lookback + 1, n):
            ef, es = ema_f[i], ema_s[i]
            if ef is None or es is None:
                continue

            curr = _safe_ohlc(bars[i])
            ref = _safe_ohlc(bars[i - self.lookback])
            if curr is None or ref is None:
                continue

            _, _, curr_lo, curr_c = curr
            _, _, ref_lo, _ = ref

            # BUY: uptrend + baixou até/abaixo da mínima anterior + fechou acima dela
            if ef > es and curr_lo <= ref_lo and curr_c > ref_lo:
                signals[i] = Signal.BUY

            # SELL: downtrend + subiu até/acima da máxima anterior + fechou abaixo dela
            elif ef < es:
                _, ref_h, _, _ = ref
                _, _, _, curr_c2 = curr
                _, curr_h, _, _ = curr
                if curr_h >= ref_h and curr_c2 < ref_h:
                    signals[i] = Signal.SELL

        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {
            "trend_fast": self.trend_fast,
            "trend_slow": self.trend_slow,
            "lookback": self.lookback,
        }


@dataclass
class TurtleSoupStrategy:
    """
    Turtle Soup — Linda Bradford Raschke.

    Operação contrária ao rompimento de N-period high/low.

    BUY:  price bate nova mínima de N períodos, depois reverte acima dela
          (stop hunt: os turtles compraram a mínima anterior, são stopados,
          a reversão pega o breakout falso)
    SELL: inverso com nova máxima de N períodos

    Muito eficaz em mercados com alto algoritmo de breakout — onde
    o mercado "faz" a mínima só para buscar stops e então reverte.
    """

    name: str = "Turtle Soup"
    lookback: int = 20  # período para máxima/mínima
    confirm_bars: int = 2  # barras para confirmar reversão após falso break

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        n = len(bars)
        signals = [Signal.HOLD] * n
        highs = []
        lows = []
        closes = []
        for b in bars:
            ohlc = _safe_ohlc(b)
            if ohlc:
                _, h, lo, c = ohlc
                highs.append(h)
                lows.append(lo)
                closes.append(c)
            else:
                highs.append(float(b["close"]))
                lows.append(float(b["close"]))
                closes.append(float(b["close"]))

        roll_max = _rolling_max(highs, self.lookback)
        roll_min = _rolling_min(lows, self.lookback)

        for i in range(self.lookback, n - self.confirm_bars):
            if roll_max[i] is None or roll_min[i] is None:
                continue

            curr = _safe_ohlc(bars[i])
            if curr is None:
                continue
            _, ch, clo, cc = curr

            # Falsa nova mínima → BUY
            prev_min = roll_min[i - 1] if i > 0 else roll_min[i]
            if prev_min is not None and clo < prev_min and cc > prev_min:
                # confirma reversão: fecha acima da mínima anterior
                confirm = _safe_ohlc(bars[i + self.confirm_bars])
                if confirm and confirm[3] > cc:
                    signals[i + self.confirm_bars] = Signal.BUY

            # Falsa nova máxima → SELL
            prev_max = roll_max[i - 1] if i > 0 else roll_max[i]
            if prev_max is not None and ch > prev_max and cc < prev_max:
                confirm = _safe_ohlc(bars[i + self.confirm_bars])
                if confirm and confirm[3] < cc:
                    signals[i + self.confirm_bars] = Signal.SELL

        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {"lookback": self.lookback, "confirm_bars": self.confirm_bars}


@dataclass
class HiloActivatorStrategy:
    """
    Hilo Activator — Alexandre Elder / adaptação B3.

    Hilo = média entre MA(máximas, period) e MA(mínimas, period).
    BUY  quando close cruza acima do Hilo
    SELL quando close cruza abaixo do Hilo

    Funciona como trailing stop dinâmico e gerador de sinal simultâneo.
    Muito usado na B3 combinado com Elder Impulse System.
    """

    name: str = "Hilo Activator"
    period: int = 8

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        n = len(bars)
        signals = [Signal.HOLD] * n
        closes = [float(b["close"]) for b in bars]
        highs = []
        lows = []
        for b in bars:
            ohlc = _safe_ohlc(b)
            if ohlc:
                _, h, lo, _ = ohlc
                highs.append(h)
                lows.append(lo)
            else:
                highs.append(float(b["close"]))
                lows.append(float(b["close"]))

        ma_high = _sma(highs, self.period)
        ma_low = _sma(lows, self.period)

        hilo: list[float | None] = [
            (mh + ml) / 2 if mh is not None and ml is not None else None
            for mh, ml in zip(ma_high, ma_low)
        ]

        for i in range(1, n):
            h_prev, h_curr = hilo[i - 1], hilo[i]
            if h_prev is None or h_curr is None:
                continue
            c_prev, c_curr = closes[i - 1], closes[i]
            # Crossover: close cruza acima do Hilo
            if c_prev <= h_prev and c_curr > h_curr:
                signals[i] = Signal.BUY
            elif c_prev >= h_prev and c_curr < h_curr:
                signals[i] = Signal.SELL

        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self.period}


# ══════════════════════════════════════════════════════════════════════════════
# TREND / BREAKOUT
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class BreakoutStrategy:
    """
    Rompimento de Range — Donchian Channel.

    BUY:  close supera a máxima dos últimos N períodos
    SELL: close rompe abaixo da mínima dos últimos N períodos

    Filtro de ATR: evita entrar em rompimentos de baixa volatilidade
    (rompimentos falsos geralmente têm ATR menor).
    """

    name: str = "Breakout Range"
    period: int = 20
    atr_filter: bool = True
    atr_period: int = 14
    atr_multiplier: float = 0.5  # rompimento deve ter range >= X * ATR

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        n = len(bars)
        signals = [Signal.HOLD] * n
        closes = [float(b["close"]) for b in bars]
        highs, lows = [], []
        for b in bars:
            ohlc = _safe_ohlc(b)
            highs.append(ohlc[1] if ohlc else float(b["close"]))
            lows.append(ohlc[2] if ohlc else float(b["close"]))

        roll_max = _rolling_max(highs, self.period)
        roll_min = _rolling_min(lows, self.period)

        # ATR
        atr: list[float | None] = [None] * n
        if self.atr_filter:
            trs = []
            for i in range(1, n):
                h, lo, pc = highs[i], lows[i], closes[i - 1]
                trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
            atr_ma = _sma(trs, self.atr_period)
            for i in range(1, n):
                atr[i] = atr_ma[i - 1] if i - 1 < len(atr_ma) else None

        for i in range(1, n):
            rm = roll_max[i - 1]
            rmi = roll_min[i - 1]
            if rm is None or rmi is None:
                continue
            c = closes[i]
            bar_range = highs[i] - lows[i]
            atr_val = atr[i]
            atr_ok = (
                not self.atr_filter or atr_val is None or bar_range >= self.atr_multiplier * atr_val
            )

            if c > rm and atr_ok:
                signals[i] = Signal.BUY
            elif c < rmi and atr_ok:
                signals[i] = Signal.SELL

        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "atr_filter": self.atr_filter,
            "atr_multiplier": self.atr_multiplier,
        }


@dataclass
class PullbackTrendStrategy:
    """
    Pullback em tendência — o setup mais confiável do day trade.

    Tendência:  EMA rápida > EMA lenta
    Pullback:   RSI recua para zona de neutralidade (padrão: 40-50)
    Gatilho:    RSI cruza acima do threshold de retomada (padrão: 50)

    BUY:  uptrend + RSI recuou para zona neutra + voltou acima de 50
    SELL: downtrend + RSI subiu para zona neutra + voltou abaixo de 50

    Design: evita entrar no topo (RSI >70) ou no fundo (RSI<30) de pullback.
    A zona de oportunidade é o meio: RSI entre 40 e 60.
    """

    name: str = "Pullback in Trend"
    trend_fast: int = 9
    trend_slow: int = 21
    rsi_period: int = 14
    pullback_low: float = 40.0  # RSI deve tocar abaixo disto (uptrend)
    pullback_high: float = 60.0  # RSI deve tocar acima disto (downtrend)
    resume_up: float = 50.0  # cruzar acima → BUY
    resume_down: float = 50.0  # cruzar abaixo → SELL

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        n = len(bars)
        signals = [Signal.HOLD] * n
        closes = [float(b["close"]) for b in bars]
        ema_f = _ema(closes, self.trend_fast)
        ema_s = _ema(closes, self.trend_slow)
        rsi_vals = compute_rsi(closes, self.rsi_period)["values"]

        pullback_seen_up = False  # RSI tocou zona de pullback em uptrend
        pullback_seen_dn = False

        for i in range(1, n):
            ef, es = ema_f[i], ema_s[i]
            r_prev, r_curr = rsi_vals[i - 1], rsi_vals[i]
            if ef is None or es is None or r_prev is None or r_curr is None:
                continue

            uptrend = ef > es
            downtrend = ef < es

            # Monitora pullback em uptrend
            if uptrend:
                if r_curr <= self.pullback_low:
                    pullback_seen_up = True
                if pullback_seen_up and r_prev <= self.resume_up and r_curr > self.resume_up:
                    signals[i] = Signal.BUY
                    pullback_seen_up = False
            else:
                pullback_seen_up = False

            # Monitora pullback em downtrend
            if downtrend:
                if r_curr >= self.pullback_high:
                    pullback_seen_dn = True
                if pullback_seen_dn and r_prev >= self.resume_down and r_curr < self.resume_down:
                    signals[i] = Signal.SELL
                    pullback_seen_dn = False
            else:
                pullback_seen_dn = False

        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {
            "trend_fast": self.trend_fast,
            "trend_slow": self.trend_slow,
            "rsi_period": self.rsi_period,
            "pullback_low": self.pullback_low,
            "pullback_high": self.pullback_high,
        }


@dataclass
class FirstPullbackStrategy:
    """
    First Pullback — primeira retração após rompimento forte.

    Identifica uma barra de força (corpo > X% do range, na direção):
    BUY:  barra bullish forte → próxima retração (bar recua) → fecha acima da EMA
    SELL: barra bearish forte → próxima retração (bar avança) → fecha abaixo da EMA

    O "primeiro pullback" após um breakout é geralmente o melhor ponto
    de entrada para quem perdeu o rompimento original.
    """

    name: str = "First Pullback"
    strength_ratio: float = 0.6  # corpo deve ser >= X do range total
    ema_period: int = 9  # EMA de suporte/resistência
    max_pullback_bars: int = 3  # máximo de barras de pullback esperado

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        n = len(bars)
        signals = [Signal.HOLD] * n
        closes = [float(b["close"]) for b in bars]
        ema = _ema(closes, self.ema_period)

        strong_up_idx = -999
        strong_dn_idx = -999

        for i in range(1, n):
            curr = _safe_ohlc(bars[i])
            e = ema[i]
            if curr is None or e is None:
                continue
            o, h, lo, c = curr
            rng = h - lo
            body = abs(c - o)

            # Identifica barra forte
            if rng > 0 and body / rng >= self.strength_ratio:
                if c > o:  # bullish forte
                    strong_up_idx = i
                else:  # bearish forte
                    strong_dn_idx = i

            # First pullback após barra bullish forte
            if 1 <= i - strong_up_idx <= self.max_pullback_bars:
                if c >= e and c < closes[strong_up_idx]:  # recuou mas acima da EMA
                    prev_c = closes[i - 1]
                    if prev_c < c:  # fechamento em recuperação
                        signals[i] = Signal.BUY
                        strong_up_idx = -999

            # First pullback após barra bearish forte
            if 1 <= i - strong_dn_idx <= self.max_pullback_bars:
                if c <= e and c > closes[strong_dn_idx]:
                    prev_c = closes[i - 1]
                    if prev_c > c:
                        signals[i] = Signal.SELL
                        strong_dn_idx = -999

        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {"strength_ratio": self.strength_ratio, "ema_period": self.ema_period}


# ══════════════════════════════════════════════════════════════════════════════
# OUTROS
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class GapAndGoStrategy:
    """
    Gap and Go — continuação de gap na direção da abertura.

    BUY:  gap de alta (open > prev.close * (1 + gap_pct/100)) + fecha acima da abertura
    SELL: gap de baixa + fecha abaixo da abertura

    Em dados diários, "abertura" = open da barra. O gap é medido
    entre o close anterior e o open atual.

    Filtro de volume: gaps com volume acima da média são mais confiáveis.
    """

    name: str = "Gap and Go"
    gap_pct: float = 0.5  # gap mínimo em % (0.5 = 0.5%)
    volume_filter: bool = True
    volume_period: int = 20

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        n = len(bars)
        signals = [Signal.HOLD] * n
        vols = [float(b.get("volume") or 0) for b in bars]
        vol_ma = _sma(vols, self.volume_period)

        for i in range(1, n):
            curr = _safe_ohlc(bars[i])
            prev = _safe_ohlc(bars[i - 1])
            if curr is None or prev is None:
                continue
            o, h, lo, c = curr
            _, _, _, pc = prev
            if pc == 0:
                continue

            gap_up_pct = (o - pc) / pc * 100
            gap_dn_pct = (pc - o) / pc * 100

            vol_ok = True
            if self.volume_filter and vol_ma[i] is not None:
                vol_ok = vols[i] >= vol_ma[i]  # type: ignore[operator]

            if gap_up_pct >= self.gap_pct and c > o and vol_ok:
                signals[i] = Signal.BUY
            elif gap_dn_pct >= self.gap_pct and c < o and vol_ok:
                signals[i] = Signal.SELL

        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {"gap_pct": self.gap_pct, "volume_filter": self.volume_filter}


@dataclass
class BollingerSqueezeStrategy:
    """
    Bollinger Squeeze — contração extrema seguida de expansão.

    Squeeze: banda de Bollinger se estreita abaixo do threshold
             (bandwidth = (upper-lower)/middle < squeeze_threshold)

    Após o squeeze, opera na direção da expansão:
    BUY:  close cruza acima da banda superior após squeeze
    SELL: close cruza abaixo da banda inferior após squeeze

    Este é o setup de volatility breakout mais usado no mercado quantitativo.
    O squeeze é o prelúdio, a expansão é o sinal.
    """

    name: str = "Bollinger Squeeze"
    period: int = 20
    std_dev: float = 2.0
    squeeze_threshold: float = 0.05  # bandwidth < 5% → squeeze
    lookback_squeeze: int = 5  # squeeze deve durar ao menos N barras

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        n = len(bars)
        signals = [Signal.HOLD] * n
        closes = [float(b["close"]) for b in bars]
        bb = compute_bollinger(closes, self.period, self.std_dev)
        upper, lower, middle = bb["upper"], bb["lower"], bb["middle"]

        # Calcula bandwidth
        bw: list[float | None] = []
        for u, lo, m in zip(upper, lower, middle):
            if u is None or lo is None or m is None or m == 0:
                bw.append(None)
            else:
                bw.append((u - lo) / m)

        for i in range(self.lookback_squeeze + 1, n):
            if upper[i] is None or lower[i] is None or bw[i] is None:
                continue

            # Verifica squeeze nas últimas N barras
            squeeze_bars = sum(
                1
                for j in range(i - self.lookback_squeeze, i)
                if bw[j] is not None and bw[j] < self.squeeze_threshold  # type: ignore[operator]
            )
            in_squeeze = squeeze_bars >= self.lookback_squeeze

            if not in_squeeze:
                continue

            c = closes[i]
            c_prev = closes[i - 1]

            # Expansão para cima
            if upper[i] is not None and c_prev <= upper[i - 1] and c > upper[i]:  # type: ignore[operator]
                signals[i] = Signal.BUY

            # Expansão para baixo
            elif lower[i] is not None and c_prev >= lower[i - 1] and c < lower[i]:  # type: ignore[operator]
                signals[i] = Signal.SELL

        return signals

    @property
    def params(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "std_dev": self.std_dev,
            "squeeze_threshold": self.squeeze_threshold,
            "lookback_squeeze": self.lookback_squeeze,
        }


# ══════════════════════════════════════════════════════════════════════════════
# REGISTRO E FACTORY
# ══════════════════════════════════════════════════════════════════════════════

STRATEGIES: dict[str, Any] = {
    # Existentes
    "rsi": RSIStrategy,
    "macd": MACDCrossStrategy,
    "combined": CombinedStrategy,
    "bollinger": BollingerBandsStrategy,
    "ema_cross": EMACrossStrategy,
    "momentum": MomentumStrategy,
    # Price Action
    "pin_bar": PinBarStrategy,
    "inside_bar": InsideBarStrategy,
    "engulfing": EngulfingStrategy,
    "fakey": FakeyStrategy,
    # BR Clássicos
    "setup_91": Setup91Strategy,
    "larry_williams": LarryWilliamsStrategy,
    "turtle_soup": TurtleSoupStrategy,
    "hilo": HiloActivatorStrategy,
    # Trend / Breakout
    "breakout": BreakoutStrategy,
    "pullback_trend": PullbackTrendStrategy,
    "first_pullback": FirstPullbackStrategy,
    # Outros
    "gap_and_go": GapAndGoStrategy,
    "bollinger_squeeze": BollingerSqueezeStrategy,
}


def get_strategy(name: str, params: dict[str, Any] | None = None) -> Any:
    """Factory de estratégias. Levanta ValueError se nome desconhecido."""
    cls = STRATEGIES.get(name.lower())
    if cls is None:
        raise ValueError(
            f"Estratégia '{name}' não encontrada. Disponíveis: {sorted(STRATEGIES.keys())}"
        )
    return cls(**(params or {}))
