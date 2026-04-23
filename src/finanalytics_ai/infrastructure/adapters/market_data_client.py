"""
finanalytics_ai.infrastructure.adapters.market_data_client
────────────────────────────────────────────────────────────
CompositeMarketDataClient: DB (Profit DLL + Fintz) → Yahoo → BRAPI.

Decisão 20 (Caminho 2, 23/abr/2026):
  BRAPI é ÚLTIMO fallback. A stack prefere dados locais (ingeridos via DLL
  Profit + Fintz histórico) e só sai pra rede quando o banco está vazio
  para o ticker/range solicitado.

Ordem canônica em get_ohlc_bars():
  1. candle_repository.fetch_candles (TimescaleDB)
     → profit_daily_bars, ohlc_1m, market_history_trades, profit_ticks, fintz_cotacoes_ts
  2. Yahoo Finance (cobertura ampla B3, histórico profundo)
  3. BRAPI (último recurso; requer token, instável, rate-limited)

get_quote() prefere profit_agent (:8002) para tickers subscritos via DLL;
BRAPI cai no fim se nenhuma fonte DLL/Yahoo responder.

Design:
  - Structural subtyping: substituível em qualquer service que tipa BrapiClient
  - Sem herança: composição sobre herança
  - MIN_BARS_THRESHOLD = 30 barras para considerar fonte "suficiente"
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient
from finanalytics_ai.infrastructure.adapters.yahoo_client import YahooFinanceClient

if TYPE_CHECKING:
    from finanalytics_ai.domain.value_objects.money import Money, Ticker

logger = structlog.get_logger(__name__)

# Ranges que o DB local pode não cobrir (histórico muito longo) → Yahoo primeiro
YAHOO_PREFERRED_RANGES = frozenset({"10y", "max"})

# Mínimo de barras para considerar a fonte "suficiente"
MIN_BARS_THRESHOLD = 30

# Mapeamento range_period → dias de histórico (para since date do DB)
_RANGE_TO_DAYS = {
    "1d": 2,
    "5d": 7,
    "1mo": 33,
    "3mo": 95,
    "6mo": 185,
    "1y": 370,
    "2y": 735,
    "5y": 1830,
    "10y": 3660,
}

_PROFIT_AGENT_URL = os.getenv("PROFIT_AGENT_URL", "http://host.docker.internal:8002")


def _range_to_since(range_period: str) -> date | None:
    if range_period == "max":
        return None
    if range_period == "ytd":
        return date(date.today().year, 1, 1)
    days = _RANGE_TO_DAYS.get(range_period, 95)
    return date.today() - timedelta(days=days)


def _candles_to_bars(candles: list[Any]) -> list[dict[str, Any]]:
    # Formato compatível com BrapiClient.get_ohlc_bars: time=unix_ts, o/h/l/c/v
    out: list[dict[str, Any]] = []
    for c in candles:
        ts = int(datetime.combine(c.date, datetime.min.time()).timestamp())
        out.append(
            {
                "time": ts,
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": int(c.volume or 0),
            }
        )
    return out


class CompositeMarketDataClient:
    """
    Cliente composto: DB (Profit DLL + Fintz) → Yahoo → BRAPI.

    Substitui BrapiClient em todos os services via duck typing (Decisão 20).
    """

    def __init__(
        self,
        brapi_client: BrapiClient,
        yahoo_client: YahooFinanceClient | None = None,
    ) -> None:
        self._brapi = brapi_client
        self._yahoo = yahoo_client or YahooFinanceClient()

    # ── Interface principal ───────────────────────────────────────────────────

    async def get_ohlc_bars(
        self,
        ticker: Any,
        range_period: str = "1y",
        interval: str | None = None,
    ) -> list[dict[str, Any]]:
        """OHLC com fallback DB → Yahoo → BRAPI (Decisão 20)."""
        tkr = str(ticker).upper()

        # Ranges muito longos → Yahoo primeiro (DB pode não ter 10y de tudo)
        if range_period in YAHOO_PREFERRED_RANGES:
            yahoo_bars = await self._fetch_yahoo_safe(ticker, range_period, interval)
            if yahoo_bars:
                logger.info(
                    "market_data.source.yahoo",
                    ticker=tkr,
                    range=range_period,
                    bars=len(yahoo_bars),
                )
                return yahoo_bars
            # Fallback derradeiro: BRAPI
            return await self._fetch_brapi_safe(ticker, range_period, interval)

        # 1. DB local (candle_repository cobre profit_daily_bars + ohlc_1m + trades + ticks + Fintz)
        db_bars = await self._fetch_db_safe(tkr, range_period)
        if len(db_bars) >= MIN_BARS_THRESHOLD:
            logger.info(
                "market_data.source.db",
                ticker=tkr,
                range=range_period,
                bars=len(db_bars),
            )
            return db_bars

        # 2. Yahoo fallback (cobertura B3 ampla, histórico profundo)
        yahoo_bars = await self._fetch_yahoo_safe(ticker, range_period, interval)
        if yahoo_bars:
            logger.info(
                "market_data.source.yahoo",
                ticker=tkr,
                range=range_period,
                bars=len(yahoo_bars),
                db_bars=len(db_bars),
            )
            return yahoo_bars

        # 3. BRAPI — último recurso (Decisão 20)
        logger.warning(
            "market_data.fallback.brapi",
            ticker=tkr,
            range=range_period,
            reason="db_and_yahoo_empty",
        )
        brapi_bars = await self._fetch_brapi_safe(ticker, range_period, interval)
        if brapi_bars:
            logger.info(
                "market_data.source.brapi",
                ticker=tkr,
                range=range_period,
                bars=len(brapi_bars),
            )
            return brapi_bars

        # Todos falharam → retorna o que o DB tinha (pode ser < MIN_BARS)
        return db_bars

    async def _fetch_db_safe(self, ticker: str, range_period: str) -> list[dict[str, Any]]:
        try:
            from finanalytics_ai.infrastructure.market_data.candle_repository import fetch_candles

            candles, _source = await fetch_candles(ticker, since=_range_to_since(range_period))
            return _candles_to_bars(candles)
        except Exception as exc:
            logger.warning("market_data.db_error", ticker=ticker, error=str(exc))
            return []

    async def _fetch_brapi_safe(
        self,
        ticker: Any,
        range_period: str,
        interval: str | None,
    ) -> list[dict[str, Any]]:
        try:
            return await self._brapi.get_ohlc_bars(
                ticker, range_period=range_period, interval=interval
            )
        except Exception as exc:
            logger.warning("market_data.brapi_error", ticker=str(ticker), error=str(exc))
            return []

    async def _fetch_yahoo_safe(
        self,
        ticker: Any,
        range_period: str,
        interval: str | None,
    ) -> list[dict[str, Any]]:
        try:
            return await self._yahoo.get_ohlc_bars(
                ticker, range_period=range_period, interval=interval
            )
        except Exception as exc:
            logger.warning("market_data.yahoo_error", ticker=str(ticker), error=str(exc))
            return []

    # ── Quotes (realtime) ─────────────────────────────────────────────────────

    async def get_quote(self, ticker: Ticker) -> Money:
        """Cotação live: profit_agent (DLL subscrita) → Yahoo → BRAPI."""
        tkr = str(ticker).upper()

        # 1. profit_agent (DLL realtime) — só funciona para tickers subscritos
        price = await self._fetch_profit_agent_quote(tkr)
        if price is not None:
            from finanalytics_ai.domain.value_objects.money import Money as _Money

            return _Money.of(price)

        # 2. Yahoo (gratuito, sem token)
        try:
            return await self._yahoo.get_quote(ticker)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.debug("market_data.yahoo_quote_miss", ticker=tkr, error=str(exc))

        # 3. BRAPI (último)
        return await self._brapi.get_quote(ticker)

    async def _fetch_profit_agent_quote(self, ticker: str) -> float | None:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{_PROFIT_AGENT_URL}/quotes")
                if r.status_code != 200:
                    return None
                data = r.json()
                if isinstance(data, dict):
                    data = data.get("quotes") or data.get("data") or []
                for q in data or []:
                    if isinstance(q, dict) and str(q.get("ticker", "")).upper() == ticker:
                        price = q.get("last_price") or q.get("price") or q.get("last")
                        if price:
                            return float(price)
            return None
        except Exception:
            return None

    async def get_quote_full(self, ticker: Ticker) -> dict[str, Any]:
        # BRAPI é a única fonte com dados completos (change, volume, 52w, etc)
        return await self._brapi.get_quote_full(ticker)

    async def get_fundamentals_batch(self, tickers: list[str]) -> list[dict[str, Any]]:
        # Fundamentals continuam via BRAPI (DLL não fornece)
        return await self._brapi.get_fundamentals_batch(tickers)

    async def search_assets(self, query: str) -> list[dict[str, str]]:
        return await self._brapi.search_assets(query)

    async def is_healthy(self) -> bool:
        # Saudável se qualquer fonte responder
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                r = await client.get(f"{_PROFIT_AGENT_URL}/health")
                if r.status_code == 200:
                    return True
        except Exception:
            pass
        try:
            if await self._yahoo.is_healthy():
                return True
        except Exception:
            pass
        try:
            return await self._brapi.is_healthy()
        except Exception:
            return False

    async def close(self) -> None:
        try:
            await self._brapi.close()
        except Exception:
            pass
        try:
            await self._yahoo.close()
        except Exception:
            pass


# ── Factory ───────────────────────────────────────────────────────────────────


def create_market_data_client(brapi_token: str | None = None) -> CompositeMarketDataClient:
    brapi = BrapiClient()
    yahoo = YahooFinanceClient()
    client = CompositeMarketDataClient(brapi, yahoo)
    logger.info(
        "market_data_client.created",
        brapi_configured=bool(brapi_token),
        yahoo_available=True,
        order="db→yahoo→brapi",
    )
    return client


def create_cached_market_data_client(
    brapi_token: str | None = None,
    session_factory: Any | None = None,
) -> CompositeMarketDataClient:
    """Alias de create_market_data_client mantido para compatibilidade."""
    return create_market_data_client(brapi_token)
