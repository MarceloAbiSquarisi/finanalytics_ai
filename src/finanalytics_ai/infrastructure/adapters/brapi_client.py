"""
Adaptador para a API BRAPI (https://brapi.dev).

Implementa o Port MarketDataProvider do domínio.

Design decision:
  - httpx.AsyncClient para I/O não-bloqueante
  - tenacity para retry automático em erros transitórios (5xx, timeout)
  - Nunca retorna dict raw — mapeia para tipos do domínio
  - Headers e auth centralizados no __init__

BRAPI OHLC endpoint:
  GET /api/quote/{ticker}?range={range}&interval={interval}
  range:    1d | 5d | 1mo | 3mo | 6mo | 1y | 2y | 5y | 10y | ytd | max
  interval: 1m | 2m | 5m | 15m | 30m | 60m | 90m | 1h | 1d | 5d | 1wk | 1mo | 3mo
"""
from __future__ import annotations

import structlog
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from finanalytics_ai.config import get_settings
from finanalytics_ai.domain.entities.event import OHLCBar
from finanalytics_ai.domain.value_objects.money import Money, Ticker
from finanalytics_ai.exceptions import MarketDataUnavailableError, TransientError
from finanalytics_ai.observability import market_data_requests_total

logger = structlog.get_logger(__name__)

# Mapeamento range → interval padrão para candlestick
RANGE_INTERVAL_MAP: dict[str, str] = {
    "1d":  "5m",
    "5d":  "15m",
    "1mo": "1d",
    "3mo": "1d",
    "6mo": "1wk",
    "1y":  "1wk",
    "2y":  "1mo",
    "5y":  "1mo",
    "max": "1mo",
}


class BrapiClient:
    """
    Cliente assíncrono para a API BRAPI.
    Implementa MarketDataProvider protocol.
    """

    def __init__(self, token: str | None = None, base_url: str | None = None) -> None:
        settings = get_settings()
        self._token = token or settings.brapi_token
        self._base_url = base_url or str(settings.brapi_base_url)
        self._timeout = settings.http_timeout_seconds
        self._max_retries = settings.http_retry_max_attempts
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def get_quote(self, ticker: Ticker) -> Money:
        """Retorna o preço atual do ativo."""
        data = await self._request_with_retry(f"/quote/{ticker}?fundamental=false")
        try:
            price = data["results"][0]["regularMarketPrice"]
            return Money.of(price)
        except (KeyError, IndexError, TypeError) as exc:
            raise MarketDataUnavailableError(
                message=f"Resposta inesperada da BRAPI para {ticker}",
                context={"ticker": str(ticker), "error": str(exc)},
            ) from exc

    async def get_ohlc_bars(
        self,
        ticker: Ticker,
        range_period: str = "3mo",
        interval: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retorna barras OHLC históricas da BRAPI.

        Retorna lista de dicts prontos para o frontend:
          {time, open, high, low, close, volume}

        Design decision: retorna dict ao invés de OHLCBar para evitar
        conversão desnecessária quando o destino é serialização JSON.
        Para persistência usar OHLCBar do domínio.
        """
        resolved_interval = interval or RANGE_INTERVAL_MAP.get(range_period, "1d")
        path = f"/quote/{ticker}?range={range_period}&interval={resolved_interval}&fundamental=false"

        data = await self._request_with_retry(path)

        try:
            result = data["results"][0]
            historical = result.get("historicalDataPrice", [])
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("ohlc.parse_error", ticker=str(ticker), error=str(exc))
            return []

        bars: list[dict[str, Any]] = []
        for bar in historical:
            # BRAPI retorna timestamp Unix em segundos
            ts = bar.get("date")
            o  = bar.get("open")
            h  = bar.get("high")
            l  = bar.get("low")
            c  = bar.get("close")
            v  = bar.get("volume", 0)

            # Filtra barras incompletas
            if not all([ts, o, h, l, c]):
                continue

            bars.append({
                "time":   ts,          # Unix timestamp (segundos) — TradingView aceita diretamente
                "open":   float(o),
                "high":   float(h),
                "low":    float(l),
                "close":  float(c),
                "volume": int(v or 0),
            })

        # Ordena por timestamp (BRAPI às vezes retorna desordenado)
        bars.sort(key=lambda x: x["time"])

        logger.info(
            "ohlc.fetched",
            ticker=str(ticker),
            range=range_period,
            interval=resolved_interval,
            bars=len(bars),
        )
        return bars

    async def get_quote_full(self, ticker: Ticker) -> dict[str, Any]:
        """
        Retorna dados completos do ativo: preço, variação, volume, 52w high/low.
        Usado no painel de detalhes do ticker.
        """
        data = await self._request_with_retry(f"/quote/{ticker}?fundamental=false")
        try:
            r = data["results"][0]
            return {
                "ticker":               r.get("symbol", str(ticker)),
                "name":                 r.get("longName") or r.get("shortName", ""),
                "price":                r.get("regularMarketPrice"),
                "change":               r.get("regularMarketChange"),
                "change_pct":           r.get("regularMarketChangePercent"),
                "volume":               r.get("regularMarketVolume"),
                "market_cap":           r.get("marketCap"),
                "high_52w":             r.get("fiftyTwoWeekHigh"),
                "low_52w":              r.get("fiftyTwoWeekLow"),
                "avg_volume":           r.get("averageDailyVolume3Month"),
                "previous_close":       r.get("regularMarketPreviousClose"),
                "market_time":          r.get("regularMarketTime"),
                "currency":             r.get("currency", "BRL"),
                "exchange":             r.get("exchange", ""),
            }
        except (KeyError, IndexError, TypeError) as exc:
            raise MarketDataUnavailableError(
                message=f"Resposta inesperada da BRAPI para {ticker}",
                context={"ticker": str(ticker), "error": str(exc)},
            ) from exc

    async def search_assets(self, query: str) -> list[dict[str, str]]:
        data = await self._request_with_retry(f"/quote/list?search={query}")
        stocks = data.get("stocks", [])
        return [{"ticker": s.get("stock", ""), "name": s.get("name", "")} for s in stocks]

    async def is_healthy(self) -> bool:
        try:
            await self._request_with_retry("/quote/PETR4?fundamental=false")
            return True
        except Exception:
            return False

    async def _request_with_retry(self, path: str) -> dict[str, Any]:
        """HTTP GET com retry automático para erros transitórios."""
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(TransientError),
            reraise=True,
        ):
            with attempt:
                return await self._request(path)
        raise MarketDataUnavailableError(message="Retry esgotado", context={"path": path})

    async def _request(self, path: str) -> dict[str, Any]:
        client = await self._get_client()
        try:
            response = await client.get(path)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            market_data_requests_total.labels(provider="brapi", status="error").inc()
            raise TransientError(
                message=f"Timeout/conexão BRAPI: {exc}",
                context={"path": path},
            ) from exc

        if response.status_code >= 500:
            market_data_requests_total.labels(provider="brapi", status="5xx").inc()
            raise TransientError(
                message=f"BRAPI 5xx: {response.status_code}",
                context={"path": path, "status": str(response.status_code)},
            )
        if response.status_code == 429:
            market_data_requests_total.labels(provider="brapi", status="429").inc()
            raise TransientError(message="Rate limit BRAPI", context={"path": path})
        if response.status_code >= 400:
            market_data_requests_total.labels(provider="brapi", status="4xx").inc()
            raise MarketDataUnavailableError(
                message=f"BRAPI erro {response.status_code}: {path}",
                context={"path": path, "status": str(response.status_code)},
            )

        market_data_requests_total.labels(provider="brapi", status="ok").inc()
        return response.json()  # type: ignore[no-any-return]
