"""
Port: MarketDataProvider

Protocol que define o contrato para qualquer fonte de dados de mercado.
Implementações concretas ficam em infrastructure/adapters/.

Design decision: Protocol (structural subtyping) ao invés de ABC.
Permite que qualquer classe que implemente os métodos seja aceita
sem herança explícita — duck typing com checagem estática do mypy.

--- get_ohlc_bars: por que range_period/interval e não timeframe/limit? ---

As três implementações concretas (BrapiClient, YahooClient,
CompositeMarketDataClient) e todos os call sites no código de aplicação
usam `range_period` (ex: "3mo", "1y") e `interval` (ex: "1d", "1m").
Esse é o vocabulário da BRAPI e do Yahoo Finance — os dois provedores reais.

A versão anterior do Protocol tinha `timeframe: str, limit: int = 100` e
`-> list[OHLCBar]`, que nunca foi satisfeita por nenhuma implementação.
O Protocol descrevia o que gostaríamos, não o que o sistema faz.

Regra: o Protocol é o contrato descrito pelas implementações existentes,
não um ideal abstrato que força adaptações desnecessárias nos adaptadores.
O `cast()` em dependencies.py e o `type: ignore` em main.py eram sintomas
desse desalinhamento — ambos foram removidos com esta correção.

--- OHLCBar e o retorno list[dict] ---

`get_ohlc_bars` retorna `list[dict[str, Any]]` — estrutura raw pronta para
serialização JSON e consumo direto pelas rotas. A conversão para `OHLCBar`
(entidade de domínio) é responsabilidade exclusiva da camada de persistência
(ex: TimescalePriceTickRepository). Isso evita conversão desnecessária em
contextos onde o dado vai direto para o frontend sem persistir.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from finanalytics_ai.domain.value_objects.money import Money, Ticker


@runtime_checkable
class MarketDataProvider(Protocol):
    """Contrato para provedores de dados de mercado (BRAPI, XP, BTG)."""

    async def get_quote(self, ticker: Ticker) -> Money:
        """Retorna o preço atual do ativo."""
        ...

    async def get_ohlc_bars(
        self,
        ticker: Ticker,
        range_period: str = "3mo",
        interval: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retorna barras OHLC históricas.

        Args:
            ticker: símbolo do ativo
            range_period: janela temporal — "1d", "5d", "1mo", "3mo", "1y", "5y"
            interval: granularidade — "1m", "5m", "1h", "1d". None = automático.

        Returns:
            Lista de dicts {time, open, high, low, close, volume} por time.
            time é Unix timestamp em segundos.
        """
        ...

    async def search_assets(self, query: str) -> list[dict[str, str]]:
        """Busca ativos por nome ou ticker."""
        ...

    async def is_healthy(self) -> bool:
        """Health check da conexão com a fonte de dados."""
        ...
