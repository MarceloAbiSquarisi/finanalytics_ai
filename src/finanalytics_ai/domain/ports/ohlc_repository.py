"""Port: OHLCBarRepository — persistência de barras OHLC.

Design decision: Protocol com runtime_checkable permite usar isinstance()
nos testes para verificar conformidade sem herança. O método upsert_bar
garante idempotência via ON CONFLICT DO NOTHING — barras OHLC são
identificadas pelo par (ticker, timestamp, timeframe).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from finanalytics_ai.domain.entities.event import OHLCBar


@runtime_checkable
class OHLCBarRepository(Protocol):
    async def upsert_bar(self, bar: OHLCBar) -> bool:
        """
        Persiste uma barra OHLC. Retorna True se inserida, False se já existia.

        Idempotência: (ticker, timestamp, timeframe) é chave única.
        Chamadas repetidas com o mesmo bar são no-ops seguros.
        """
        ...

    async def get_latest(self, ticker: str, timeframe: str, limit: int = 100) -> list[OHLCBar]:
        """Retorna as `limit` barras mais recentes para o ticker/timeframe."""
        ...
