"""
finanalytics_ai.domain.fintz.ports
────────────────────────────────────
Ports (interfaces) do domínio Fintz — dependency inversion.

O service depende deste Protocol, não da implementação concreta.
Isso permite mockar o repositório nos testes sem tocar o banco.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import pandas as pd

    from finanalytics_ai.domain.fintz.entities import FintzDatasetSpec


@runtime_checkable
class FintzRepository(Protocol):
    """
    Contrato do repositório de dados Fintz.

    Implementado por FintzRepo (infrastructure/database/repositories/fintz_repo.py).
    """

    async def get_last_hash(self, dataset_key: str) -> str | None:
        """
        Retorna o hash SHA-256 da última sincronização bem-sucedida.

        Retorna None se o dataset nunca foi sincronizado.
        """
        ...

    async def upsert_cotacoes(self, df: pd.DataFrame) -> int:
        """
        Faz upsert das cotações OHLC.

        Returns: número de linhas inseridas/atualizadas.
        """
        ...

    async def upsert_itens_contabeis(self, df: pd.DataFrame, spec: FintzDatasetSpec) -> int:
        """
        Faz upsert dos itens contábeis PIT.

        Returns: número de linhas inseridas/atualizadas.
        """
        ...

    async def upsert_indicadores(self, df: pd.DataFrame, spec: FintzDatasetSpec) -> int:
        """
        Faz upsert dos indicadores PIT.

        Returns: número de linhas inseridas/atualizadas.
        """
        ...

    async def record_sync(
        self,
        dataset_key: str,
        file_hash: str,
        rows_upserted: int,
        status: str,
        error_message: str | None = None,
    ) -> None:
        """Registra ou atualiza o resultado do sync no log de idempotência."""
        ...
