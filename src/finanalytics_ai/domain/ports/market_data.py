"""
Port: MarketDataProvider

Protocol que define o contrato para qualquer fonte de dados de mercado.
Implementações concretas ficam em infrastructure/adapters/.

Design decision: Protocol (structural subtyping) ao invés de ABC.
Permite que qualquer classe que implemente os métodos seja aceita
sem herança explícita — duck typing com checagem estática do mypy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from finanalytics_ai.domain.entities.event import OHLCBar
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
        timeframe: str,
        limit: int = 100,
    ) -> list[OHLCBar]:
        """Retorna barras OHLC históricas."""
        ...

    async def search_assets(self, query: str) -> list[dict[str, str]]:
        """Busca ativos por nome ou ticker."""
        ...

    async def is_healthy(self) -> bool:
        """Health check da conexão com a fonte de dados."""
        ...
