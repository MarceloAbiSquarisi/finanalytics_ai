"""
finanalytics_ai.infrastructure.adapters.market_data_client
────────────────────────────────────────────────────────────
CompositeMarketDataClient: BRAPI como primário, Yahoo como fallback.

Motivação:

  BRAPI é excelente para cotações em tempo real e dados recentes, mas:
    1. Requer token (pode expirar / rate limit)
    2. Ranges longos (5y, 10y) podem retornar poucos pontos
    3. Instabilidade temporária afeta backtests

  Yahoo Finance tem:
    1. Dados históricos profundos e gratuitos
    2. Suporte nativo a 5y, 10y, max
    3. Boa cobertura de ativos B3

  Estratégia de fallback:
    1. Tenta BRAPI
    2. Se retornar < MIN_BARS barras → tenta Yahoo como complemento
    3. Se BRAPI falhar com exceção → tenta Yahoo
    4. Mescla: usa BRAPI para dados recentes (último 1y) e Yahoo para
       dados históricos mais antigos quando range > 2y

Design do CompositeMarketDataClient:

  Structural subtyping (duck typing):
    Os services dependem de `BrapiClient` no type hint mas na prática
    só chamam get_ohlc_bars(). O CompositeMarketDataClient implementa
    todos os métodos de BrapiClient para ser substituível sem mudanças
    nos services.

  Sem herança:
    Não herda de BrapiClient. Usa composição (tem um BrapiClient e um
    YahooFinanceClient). Isso segue o princípio de composição sobre
    herança e evita acoplamento com detalhes internos do BrapiClient.

  Ranges estendidos suportados:
    "1mo", "3mo", "6mo", "1y", "2y" → BRAPI primário
    "5y", "10y", "ytd", "max"       → Yahoo direto (BRAPI não suporta bem)
    Qualquer range → Yahoo fallback se BRAPI falhar

  Métricas:
    Registra source (brapi|yahoo|merged) no structured log.
    Expõe contador de fallbacks via prometheus (opcional).

  MIN_BARS:
    Threshold para considerar resultado "insuficiente" do BRAPI.
    30 barras = ~1.5 meses de dados diários. Abaixo disso, o backtest
    não teria warmup suficiente para a maioria das estratégias.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient
from finanalytics_ai.infrastructure.adapters.fintz_market_client import FintzMarketDataClient
from finanalytics_ai.infrastructure.adapters.yahoo_client import YahooFinanceClient

if TYPE_CHECKING:
    from finanalytics_ai.domain.value_objects.money import Money, Ticker

logger = structlog.get_logger(__name__)

# Ranges que BRAPI não suporta bem → vai direto para Yahoo
YAHOO_PREFERRED_RANGES = frozenset({"5y", "10y", "ytd", "max"})

# Mínimo de barras do BRAPI antes de tentar Yahoo como complemento
MIN_BARS_THRESHOLD = 30


class CompositeMarketDataClient:
    """
    Cliente composto: BRAPI → Yahoo Finance fallback.

    Substitui BrapiClient em todos os services via duck typing.
    Mesma interface pública, comportamento enriquecido.
    """

    def __init__(
        self,
        brapi_client: BrapiClient,
        yahoo_client: YahooFinanceClient | None = None,
    ) -> None:
        self._brapi = brapi_client
        self._yahoo = yahoo_client or YahooFinanceClient()
        self._fintz = FintzMarketDataClient()

    # ── Interface principal ───────────────────────────────────────────────────

    async def get_ohlc_bars(
        self,
        ticker: Any,
        range_period: str = "1y",
        interval: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Busca OHLC com fallback automático BRAPI → Yahoo.

        Estratégia por range:
          - Ranges curtos (≤2y):  BRAPI primeiro; fallback Yahoo se insuficiente
          - Ranges longos (>2y):  Yahoo direto (mais confiável para séries longas)
        """
        # Ranges longos → Yahoo direto
        if range_period in YAHOO_PREFERRED_RANGES:
            return await self._fetch_yahoo_only(ticker, range_period, interval)

        # 1. Tenta Fintz (banco local - zero latencia de rede)
        fintz_bars = await self._fetch_fintz_safe(ticker, range_period, interval)
        if len(fintz_bars) >= MIN_BARS_THRESHOLD:
            logger.info(
                "market_data.source.fintz",
                ticker=str(ticker),
                range=range_period,
                bars=len(fintz_bars),
            )
            return fintz_bars

        # 2. Tenta BRAPI
        brapi_bars = await self._fetch_brapi_safe(ticker, range_period, interval)

        if len(brapi_bars) >= MIN_BARS_THRESHOLD:
            logger.debug(
                "market_data.source.brapi",
                ticker=str(ticker),
                range=range_period,
                bars=len(brapi_bars),
            )
            return brapi_bars

        # BRAPI insuficiente → tenta Yahoo
        logger.info(
            "market_data.fallback.yahoo",
            ticker=str(ticker),
            range=range_period,
            brapi_bars=len(brapi_bars),
            reason="insufficient_bars" if brapi_bars else "empty_response",
        )

        # Instrumentação de fallback (best-effort, não bloqueia)
        try:
            from finanalytics_ai.metrics import brapi_requests_total

            brapi_requests_total.labels(endpoint="ohlc_fallback", status="yahoo").inc()
        except Exception:
            pass

        yahoo_bars = await self._fetch_yahoo_safe(ticker, range_period, interval)

        if yahoo_bars:
            logger.info(
                "market_data.source.yahoo",
                ticker=str(ticker),
                range=range_period,
                bars=len(yahoo_bars),
            )
            return yahoo_bars

        # Ambos falharam → retorna o que o BRAPI tinha (pode ser vazio)
        logger.warning(
            "market_data.both_failed",
            ticker=str(ticker),
            range=range_period,
        )
        return brapi_bars

    async def _fetch_fintz_safe(
        self,
        ticker: Any,
        range_period: str,
        interval: str | None,
    ) -> list[dict[str, Any]]:
        try:
            return await self._fintz.get_ohlc_bars(
                ticker, range_period=range_period, interval=interval
            )
        except Exception as exc:
            logger.warning("market_data.fintz_error", ticker=str(ticker), error=str(exc))
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

    async def _fetch_yahoo_only(
        self,
        ticker: Any,
        range_period: str,
        interval: str | None,
    ) -> list[dict[str, Any]]:
        bars = await self._fetch_yahoo_safe(ticker, range_period, interval)
        if not bars:
            # Último recurso: tenta BRAPI com o range mais longo que suporta
            logger.info(
                "market_data.yahoo_failed.brapi_fallback",
                ticker=str(ticker),
                range=range_period,
            )
            return await self._fetch_brapi_safe(ticker, "2y", interval)
        return bars

    # ── Delegações para manter compatibilidade com BrapiClient ───────────────

    async def get_quote(self, ticker: Ticker) -> Money:
        return await self._brapi.get_quote(ticker)

    async def get_quote_full(self, ticker: Ticker) -> dict[str, Any]:
        return await self._brapi.get_quote_full(ticker)

    async def get_fundamentals_batch(self, tickers: list[str]) -> list[dict[str, Any]]:
        return await self._brapi.get_fundamentals_batch(tickers)

    async def search_assets(self, query: str) -> list[dict[str, str]]:
        return await self._brapi.search_assets(query)

    async def is_healthy(self) -> bool:
        brapi_ok = await self._brapi.is_healthy()
        yahoo_ok = await self._yahoo.is_healthy()
        logger.info("market_data.health", brapi=brapi_ok, yahoo=yahoo_ok)
        return brapi_ok or yahoo_ok  # Saudável se pelo menos um disponível

    async def close(self) -> None:
        await self._brapi.close()
        await self._yahoo.close()


# ── Factory ───────────────────────────────────────────────────────────────────


def create_market_data_client(brapi_token: str | None = None) -> CompositeMarketDataClient:
    """
    Cria o cliente composto com token BRAPI.
    Se token ausente, BRAPI falhará e Yahoo será o primário de fato.
    """
    brapi = BrapiClient()
    yahoo = YahooFinanceClient()
    client = CompositeMarketDataClient(brapi, yahoo)
    logger.info(
        "market_data_client.created",
        brapi_configured=bool(brapi_token),
        yahoo_available=True,
    )
    return client


def create_cached_market_data_client(
    brapi_token: str | None = None,
    session_factory: Any | None = None,
) -> CompositeMarketDataClient:
    """Alias de create_market_data_client mantido para compatibilidade."""
    return create_market_data_client(brapi_token)
