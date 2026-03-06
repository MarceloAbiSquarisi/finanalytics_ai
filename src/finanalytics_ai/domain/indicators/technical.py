"""
Indicadores técnicos — implementação em Python puro.

Design decisions:
  - Zero dependências externas (sem pandas/numpy/ta-lib)
  - Funções puras: list[float] in → list[float | None] out
  - None para períodos de aquecimento (warmup) — frontend omite esses pontos
  - Algoritmos canônicos: Wilder smoothing para RSI, EMA para MACD/Bollinger
  - Tipagem estática rigorosa via TypeAlias e TypedDict

Trade-off vs pandas:
  Pandas seria mais rápido em listas longas (>5000 pontos), mas adiciona
  ~50MB de dependência. Para o caso de uso (até ~600 barras de histórico)
  a diferença é imperceptível (<5ms). Mantemos zero-deps aqui.

Referências canônicas:
  RSI:    Wilder, J.W. (1978) — New Concepts in Technical Trading Systems
  MACD:   Appel, G. (1979)
  BB:     Bollinger, J. (1992)
"""
from __future__ import annotations

import math
from typing import TypedDict


# ── TYPE ALIASES ──────────────────────────────────────────────────────────────

class RSIResult(TypedDict):
    values: list[float | None]   # RSI 0-100, None durante warmup
    overbought: float             # linha de referência (70)
    oversold: float               # linha de referência (30)
    period: int


class MACDResult(TypedDict):
    macd: list[float | None]      # MACD line (fast EMA - slow EMA)
    signal: list[float | None]    # Signal line (EMA do MACD)
    histogram: list[float | None] # MACD - Signal
    fast: int
    slow: int
    signal_period: int


class BollingerResult(TypedDict):
    upper: list[float | None]     # Banda superior (SMA + k*std)
    middle: list[float | None]    # SMA central
    lower: list[float | None]     # Banda inferior (SMA - k*std)
    bandwidth: list[float | None] # (upper - lower) / middle — volatilidade relativa
    pct_b: list[float | None]     # (close - lower) / (upper - lower) — posição na banda
    period: int
    std_dev: float


class IndicatorsResult(TypedDict):
    rsi: RSIResult
    macd: MACDResult
    bollinger: BollingerResult
    timestamps: list[int]         # timestamps Unix correspondentes
    ticker: str
    range: str
    count: int


# ── PRIMITIVOS ────────────────────────────────────────────────────────────────

def _ema(values: list[float], period: int) -> list[float | None]:
    """
    Exponential Moving Average com fator de suavização 2/(period+1).
    Retorna None durante o período de warmup (primeiros period-1 valores).

    Inicialização via SMA dos primeiros `period` valores — mais estável
    que iniciar com o primeiro valor diretamente.
    """
    if len(values) < period:
        return [None] * len(values)

    result: list[float | None] = [None] * (period - 1)
    # Seed: SMA dos primeiros `period` elementos
    seed = sum(values[:period]) / period
    result.append(seed)

    k = 2.0 / (period + 1)
    prev = seed
    for v in values[period:]:
        curr = v * k + prev * (1 - k)
        result.append(curr)
        prev = curr

    return result


def _sma(values: list[float], period: int) -> list[float | None]:
    """Simple Moving Average com sliding window."""
    result: list[float | None] = [None] * (period - 1)
    for i in range(period - 1, len(values)):
        result.append(sum(values[i - period + 1: i + 1]) / period)
    return result


def _std(values: list[float], period: int) -> list[float | None]:
    """
    Desvio padrão populacional com janela deslizante.
    Bollinger usa população (ddof=0), não amostra.
    """
    result: list[float | None] = [None] * (period - 1)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1: i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period
        result.append(math.sqrt(variance))
    return result


# ── RSI ───────────────────────────────────────────────────────────────────────

def compute_rsi(closes: list[float], period: int = 14) -> RSIResult:
    """
    RSI usando Wilder's Smoothed Moving Average (RMA/SMMA).

    Wilder usa alfa = 1/period (mais lento que EMA padrão).
    Essa é a implementação canônica — diferente do que alguns libs fazem
    erroneamente com EMA alfa=2/(period+1).

    Mínimo de closes necessários: period + 1 (para ter os deltas).
    """
    n = len(closes)
    values: list[float | None] = [None] * n

    if n < period + 1:
        return RSIResult(values=values, overbought=70.0, oversold=30.0, period=period)

    # Calcula deltas
    deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    # Seed: SMA dos primeiros `period` deltas (índice 0..period-1 de gains/losses)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _rsi(ag: float, al: float) -> float:
        if al == 0.0:
            return 100.0
        rs = ag / al
        return 100.0 - (100.0 / (1.0 + rs))

    # O primeiro RSI válido corresponde ao closes[period] (índice period no array original)
    values[period] = _rsi(avg_gain, avg_loss)

    # Wilder smoothing para os demais
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        values[i + 1] = _rsi(avg_gain, avg_loss)

    return RSIResult(values=values, overbought=70.0, oversold=30.0, period=period)


# ── MACD ──────────────────────────────────────────────────────────────────────

def compute_macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> MACDResult:
    """
    MACD = EMA(fast) - EMA(slow)
    Signal = EMA(MACD, signal_period)
    Histogram = MACD - Signal

    Warmup total: slow + signal_period - 2 barras.
    Antes disso retorna None para manter alinhamento de timestamps.
    """
    n = len(closes)
    empty: list[float | None] = [None] * n

    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)

    # MACD line — só onde ambas as EMAs existem
    macd_line: list[float | None] = []
    for f, s in zip(ema_fast, ema_slow):
        if f is None or s is None:
            macd_line.append(None)
        else:
            macd_line.append(f - s)

    # Extrai apenas os valores não-None para calcular o signal EMA
    valid_indices = [i for i, v in enumerate(macd_line) if v is not None]
    if len(valid_indices) < signal_period:
        return MACDResult(macd=empty, signal=empty, histogram=empty,
                          fast=fast, slow=slow, signal_period=signal_period)

    valid_macd = [macd_line[i] for i in valid_indices]  # type: ignore[misc]
    signal_ema = _ema(valid_macd, signal_period)  # type: ignore[arg-type]

    # Remapeia signal_ema de volta para o índice original
    signal_line: list[float | None] = [None] * n
    histogram: list[float | None] = [None] * n

    for idx, orig_i in enumerate(valid_indices):
        sig = signal_ema[idx]
        signal_line[orig_i] = sig
        if sig is not None and macd_line[orig_i] is not None:
            histogram[orig_i] = macd_line[orig_i] - sig  # type: ignore[operator]

    return MACDResult(
        macd=macd_line,
        signal=signal_line,
        histogram=histogram,
        fast=fast,
        slow=slow,
        signal_period=signal_period,
    )


# ── BOLLINGER BANDS ───────────────────────────────────────────────────────────

def compute_bollinger(
    closes: list[float],
    period: int = 20,
    std_dev: float = 2.0,
) -> BollingerResult:
    """
    Bollinger Bands:
      Middle = SMA(period)
      Upper  = Middle + std_dev * σ
      Lower  = Middle - std_dev * σ

    %B = (close - lower) / (upper - lower)
      > 1.0 → acima da banda (sobrecompra)
      < 0.0 → abaixo da banda (sobrevenda)
      = 0.5 → na média

    Bandwidth = (upper - lower) / middle
      Valores baixos = squeeze (potencial breakout)
    """
    n = len(closes)
    sma  = _sma(closes, period)
    std  = _std(closes, period)

    upper:     list[float | None] = [None] * n
    middle:    list[float | None] = [None] * n
    lower:     list[float | None] = [None] * n
    bandwidth: list[float | None] = [None] * n
    pct_b:     list[float | None] = [None] * n

    for i in range(n):
        m = sma[i]
        s = std[i]
        if m is None or s is None:
            continue
        u = m + std_dev * s
        l = m - std_dev * s
        middle[i]    = m
        upper[i]     = u
        lower[i]     = l
        bandwidth[i] = (u - l) / m if m != 0 else None
        band_width   = u - l
        pct_b[i]     = (closes[i] - l) / band_width if band_width != 0 else None

    return BollingerResult(
        upper=upper, middle=middle, lower=lower,
        bandwidth=bandwidth, pct_b=pct_b,
        period=period, std_dev=std_dev,
    )


# ── FACADE ────────────────────────────────────────────────────────────────────

def compute_all(
    bars: list[dict],
    rsi_period: int = 14,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    bb_period: int = 20,
    bb_std: float = 2.0,
) -> IndicatorsResult:
    """
    Calcula RSI + MACD + Bollinger para uma lista de barras OHLC.

    Entrada esperada: list[dict] com chaves {time, open, high, low, close, volume}
    — exatamente o formato retornado pelo BrapiClient.

    Retorna todos os arrays com mesmo comprimento que `bars`,
    com None nos índices de warmup, garantindo alinhamento 1:1 com timestamps.
    """
    if not bars:
        empty: list[float | None] = []
        return IndicatorsResult(
            rsi=RSIResult(values=[], overbought=70.0, oversold=30.0, period=rsi_period),
            macd=MACDResult(macd=[], signal=[], histogram=[],
                            fast=macd_fast, slow=macd_slow, signal_period=macd_signal),
            bollinger=BollingerResult(upper=[], middle=[], lower=[],
                                      bandwidth=[], pct_b=[], period=bb_period, std_dev=bb_std),
            timestamps=[],
            ticker="",
            range="",
            count=0,
        )

    closes     = [float(b["close"]) for b in bars]
    timestamps = [int(b["time"]) for b in bars]

    return IndicatorsResult(
        rsi=compute_rsi(closes, rsi_period),
        macd=compute_macd(closes, macd_fast, macd_slow, macd_signal),
        bollinger=compute_bollinger(closes, bb_period, bb_std),
        timestamps=timestamps,
        ticker="",
        range="",
        count=len(bars),
    )
