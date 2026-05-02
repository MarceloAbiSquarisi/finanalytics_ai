"""
Adaptador para a API BRAPI (https://brapi.dev).

Implementa o Port MarketDataProvider do domínio.

Design decision:
  - httpx.AsyncClient para I/O não-bloqueante
  - tenacity para retry automático em erros transitórios (5xx, timeout)
  - Nunca retorna dict raw — mapeia para tipos do domínio
  - Headers e auth centralizados no __init__
"""

from __future__ import annotations

import structlog
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
        data = await self._request_with_retry(f"/quote/{ticker}")
        try:
            price = data["results"][0]["regularMarketPrice"]
            return Money.of(price)
        except (KeyError, IndexError, TypeError) as exc:
            raise MarketDataUnavailableError(
                message=f"Resposta inesperada da BRAPI para {ticker}",
                context={"ticker": str(ticker), "error": str(exc)},
            ) from exc

    async def get_ohlc_bars(
        self, ticker: Ticker, timeframe: str = "1d", limit: int = 100
    ) -> list[OHLCBar]:
        """Retorna barras OHLC históricas."""
        params = f"range={timeframe}&interval={timeframe}&fundamental=false"
        data = await self._request_with_retry(f"/quote/{ticker}?{params}")
        # TODO: mapear historical data da BRAPI para OHLCBar
        return []

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
