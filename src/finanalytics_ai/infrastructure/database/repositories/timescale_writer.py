"""
TimescaleDB writer — implementação concreta do TimescaleWriter port.

Usa asyncpg COPY protocol para máximo throughput (10-100x vs INSERT).
Engine separado do OLTP (porta 5433).

Decisão de falha não-fatal:
  O sync Postgres já completou quando este writer é chamado.
  Se o TimescaleDB falhar, retornamos -1 e logamos — não abortamos.
  O próximo sync Fintz reescreverá os dados (idempotente por natureza).
"""

from __future__ import annotations

import asyncpg
from typing import TYPE_CHECKING, Any

from finanalytics_ai.observability.logging import get_logger

if TYPE_CHECKING:
    import pandas as pd
    from finanalytics_ai.domain.fintz.entities import FintzDatasetSpec

log = get_logger(__name__)


class PgTimescaleWriter:
    """Escreve DataFrames Fintz nas hypertables do TimescaleDB via COPY protocol."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn,
                min_size=2,
                max_size=8,
                statement_cache_size=0,
            )
        return self._pool

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def write(self, df: Any, spec: Any) -> int:
        """Despacha para o método correto baseado em dataset_type."""
        dt = getattr(spec, "dataset_type", "")
        if dt == "cotacoes":
            return await self.write_cotacoes(df)
        if dt == "item_contabil":
            return await self.write_itens_contabeis(df, spec)
        if dt == "indicador":
            return await self.write_indicadores(df, spec)
        log.warning("timescale_writer.unknown_type", dataset_type=dt)
        return 0

    async def write_cotacoes(self, df: Any) -> int:
        col_map = {
            "data": "time",
            "precoFechamento": "preco_fechamento",
            "precoFechamentoAjustado": "preco_fechamento_ajustado",
            "precoAbertura": "preco_abertura",
            "precoMinimo": "preco_minimo",
            "precoMaximo": "preco_maximo",
            "volumeNegociado": "volume_negociado",
            "fatorAjuste": "fator_ajuste",
            "precoMedio": "preco_medio",
            "quantidadeNegociada": "quantidade_negociada",
            "quantidadeNegocios": "quantidade_negocios",
            "fatorAjusteDesdobramentos": "fator_ajuste_desdobramentos",
            "precoFechamentoAjustadoDesdobramentos": "preco_fechamento_ajustado_desdobramentos",
        }
        columns = ["time", "ticker"] + [v for v in col_map.values() if v != "time"]
        return await self._write_df(df, "fintz_cotacoes_ts", columns, col_map)

    async def write_itens_contabeis(self, df: Any, spec: Any) -> int:
        col_map = {
            "data": "time",
            "item": "item",
            "tipoPeriodo": "tipo_periodo",
            "valor": "valor",
        }
        return await self._write_df(
            df, "fintz_itens_contabeis_ts",
            ["time", "ticker", "item", "tipo_periodo", "valor"], col_map
        )

    async def write_indicadores(self, df: Any, spec: Any) -> int:
        col_map = {
            "data": "time",
            "indicador": "indicador",
            "valor": "valor",
        }
        return await self._write_df(
            df, "fintz_indicadores_ts",
            ["time", "ticker", "indicador", "valor"], col_map
        )

    async def _write_df(
        self,
        df: Any,
        table: str,
        columns: list[str],
        col_map: dict[str, str],
    ) -> int:
        import pandas as pd

        if df is None or (hasattr(df, "empty") and df.empty):
            return 0

        try:
            df = df.rename(columns=col_map)
            df["time"] = pd.to_datetime(df["time"]).dt.tz_localize("UTC")
            available = [c for c in columns if c in df.columns]
            df = df[available]

            records = [
                tuple(None if (v != v) else v for v in row)
                for row in df.itertuples(index=False, name=None)
            ]

            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.copy_records_to_table(
                    table, records=records, columns=available
                )

            log.info("timescale_writer.write_ok", table=table, rows=len(records))
            return len(records)

        except Exception as exc:
            log.error(
                "timescale_writer.write_failed",
                table=table, error=str(exc), exc_info=True,
            )
            return -1


class NoOpTimescaleWriter:
    """Implementação nula — para testes ou quando TIMESCALE_URL não está configurado."""

    async def write(self, df: Any, spec: Any) -> int:
        return 0

    async def write_cotacoes(self, df: Any) -> int:
        return 0

    async def write_itens_contabeis(self, df: Any, spec: Any) -> int:
        return 0

    async def write_indicadores(self, df: Any, spec: Any) -> int:
        return 0

    async def close(self) -> None:
        pass
