"""Pydantic v2 schemas for indicators API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from datetime import date


class CandleWithIndicators(BaseModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float

    ema_8: float | None = None
    ema_20: float | None = None
    ema_80: float | None = None
    ema_200: float | None = None
    sma_9: float | None = None

    rsi_2: float | None = None
    rsi_9: float | None = None
    rsi_14: float | None = None

    adx_8: float | None = None
    atr_14: float | None = None
    atr_21: float | None = None

    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None

    stoch_k: float | None = None
    stoch_d: float | None = None


class IndicatorResponse(BaseModel):
    ticker: str
    source: str
    timeframe: str = "daily"
    candle_count: int
    candles: list[CandleWithIndicators]


class SignalSummary(BaseModel):
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


class IndicatorSummaryResponse(BaseModel):
    ticker: str
    tipo: str  # "acao" | "futuro"
    source: str
    last_candle: CandleWithIndicators
    signals: SignalSummary


class HourlyVWAPSchema(BaseModel):
    hour: int
    vwap: float
    volume: float
    tick_count: int


class VWAPResponse(BaseModel):
    ticker: str
    date: date
    vwap: float | None
    mercado_aberto: bool
    hourly_profile: list[HourlyVWAPSchema] = Field(default_factory=list)
