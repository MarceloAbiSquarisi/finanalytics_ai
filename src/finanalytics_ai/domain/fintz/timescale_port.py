"""
Port para escrita de dados Fintz no TimescaleDB.

Por que separado do FintzRepository?
  - FintzRepository = OLTP Postgres (source of truth, idempotência, hashes)
  - TimescaleWriter  = TimescaleDB (séries temporais, compressão, queries analíticas)

Por que gravar ANTES de publicar o evento?
  - O DataFrame está disponível no FintzSyncService, não no payload do evento.
  - Quando EventProcessor processar a regra, os dados já estão no TimescaleDB.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TimescaleWriter(Protocol):
    """Contrato para escrita de dados Fintz no TimescaleDB."""

    async def write(self, df: Any, spec: Any) -> int:
        """Despacha para o método correto baseado em spec.dataset_type."""
        ...

    async def write_cotacoes(self, df: Any) -> int:
        """Grava cotações em fintz_cotacoes_ts. Retorna linhas escritas."""
        ...

    async def write_itens_contabeis(self, df: Any, spec: Any) -> int:
        """Grava itens contábeis em fintz_itens_contabeis_ts."""
        ...

    async def write_indicadores(self, df: Any, spec: Any) -> int:
        """Grava indicadores em fintz_indicadores_ts."""
        ...

    async def close(self) -> None:
        """Fecha conexões."""
        ...
