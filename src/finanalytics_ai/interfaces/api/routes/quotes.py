from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
import structlog

from finanalytics_ai.domain.indicators.technical import IndicatorsResult, compute_all
from finanalytics_ai.domain.value_objects.money import Money, Ticker
from finanalytics_ai.exceptions import MarketDataUnavailableError

router = APIRouter()
logger = structlog.get_logger(__name__)
RangePeriod = Literal["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"]
DAILY = {"1d", "2d", "3d", "4d", "5d", "1wk", "1mo", "3mo"}


def _svc(r: Request):
    return getattr(r.app.state, "ohlc_1m_service", None)


def _market(r: Request) -> Any:
    # Decisão 20: CompositeMarketDataClient já prioriza DB → Yahoo → BRAPI
    m = getattr(r.app.state, "market_client", None)
    if m is None:
        raise HTTPException(status_code=503, detail="Market data client não disponível.")
    return m


@router.get("/{ticker}/history")
async def get_history(
    ticker: str,
    request: Request,
    range: RangePeriod = Query(default="5d"),
    interval: str = Query(default="5m"),
) -> dict:
    svc = _svc(request)
    if svc and interval not in DAILY:
        try:
            bars = await svc.get_bars(ticker=ticker, interval=interval, range_period=range)
            if bars:
                return {
                    "ticker": ticker.upper(),
                    "range": range,
                    "interval": interval,
                    "bars": bars,
                    "count": len(bars),
                    "source": "cache_1m",
                }
        except Exception as e:
            logger.warning("ohlc_1m.fallback", ticker=ticker, error=str(e))
    market = _market(request)
    bars = await market.get_ohlc_bars(Ticker(ticker), range_period=range, interval=interval)
    return {
        "ticker": ticker.upper(),
        "range": range,
        "interval": interval,
        "bars": bars,
        "count": len(bars),
        "source": "market_client",
    }


@router.get("/{ticker}/indicators")
async def get_indicators(
    ticker: str,
    request: Request,
    range: RangePeriod = Query(default="3mo"),
    rsi_period: Annotated[int, Query(ge=2, le=50)] = 14,
    macd_fast: Annotated[int, Query(ge=2, le=50)] = 12,
    macd_slow: Annotated[int, Query(ge=3, le=200)] = 26,
    macd_signal: Annotated[int, Query(ge=2, le=50)] = 9,
    bb_period: Annotated[int, Query(ge=2, le=200)] = 20,
    bb_std: Annotated[float, Query(ge=0.5, le=5)] = 2.0,
    stoch_period: Annotated[int, Query(ge=2, le=100)] = 14,
    stoch_smooth_k: Annotated[int, Query(ge=1, le=20)] = 3,
    stoch_smooth_d: Annotated[int, Query(ge=1, le=20)] = 3,
    atr_period: Annotated[int, Query(ge=2, le=100)] = 14,
) -> IndicatorsResult:
    market = _market(request)
    try:
        bars = await market.get_ohlc_bars(Ticker(ticker), range_period=range)
    except MarketDataUnavailableError:
        # Fonte externa indisponível para este ticker (ex: futuros WDOFUT/WINFUT)
        bars = []
    if not bars:
        return {
            "ticker": ticker.upper(),
            "range": range,
            "rsi": {"values": [], "overbought": 70, "oversold": 30, "period": rsi_period},
            "macd": {
                "macd": [],
                "signal": [],
                "histogram": [],
                "fast": macd_fast,
                "slow": macd_slow,
                "signal_period": macd_signal,
            },
            "bollinger": {
                "upper": [],
                "middle": [],
                "lower": [],
                "bandwidth": [],
                "pct_b": [],
                "period": bb_period,
                "std_dev": bb_std,
            },
            "stochastic": {
                "k": [],
                "d": [],
                "overbought": 80,
                "oversold": 20,
                "period": stoch_period,
                "smooth_k": stoch_smooth_k,
                "smooth_d": stoch_smooth_d,
            },
            "atr": {"values": [], "period": atr_period},
            "vwap": {"values": []},
            "timestamps": [],
            "count": 0,
        }
    r = compute_all(
        bars,
        rsi_period=rsi_period,
        macd_fast=macd_fast,
        macd_slow=macd_slow,
        macd_signal=macd_signal,
        bb_period=bb_period,
        bb_std=bb_std,
        stoch_period=stoch_period,
        stoch_smooth_k=stoch_smooth_k,
        stoch_smooth_d=stoch_smooth_d,
        atr_period=atr_period,
    )
    r.update({"range": range, "ticker": ticker.upper()})
    return r


@router.get("/{ticker}/detail")
async def get_detail(ticker: str, request: Request) -> Money:
    market = _market(request)
    try:
        return await market.get_quote(Ticker(ticker))
    except MarketDataUnavailableError as exc:
        raise HTTPException(status_code=404, detail=f"Ticker {ticker} indisponível") from exc
