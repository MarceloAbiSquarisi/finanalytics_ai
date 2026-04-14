"""Domain models for technical analytics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date  # noqa: TC003 — needed at runtime by dataclass


@dataclass(frozen=True, slots=True)
class CandleData:
    """Single OHLCV candle."""

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(slots=True)
class IndicatorResult:
    """Computed indicators for a single candle."""

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float

    # EMAs
    ema_8: float | None = None
    ema_20: float | None = None
    ema_80: float | None = None
    ema_200: float | None = None

    # SMA
    sma_9: float | None = None

    # RSI
    rsi_2: float | None = None
    rsi_9: float | None = None
    rsi_14: float | None = None

    # ADX
    adx_8: float | None = None

    # ATR
    atr_14: float | None = None
    atr_21: float | None = None

    # Bollinger Bands
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None

    # Stochastic
    stoch_k: float | None = None
    stoch_d: float | None = None


@dataclass(slots=True)
class VWAPResult:
    """VWAP calculation result."""

    ticker: str
    date: date
    vwap: float
    mercado_aberto: bool
    hourly_profile: list[HourlyVWAP] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class HourlyVWAP:
    """VWAP per hour bucket."""

    hour: int
    vwap: float
    volume: float
    tick_count: int


@dataclass(frozen=True, slots=True)
class SetupSignal:
    """Boolean signals derived from indicator summary."""

    rsi2_sobrevendido: bool = False
    rsi2_sobrecomprado: bool = False
    preco_acima_ema8: bool = False
    preco_acima_ema20: bool = False
    preco_acima_ema80: bool = False
    preco_abaixo_bb_lower: bool = False
    preco_acima_bb_upper: bool = False
    adx_trending: bool = False
    stoch_sobrevendido: bool = False
    stoch_sobrecomprado: bool = False


@dataclass(slots=True)
class ScanResult:
    """Screener result for a single ticker."""

    ticker: str
    tipo: str  # "acao" | "futuro"
    last_close: float
    indicators: IndicatorResult
    signals: SetupSignal


@dataclass(frozen=True, slots=True)
class PairAnalysis:
    """Pair correlation/cointegration result."""

    ticker_a: str
    ticker_b: str
    correlation: float
    cointegrated: bool
    spread_zscore: float | None = None
