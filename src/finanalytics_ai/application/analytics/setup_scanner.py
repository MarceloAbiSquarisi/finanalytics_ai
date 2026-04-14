"""
Setup Scanner — detects 9 technical setups using indicator_engine output.

Reuses indicator_engine.compute() and applies rule-based detection.
"""

from __future__ import annotations

from datetime import UTC, datetime
import time as _time
from typing import Any

from finanalytics_ai.application.analytics.indicator_engine import compute
from finanalytics_ai.domain.analytics.exceptions import InsufficientDataError
from finanalytics_ai.domain.analytics.models import (
    CandleData,
    IndicatorResult,
    ScanAllResult,
    SetupDetection,
)

# ── Setup definitions ─────────────────────────────────────────────────────────

_FUTURO_SUFFIXES = ("FUT", "F", "G", "H", "J", "K", "M", "N", "Q", "U", "V", "X", "Z")

SETUP_DEFS: dict[str, dict[str, Any]] = {
    "ifr2_oversold": {
        "descricao": "IFR2 sobrevendido (< 25)",
        "direcao": "long",
        "timeframe": "daily",
        "min_candles": 10,
    },
    "ifr2_overbought": {
        "descricao": "IFR2 sobrecomprado (> 80)",
        "direcao": "short",
        "timeframe": "daily",
        "min_candles": 10,
    },
    "parada_na_20": {
        "descricao": "Parada na EMA20 — low toca EMA20, close fecha acima",
        "direcao": "long",
        "timeframe": "daily",
        "min_candles": 50,
    },
    "hdv": {
        "descricao": "HDV — ADX crescente > 20 com DI+ > DI-",
        "direcao": "long",
        "timeframe": "daily",
        "min_candles": 14,
    },
    "ema_alinhadas_alta": {
        "descricao": "EMAs alinhadas em alta: EMA8 > EMA20 > EMA80",
        "direcao": "long",
        "timeframe": "daily",
        "min_candles": 80,
    },
    "bb_squeeze": {
        "descricao": "Bollinger Squeeze — bandas convergentes (bandwidth < 5%)",
        "direcao": "neutral",
        "timeframe": "daily",
        "min_candles": 20,
    },
    "candle_pavio": {
        "descricao": "Candle de pavio — corpo < 30% da amplitude total",
        "direcao": "neutral",
        "timeframe": "daily",
        "min_candles": 1,
    },
    "inside_bar": {
        "descricao": "Inside Bar semanal — candle dentro do anterior",
        "direcao": "neutral",
        "timeframe": "weekly",
        "min_candles": 10,
    },
    "ifr14_weekly_oversold": {
        "descricao": "IFR14 semanal sobrevendido (< 30)",
        "direcao": "long",
        "timeframe": "weekly",
        "min_candles": 15,
    },
}

# ── Cache ─────────────────────────────────────────────────────────────────────

_scan_cache: dict[str, tuple[float, list[SetupDetection]]] = {}


def _cache_get(key: str, ttl: int) -> list[SetupDetection] | None:
    entry = _scan_cache.get(key)
    if entry is None:
        return None
    ts, result = entry
    if _time.monotonic() - ts > ttl:
        _scan_cache.pop(key, None)
        return None
    return result


def _cache_set(key: str, result: list[SetupDetection]) -> None:
    _scan_cache[key] = (_time.monotonic(), result)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _classify_ticker(ticker: str) -> str:
    t = ticker.upper()
    if t.endswith(_FUTURO_SUFFIXES) or "FUT" in t:
        return "futuro"
    return "acao"


def _clamp_strength(val: float) -> float:
    return max(0.0, min(1.0, val))


# ── Weekly aggregation ────────────────────────────────────────────────────────


def aggregate_weekly(candles: list[CandleData]) -> list[CandleData]:
    """Aggregate daily candles into weekly candles (ISO weeks, Mon-Fri)."""
    if not candles:
        return []

    weeks: dict[tuple[int, int], list[CandleData]] = {}
    for c in candles:
        iso = c.date.isocalendar()
        key = (iso[0], iso[1])  # (year, week)
        weeks.setdefault(key, []).append(c)

    result: list[CandleData] = []
    for key in sorted(weeks):
        wk = sorted(weeks[key], key=lambda x: x.date)
        result.append(
            CandleData(
                date=wk[-1].date,  # last day of the week
                open=wk[0].open,
                high=max(c.high for c in wk),
                low=min(c.low for c in wk),
                close=wk[-1].close,
                volume=sum(c.volume for c in wk),
            )
        )
    return result


# ── Individual setup detectors ────────────────────────────────────────────────


def _detect_ifr2_oversold(
    ind: IndicatorResult, ticker: str, tipo: str,
) -> SetupDetection | None:
    if ind.rsi_2 is None or ind.rsi_2 >= 25:
        return None
    strength = _clamp_strength((25 - ind.rsi_2) / 25)
    return SetupDetection(
        ticker=ticker, tipo=tipo, setup_name="ifr2_oversold",
        descricao=SETUP_DEFS["ifr2_oversold"]["descricao"],
        direcao="long", timeframe="daily", strength=round(strength, 3),
        date=ind.date,
        details={"rsi_2": ind.rsi_2, "close": ind.close},
        entry_price=ind.close, stop_price=ind.low,
    )


def _detect_ifr2_overbought(
    ind: IndicatorResult, ticker: str, tipo: str,
) -> SetupDetection | None:
    if ind.rsi_2 is None or ind.rsi_2 <= 80:
        return None
    strength = _clamp_strength((ind.rsi_2 - 80) / 20)
    return SetupDetection(
        ticker=ticker, tipo=tipo, setup_name="ifr2_overbought",
        descricao=SETUP_DEFS["ifr2_overbought"]["descricao"],
        direcao="short", timeframe="daily", strength=round(strength, 3),
        date=ind.date,
        details={"rsi_2": ind.rsi_2, "close": ind.close},
        entry_price=ind.close, stop_price=ind.high,
    )


def _detect_parada_na_20(
    ind: IndicatorResult, ticker: str, tipo: str,
) -> SetupDetection | None:
    if ind.ema_20 is None:
        return None
    if not (ind.low <= ind.ema_20 and ind.close > ind.ema_20):
        return None
    # EMA200 check is optional (data may be insufficient)
    ema200_ok = ind.ema_200 is None or ind.close > ind.ema_200
    strength = 0.7 if ema200_ok else 0.5
    return SetupDetection(
        ticker=ticker, tipo=tipo, setup_name="parada_na_20",
        descricao=SETUP_DEFS["parada_na_20"]["descricao"],
        direcao="long", timeframe="daily", strength=round(strength, 3),
        date=ind.date,
        details={"ema_20": ind.ema_20, "ema_200": ind.ema_200, "close": ind.close, "low": ind.low},
        entry_price=ind.close, stop_price=ind.low,
    )


def _detect_hdv(
    ind: IndicatorResult, prev: IndicatorResult | None, ticker: str, tipo: str,
) -> SetupDetection | None:
    if ind.adx_8 is None or ind.adx_8 <= 20:
        return None
    # ADX must be rising
    if prev is None or prev.adx_8 is None or ind.adx_8 <= prev.adx_8:
        return None
    # DI+ > DI- approximated by close > open (price advancing)
    if ind.close <= ind.open:
        return None
    strength = _clamp_strength((ind.adx_8 - 20) / 30)
    return SetupDetection(
        ticker=ticker, tipo=tipo, setup_name="hdv",
        descricao=SETUP_DEFS["hdv"]["descricao"],
        direcao="long", timeframe="daily", strength=round(strength, 3),
        date=ind.date,
        details={"adx_8": ind.adx_8, "prev_adx_8": prev.adx_8, "close": ind.close},
        entry_price=ind.close, stop_price=ind.low,
    )


def _detect_ema_alinhadas_alta(
    ind: IndicatorResult, ticker: str, tipo: str,
) -> SetupDetection | None:
    if ind.ema_8 is None or ind.ema_20 is None or ind.ema_80 is None:
        return None
    if not (ind.ema_8 > ind.ema_20 > ind.ema_80):
        return None
    # Strength: how far apart the EMAs are relative to price
    spread = (ind.ema_8 - ind.ema_80) / ind.close if ind.close else 0
    strength = _clamp_strength(spread * 10)
    return SetupDetection(
        ticker=ticker, tipo=tipo, setup_name="ema_alinhadas_alta",
        descricao=SETUP_DEFS["ema_alinhadas_alta"]["descricao"],
        direcao="long", timeframe="daily", strength=round(strength, 3),
        date=ind.date,
        details={"ema_8": ind.ema_8, "ema_20": ind.ema_20, "ema_80": ind.ema_80},
        entry_price=ind.close,
    )


def _detect_bb_squeeze(
    ind: IndicatorResult, ticker: str, tipo: str,
) -> SetupDetection | None:
    if ind.bb_upper is None or ind.bb_lower is None or ind.bb_middle is None:
        return None
    if ind.bb_middle == 0:
        return None
    bandwidth = (ind.bb_upper - ind.bb_lower) / ind.bb_middle
    if bandwidth >= 0.05:
        return None
    strength = _clamp_strength((0.05 - bandwidth) / 0.05)
    return SetupDetection(
        ticker=ticker, tipo=tipo, setup_name="bb_squeeze",
        descricao=SETUP_DEFS["bb_squeeze"]["descricao"],
        direcao="neutral", timeframe="daily", strength=round(strength, 3),
        date=ind.date,
        details={"bb_upper": ind.bb_upper, "bb_lower": ind.bb_lower, "bb_middle": ind.bb_middle, "bandwidth": round(bandwidth, 6)},
        entry_price=ind.close,
    )


def _detect_candle_pavio(
    ind: IndicatorResult, ticker: str, tipo: str,
) -> SetupDetection | None:
    amplitude = ind.high - ind.low
    if amplitude <= 0:
        return None
    corpo = abs(ind.close - ind.open)
    ratio = corpo / amplitude
    if ratio >= 0.30:
        return None
    strength = _clamp_strength((0.30 - ratio) / 0.30)
    return SetupDetection(
        ticker=ticker, tipo=tipo, setup_name="candle_pavio",
        descricao=SETUP_DEFS["candle_pavio"]["descricao"],
        direcao="neutral", timeframe="daily", strength=round(strength, 3),
        date=ind.date,
        details={"corpo": round(corpo, 4), "amplitude": round(amplitude, 4), "ratio": round(ratio, 4)},
        entry_price=ind.close,
    )


def _detect_inside_bar_weekly(
    weekly_candles: list[CandleData], ticker: str, tipo: str,
) -> SetupDetection | None:
    if len(weekly_candles) < 2:
        return None
    curr = weekly_candles[-1]
    prev = weekly_candles[-2]
    if curr.high < prev.high and curr.low > prev.low:
        strength = _clamp_strength(1.0 - (curr.high - curr.low) / (prev.high - prev.low))
        return SetupDetection(
            ticker=ticker, tipo=tipo, setup_name="inside_bar",
            descricao=SETUP_DEFS["inside_bar"]["descricao"],
            direcao="neutral", timeframe="weekly", strength=round(strength, 3),
            date=curr.date,
            details={
                "curr_high": curr.high, "curr_low": curr.low,
                "prev_high": prev.high, "prev_low": prev.low,
            },
            entry_price=curr.close,
        )
    return None


def _detect_ifr14_weekly_oversold(
    weekly_indicators: list[IndicatorResult], ticker: str, tipo: str,
) -> SetupDetection | None:
    if not weekly_indicators:
        return None
    last = weekly_indicators[-1]
    if last.rsi_14 is None or last.rsi_14 >= 30:
        return None
    strength = _clamp_strength((30 - last.rsi_14) / 30)
    return SetupDetection(
        ticker=ticker, tipo=tipo, setup_name="ifr14_weekly_oversold",
        descricao=SETUP_DEFS["ifr14_weekly_oversold"]["descricao"],
        direcao="long", timeframe="weekly", strength=round(strength, 3),
        date=last.date,
        details={"rsi_14": last.rsi_14, "close": last.close},
        entry_price=last.close,
    )


# ── Main scan functions ───────────────────────────────────────────────────────


def scan_ticker(
    candles: list[CandleData],
    ticker: str,
    setups: list[str] | None = None,
    cache_ttl: int = 300,
) -> list[SetupDetection]:
    """
    Scan a single ticker for all (or selected) setups.

    Args:
        candles: Daily OHLCV candles, sorted oldest-first.
        ticker: Ticker symbol.
        setups: List of setup names to check. None = all.
        cache_ttl: Cache TTL in seconds. 0 = no cache.

    Returns:
        List of detected setups.
    """
    wanted = set(setups) if setups else set(SETUP_DEFS.keys())
    tipo = _classify_ticker(ticker)

    # Cache check
    cache_key = f"{ticker}:{','.join(sorted(wanted))}"
    if cache_ttl > 0:
        cached = _cache_get(cache_key, cache_ttl)
        if cached is not None:
            return cached

    detections: list[SetupDetection] = []

    # ── Daily setups ──────────────────────────────────────────────────────
    daily_setups = {s for s in wanted if SETUP_DEFS.get(s, {}).get("timeframe") == "daily"}
    if daily_setups and len(candles) >= 1:
        # Compute indicators with relaxed min_candles (let engine do what it can)
        min_needed = max(
            (SETUP_DEFS[s]["min_candles"] for s in daily_setups),
            default=1,
        )
        if len(candles) >= min_needed:
            try:
                indicators = compute(candles, min_candles=min_needed, ticker=ticker)
            except InsufficientDataError:
                indicators = []

            if indicators:
                last = indicators[-1]
                prev = indicators[-2] if len(indicators) >= 2 else None

                if "ifr2_oversold" in daily_setups:
                    d = _detect_ifr2_oversold(last, ticker, tipo)
                    if d:
                        detections.append(d)

                if "ifr2_overbought" in daily_setups:
                    d = _detect_ifr2_overbought(last, ticker, tipo)
                    if d:
                        detections.append(d)

                if "parada_na_20" in daily_setups:
                    d = _detect_parada_na_20(last, ticker, tipo)
                    if d:
                        detections.append(d)

                if "hdv" in daily_setups:
                    d = _detect_hdv(last, prev, ticker, tipo)
                    if d:
                        detections.append(d)

                if "ema_alinhadas_alta" in daily_setups:
                    d = _detect_ema_alinhadas_alta(last, ticker, tipo)
                    if d:
                        detections.append(d)

                if "bb_squeeze" in daily_setups:
                    d = _detect_bb_squeeze(last, ticker, tipo)
                    if d:
                        detections.append(d)

                if "candle_pavio" in daily_setups:
                    d = _detect_candle_pavio(last, ticker, tipo)
                    if d:
                        detections.append(d)

    # ── Weekly setups ─────────────────────────────────────────────────────
    weekly_setups = {s for s in wanted if SETUP_DEFS.get(s, {}).get("timeframe") == "weekly"}
    if weekly_setups and len(candles) >= 10:
        weekly = aggregate_weekly(candles)

        if "inside_bar" in weekly_setups and len(weekly) >= 2:
            d = _detect_inside_bar_weekly(weekly, ticker, tipo)
            if d:
                detections.append(d)

        if "ifr14_weekly_oversold" in weekly_setups and len(weekly) >= 15:
            try:
                weekly_ind = compute(weekly, min_candles=15, ticker=ticker)
            except InsufficientDataError:
                weekly_ind = []
            d = _detect_ifr14_weekly_oversold(weekly_ind, ticker, tipo)
            if d:
                detections.append(d)

    if cache_ttl > 0:
        _cache_set(cache_key, detections)

    return detections


def scan_all(
    tickers_candles: dict[str, list[CandleData]],
    setups: list[str] | None = None,
    direcao: str | None = None,
    min_volume: float = 0,
    excluir_futuros: bool = False,
    cache_ttl: int = 300,
) -> ScanAllResult:
    """
    Scan multiple tickers and aggregate results.

    Args:
        tickers_candles: Mapping of ticker -> daily candles.
        setups: Subset of setups to check. None = all.
        direcao: Filter results by direction (long|short|neutral). None = all.
        min_volume: Minimum last-day volume filter.
        excluir_futuros: Exclude futures tickers.
        cache_ttl: Cache TTL in seconds.

    Returns:
        ScanAllResult with all detections.
    """
    t0 = _time.monotonic()
    all_signals: list[SetupDetection] = []
    sem_dados: list[str] = []
    tickers_ok = 0

    for ticker, candles in tickers_candles.items():
        if excluir_futuros and _classify_ticker(ticker) == "futuro":
            continue

        if not candles or len(candles) < 1:
            sem_dados.append(ticker)
            continue

        # Volume filter
        if min_volume > 0 and candles[-1].volume < min_volume:
            tickers_ok += 1
            continue

        try:
            signals = scan_ticker(candles, ticker, setups=setups, cache_ttl=cache_ttl)
            tickers_ok += 1
            all_signals.extend(signals)
        except Exception:
            sem_dados.append(ticker)

    # Direction filter
    if direcao and direcao != "all":
        all_signals = [s for s in all_signals if s.direcao == direcao]

    duracao_ms = int((_time.monotonic() - t0) * 1000)

    return ScanAllResult(
        scanned_at=datetime.now(UTC),
        total_tickers=len(tickers_candles),
        tickers_com_dados=tickers_ok,
        total_signals=len(all_signals),
        duracao_ms=duracao_ms,
        signals=all_signals,
        tickers_sem_dados=sem_dados,
    )
