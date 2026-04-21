"""
finanalytics_ai.infrastructure.adapters.dados_mercado_client
─────────────────────────────────────────────────────────────
Adapter para a API do Dados de Mercado (https://www.dadosdemercado.com.br/api).

Agrega dados de fontes primárias (CVM, BCB, ANBIMA, B3) numa API REST
unificada com token gratuito. Funciona como fonte complementar ao BRAPI,
com foco em fundamentais de empresas, títulos públicos e macro.

Endpoints utilizados:

  /v1/companies           → lista empresas listadas (com indicadores)
  /v1/companies/{ticker}  → dados detalhados de uma empresa
  /v1/tickers             → cotações de ativos da B3
  /v1/tickers/{ticker}    → cotação de um ativo específico
  /v1/indices             → índices de mercado (IBOV, IFIX, etc.)
  /v1/macro               → indicadores econômicos (SELIC, IPCA, etc.)
  /v1/focus               → Boletim Focus (alternativa ao BCB Olinda)
  /v1/treasuries          → títulos do Tesouro Direto
  /v1/risk_indicators     → VIX, CDS, DXY e outros

Base URL: https://api.dadosdemercado.com.br/v1
Auth: Bearer token no header Authorization
Token gratuito: https://www.dadosdemercado.com.br/conta

Design decisions:

  Posição complementar ao BrapiClient:
    - BRAPI: melhor para cotações em tempo real e OHLC intraday
    - Dados de Mercado: melhor para fundamentais, macro, Focus e
      indicadores de risco (que a BRAPI não oferece)
    - Não substituímos o CompositeMarketDataClient; adicionamos um
      novo cliente especializado para os endpoints que faltam

  Token opcional com degradação graciosa:
    Se DADOS_MERCADO_TOKEN não estiver configurado, o cliente loga
    warning mas não falha no startup. Endpoints sem token retornam
    erro 401 que é capturado e retorna lista vazia.

  Mapeamento de campo snake_case:
    A API retorna camelCase em alguns campos. Normalizamos para
    snake_case para consistência com o restante do domínio.

  Cache por endpoint:
    - Cotações: 5 min (mudam rapidamente)
    - Fundamentais: 60 min (mudam com balanços trimestrais)
    - Macro/Focus: 4h (atualizados diariamente/semanalmente)
    - Tesouro: 30 min (B3 atualiza a cada 30 min)
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from finanalytics_ai.config import get_settings
from finanalytics_ai.exceptions import MarketDataUnavailableError, TransientError

logger = structlog.get_logger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

_BASE_URL = "https://api.dadosdemercado.com.br/v1"

_CACHE_TTLS = {
    "tickers": 300,  # 5 min
    "companies": 3_600,  # 60 min
    "macro": 14_400,  # 4h
    "focus": 14_400,  # 4h
    "treasuries": 1_800,  # 30 min
    "risk": 900,  # 15 min
    "indices": 300,  # 5 min
}

_HTTP_TIMEOUT = 30.0
_MAX_RETRIES = 3


class DadosDeMercadoClient:
    """
    Adapter assíncrono para a API Dados de Mercado.

    Cobertura:
      - Cotações e OHLC de ações B3
      - Dados fundamentalistas completos (BP, DRE, indicadores)
      - Índices de mercado
      - Indicadores macro (SELIC, IPCA, câmbio)
      - Boletim Focus (alternativa ao BCB Olinda)
      - Tesouro Direto
      - Indicadores de risco (VIX, CDS Brasil, DXY)
    """

    def __init__(self, token: str | None = None) -> None:
        settings = get_settings()
        self._token = token or getattr(settings, "dados_mercado_token", "")
        if not self._token:
            logger.warning(
                "dados_mercado.token_missing",
                hint="Defina DADOS_MERCADO_TOKEN no .env para ativar este provider.",
            )
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Accept": "application/json"}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            self._client = httpx.AsyncClient(
                base_url=_BASE_URL,
                headers=headers,
                timeout=_HTTP_TIMEOUT,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Cotações ──────────────────────────────────────────────────────────────

    async def get_quote(self, ticker: str) -> dict[str, Any]:
        """
        Retorna cotação atual de um ativo.

        Returns:
            dict com: ticker, preco, variacao, variacao_pct, volume,
            abertura, fechamento_anterior, data.
        """
        cache_key = f"quote_{ticker}"
        cached = self._get_cache(cache_key, _CACHE_TTLS["tickers"])
        if cached is not None:
            return cached  # type: ignore[return-value]

        try:
            data = await self._request(f"/tickers/{ticker}")
            result = _parse_ticker(data)
            self._set_cache(cache_key, result)
            return result
        except Exception as exc:
            logger.warning("dados_mercado.quote.failed", ticker=ticker, error=str(exc)[:80])
            return {}

    async def get_all_tickers(self) -> list[dict[str, Any]]:
        """
        Lista todos os ativos disponíveis com cotações.

        Útil para o screener e para popular a lista de tickers.
        """
        cache_key = "all_tickers"
        cached = self._get_cache(cache_key, _CACHE_TTLS["tickers"])
        if cached is not None:
            return cached  # type: ignore[return-value]

        try:
            data = await self._request("/tickers")
            results = [_parse_ticker(t) for t in (data if isinstance(data, list) else [])]
            self._set_cache(cache_key, results)
            logger.info("dados_mercado.tickers.fetched", count=len(results))
            return results
        except Exception as exc:
            logger.warning("dados_mercado.tickers.failed", error=str(exc)[:80])
            return []

    # ── Empresas e Fundamentais ───────────────────────────────────────────────

    async def get_company(self, ticker: str) -> dict[str, Any]:
        """
        Retorna dados fundamentalistas completos de uma empresa.

        Inclui: BP, DRE, indicadores (P/L, P/VP, ROE, ROIC, EV/EBITDA),
        histórico de dividendos e dados de mercado.
        """
        cache_key = f"company_{ticker}"
        cached = self._get_cache(cache_key, _CACHE_TTLS["companies"])
        if cached is not None:
            return cached  # type: ignore[return-value]

        try:
            data = await self._request(f"/companies/{ticker}")
            result = _parse_company(data)
            self._set_cache(cache_key, result)
            return result
        except Exception as exc:
            logger.warning("dados_mercado.company.failed", ticker=ticker, error=str(exc)[:80])
            return {}

    async def get_companies(self) -> list[dict[str, Any]]:
        """
        Lista todas as empresas com indicadores fundamentalistas básicos.

        Útil como base de dados para o screener.
        """
        cache_key = "companies"
        cached = self._get_cache(cache_key, _CACHE_TTLS["companies"])
        if cached is not None:
            return cached  # type: ignore[return-value]

        try:
            data = await self._request("/companies")
            results = [_parse_company(c) for c in (data if isinstance(data, list) else [])]
            self._set_cache(cache_key, results)
            logger.info("dados_mercado.companies.fetched", count=len(results))
            return results
        except Exception as exc:
            logger.warning("dados_mercado.companies.failed", error=str(exc)[:80])
            return []

    # ── Índices ───────────────────────────────────────────────────────────────

    async def get_indices(self) -> list[dict[str, Any]]:
        """
        Retorna índices de mercado: IBOV, IFIX, SMLL, IDIV, IMAB, etc.

        Returns:
            Lista de dicts com: indice, valor, variacao_pct, data.
        """
        cache_key = "indices"
        cached = self._get_cache(cache_key, _CACHE_TTLS["indices"])
        if cached is not None:
            return cached  # type: ignore[return-value]

        try:
            data = await self._request("/indices")
            results = data if isinstance(data, list) else []
            self._set_cache(cache_key, results)
            return results
        except Exception as exc:
            logger.warning("dados_mercado.indices.failed", error=str(exc)[:80])
            return []

    # ── Macro ─────────────────────────────────────────────────────────────────

    async def get_macro_indicators(self) -> dict[str, Any]:
        """
        Retorna indicadores macroeconômicos atuais.

        Inclui: SELIC, CDI, IPCA, IGP-M, câmbio (USD, EUR, GBP),
        Tesouro SELIC e outros indicadores BCB.

        Útil como alternativa/complemento ao macro_collector.py.
        """
        cache_key = "macro"
        cached = self._get_cache(cache_key, _CACHE_TTLS["macro"])
        if cached is not None:
            return cached  # type: ignore[return-value]

        try:
            data = await self._request("/macro")
            result = data if isinstance(data, dict) else {}
            self._set_cache(cache_key, result)
            return result
        except Exception as exc:
            logger.warning("dados_mercado.macro.failed", error=str(exc)[:80])
            return {}

    async def get_focus_expectations(self) -> list[dict[str, Any]]:
        """
        Retorna expectativas do Boletim Focus via Dados de Mercado.

        Alternativa ao FocusClient direto — útil como fallback.
        """
        cache_key = "focus"
        cached = self._get_cache(cache_key, _CACHE_TTLS["focus"])
        if cached is not None:
            return cached  # type: ignore[return-value]

        try:
            data = await self._request("/focus")
            results = data if isinstance(data, list) else []
            self._set_cache(cache_key, results)
            return results
        except Exception as exc:
            logger.warning("dados_mercado.focus.failed", error=str(exc)[:80])
            return []

    # ── Tesouro Direto ────────────────────────────────────────────────────────

    async def get_treasuries(self) -> list[dict[str, Any]]:
        """
        Retorna títulos do Tesouro Direto disponíveis.

        Complementa o TesouroDiretoClient com dados adicionais como
        histórico de preços e taxas.
        """
        cache_key = "treasuries"
        cached = self._get_cache(cache_key, _CACHE_TTLS["treasuries"])
        if cached is not None:
            return cached  # type: ignore[return-value]

        try:
            data = await self._request("/treasuries")
            results = data if isinstance(data, list) else []
            self._set_cache(cache_key, results)
            logger.info("dados_mercado.treasuries.fetched", count=len(results))
            return results
        except Exception as exc:
            logger.warning("dados_mercado.treasuries.failed", error=str(exc)[:80])
            return []

    # ── Indicadores de Risco ──────────────────────────────────────────────────

    async def get_risk_indicators(self) -> dict[str, Any]:
        """
        Retorna indicadores de risco globais e locais.

        Inclui: VIX (volatilidade S&P), CDS Brasil (risco soberano),
        DXY (índice dólar), Ibovespa Futuro, DI Futuro e outros.

        Estes dados não estão disponíveis no BRAPI ou BCB diretamente.
        """
        cache_key = "risk"
        cached = self._get_cache(cache_key, _CACHE_TTLS["risk"])
        if cached is not None:
            return cached  # type: ignore[return-value]

        try:
            data = await self._request("/risk_indicators")
            result = data if isinstance(data, dict) else {}
            self._set_cache(cache_key, result)
            return result
        except Exception as exc:
            logger.warning("dados_mercado.risk.failed", error=str(exc)[:80])
            return {}

    # ── Health check ──────────────────────────────────────────────────────────

    async def is_healthy(self) -> bool:
        """Health check: verifica conectividade com a API."""
        if not self._token:
            return False
        try:
            await self._request("/tickers?limit=1")
            return True
        except Exception:
            return False

    # ── Internos ──────────────────────────────────────────────────────────────

    async def _request(self, path: str) -> Any:
        """GET com retry automático para erros transitórios."""
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(_MAX_RETRIES),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(TransientError),
            reraise=True,
        ):
            with attempt:
                return await self._do_get(path)
        raise MarketDataUnavailableError(
            message="Retry esgotado",
            context={"path": path, "provider": "dados_mercado"},
        )

    async def _do_get(self, path: str) -> Any:
        client = await self._get_client()
        try:
            resp = await client.get(path)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise TransientError(
                message=f"Timeout/conexão Dados de Mercado: {exc}",
                context={"path": path},
            ) from exc

        if resp.status_code >= 500:
            raise TransientError(
                message=f"Dados de Mercado 5xx: {resp.status_code}",
                context={"path": path},
            )
        if resp.status_code == 401:
            logger.error(
                "dados_mercado.unauthorized",
                hint="Verifique DADOS_MERCADO_TOKEN no .env",
            )
            raise MarketDataUnavailableError(
                message="Token inválido ou ausente",
                context={"path": path},
            )
        if resp.status_code == 429:
            raise TransientError(
                message="Rate limit Dados de Mercado",
                context={"path": path},
            )
        if resp.status_code >= 400:
            raise MarketDataUnavailableError(
                message=f"Dados de Mercado {resp.status_code}: {path}",
                context={"path": path, "status": str(resp.status_code)},
            )

        logger.debug("dados_mercado.request.ok", path=path)
        return resp.json()

    # ── Cache ─────────────────────────────────────────────────────────────────

    _cache: dict[str, tuple[float, Any]] = {}

    def _get_cache(self, key: str, ttl: float) -> Any | None:
        if key in self._cache:
            ts, val = self._cache[key]
            if time.time() - ts < ttl:
                return val
        return None

    def _set_cache(self, key: str, value: Any) -> None:
        self._cache[key] = (time.time(), value)


# ── Parsers ───────────────────────────────────────────────────────────────────


def _parse_ticker(data: dict[str, Any]) -> dict[str, Any]:
    """Normaliza resposta do endpoint /tickers para snake_case."""
    return {
        "ticker": data.get("ticker", ""),
        "nome": data.get("name", data.get("nome", "")),
        "preco": _safe_float(data.get("price", data.get("preco"))),
        "variacao": _safe_float(data.get("change", data.get("variacao"))),
        "variacao_pct": _safe_float(data.get("changePercent", data.get("variacao_pct"))),
        "volume": _safe_float(data.get("volume")),
        "abertura": _safe_float(data.get("open", data.get("abertura"))),
        "fechamento_anterior": _safe_float(data.get("previousClose")),
        "data": data.get("date", data.get("data", "")),
        "mercado_cap": _safe_float(data.get("marketCap")),
    }


def _parse_company(data: dict[str, Any]) -> dict[str, Any]:
    """Normaliza resposta do endpoint /companies."""
    return {
        "ticker": data.get("ticker", ""),
        "nome": data.get("name", data.get("nome", "")),
        "setor": data.get("sector", data.get("setor", "")),
        "subsetor": data.get("subSector", data.get("subsetor", "")),
        "pl": _safe_float(data.get("priceToEarnings", data.get("pl"))),
        "pvp": _safe_float(data.get("priceToBook", data.get("pvp"))),
        "roe": _safe_float(data.get("roe")),
        "roic": _safe_float(data.get("roic")),
        "ev_ebitda": _safe_float(data.get("evToEbitda", data.get("ev_ebitda"))),
        "dividend_yield": _safe_float(data.get("dividendYield", data.get("dividend_yield"))),
        "margem_bruta": _safe_float(data.get("grossMargin", data.get("margem_bruta"))),
        "margem_ebitda": _safe_float(data.get("ebitdaMargin")),
        "margem_liquida": _safe_float(data.get("netMargin", data.get("margem_liquida"))),
        "divida_pl": _safe_float(data.get("debtToEquity")),
        "market_cap": _safe_float(data.get("marketCap")),
        "vpa": _safe_float(data.get("bookValuePerShare", data.get("vpa"))),
        "lpa": _safe_float(data.get("earningsPerShare", data.get("lpa"))),
    }


def _safe_float(value: Any) -> float | None:
    if value is None or str(value).strip() in ("", "nan", "NaN", "None"):
        return None
    try:
        return float(str(value).replace(",", "."))
    except (ValueError, TypeError):
        return None


# ── Singleton ─────────────────────────────────────────────────────────────────

_client: DadosDeMercadoClient | None = None


def get_dados_mercado_client() -> DadosDeMercadoClient:
    global _client
    if _client is None:
        _client = DadosDeMercadoClient()
    return _client
