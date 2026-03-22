"""
TimescaleDB writer v2 — colunas corretas + idempotência via temp table.

Correções em relação ao v1:
  1. Parquets Fintz já vêm em snake_case após _normalize_*
     (data, preco_fechamento, etc.) — sem mapeamento camelCase
  2. Coluna de tempo:
     - cotacoes:       "data"          (date object)
     - itens/indicad.: "data_publicacao" (date object)
  3. Idempotência via COPY → temp table → INSERT ON CONFLICT DO NOTHING
     COPY protocol não aceita ON CONFLICT — workaround padrão TimescaleDB
  4. tipo_periodo em itens_contabeis vem de spec.params["tipoPeriodo"]
     (já injetado pelo _normalize_itens_contabeis do FintzRepo)
"""

from __future__ import annotations

import asyncpg
from datetime import datetime, timezone, date as date_type
from typing import TYPE_CHECKING, Any

from finanalytics_ai.observability.logging import get_logger

if TYPE_CHECKING:
    import pandas as pd
    from finanalytics_ai.domain.fintz.entities import FintzDatasetSpec

log = get_logger(__name__)


def _to_utc_datetime(v: Any) -> datetime | None:
    """Converte date ou datetime para datetime UTC aware."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, date_type):
        return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
    # Fallback: string ISO ou pandas Timestamp
    if isinstance(v, str):
        try:
            from datetime import datetime as _dt
            d = _dt.fromisoformat(v.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    try:
        from pandas import Timestamp
        ts = Timestamp(v)
        if ts is not None and not ts is ts.NaT:
            return ts.to_pydatetime().replace(tzinfo=timezone.utc)
    except Exception:
        pass
    return None


def _nan_to_none(v: Any) -> Any:
    """Converte NaN / pd.NA / pd.NaT para None."""
    try:
        import pandas as pd
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


class PgTimescaleWriter:
    """
    Escreve DataFrames Fintz normalizados nas hypertables do TimescaleDB.

    Estratégia de idempotência:
      1. COPY para tabela temporária (sem índices — rápido)
      2. INSERT INTO hypertable SELECT FROM temp ON CONFLICT DO NOTHING
      Garante que re-sync do mesmo dataset não duplica dados.

    Pool isolado do OLTP (porta 5433, statement_cache_size=0).
    """

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

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def write(self, df: Any, spec: Any) -> int:
        """Despacha para o método correto baseado em spec.dataset_type."""
        dt = getattr(spec, "dataset_type", "")
        if dt == "cotacoes":
            return await self.write_cotacoes(df)
        if dt == "item_contabil":
            return await self.write_itens_contabeis(df, spec)
        if dt == "indicador":
            return await self.write_indicadores(df, spec)
        log.warning("timescale_writer.unknown_type", dataset_type=dt)
        return 0

    # ── Cotações ──────────────────────────────────────────────────────────────

    async def write_cotacoes(self, df: Any) -> int:
        """
        Grava cotações em fintz_cotacoes_ts.

        DataFrame esperado (após FintzRepo._normalize_cotacoes):
          ticker, data (date), preco_abertura, preco_fechamento,
          preco_maximo, preco_medio, preco_minimo, volume_negociado,
          quantidade_negociada, quantidade_negocios, fator_ajuste,
          preco_fechamento_ajustado, fator_ajuste_desdobramentos,
          preco_fechamento_ajustado_desdobramentos
        """
        columns = [
            "time", "ticker",
            "preco_fechamento", "preco_fechamento_ajustado",
            "preco_abertura", "preco_minimo", "preco_maximo",
            "volume_negociado", "fator_ajuste", "preco_medio",
            "quantidade_negociada", "quantidade_negocios",
            "fator_ajuste_desdobramentos",
            "preco_fechamento_ajustado_desdobramentos",
        ]
        # Renomeia: "data" → "time" (sem outras mudanças — já é snake_case)
        return await self._write_normalized(
            df=df,
            table="fintz_cotacoes_ts",
            columns=columns,
            time_col_src="data",      # nome no DataFrame
            conflict_cols="(time, ticker)",
        )

    # ── Itens Contábeis ───────────────────────────────────────────────────────

    async def write_itens_contabeis(self, df: Any, spec: Any) -> int:
        """
        Grava itens contábeis em fintz_itens_contabeis_ts.

        DataFrame esperado (após FintzRepo._normalize_itens_contabeis):
          ticker, item, tipo_periodo, data_publicacao (date), valor
        """
        columns = ["time", "ticker", "item", "tipo_periodo", "valor"]
        return await self._write_normalized(
            df=df,
            table="fintz_itens_contabeis_ts",
            columns=columns,
            time_col_src="data_publicacao",
            conflict_cols="(time, ticker, item, tipo_periodo)",
        )

    # ── Indicadores ───────────────────────────────────────────────────────────

    async def write_indicadores(self, df: Any, spec: Any) -> int:
        """
        Grava indicadores em fintz_indicadores_ts.

        DataFrame esperado (após FintzRepo._normalize_indicadores):
          ticker, indicador, data_publicacao (date), valor
        """
        columns = ["time", "ticker", "indicador", "valor"]
        return await self._write_normalized(
            df=df,
            table="fintz_indicadores_ts",
            columns=columns,
            time_col_src="data_publicacao",
            conflict_cols="(time, ticker, indicador)",
        )

    # ── Core: COPY → temp → INSERT ON CONFLICT DO NOTHING ────────────────────

    async def _write_normalized(
        self,
        df: Any,
        table: str,
        columns: list[str],
        time_col_src: str,      # nome da coluna de tempo no DataFrame
        conflict_cols: str,     # ex: "(time, ticker)" para ON CONFLICT
    ) -> int:
        """
        Fluxo de idempotência:
          1. Cria tabela temporária com mesma estrutura
          2. COPY protocol (rápido, sem índices na temp)
          3. INSERT INTO hypertable SELECT FROM temp ON CONFLICT DO NOTHING
          4. DROP TEMP (automático ao fechar transação)
        """
        if df is None or (hasattr(df, "empty") and df.empty):
            return 0

        try:
            records = self._build_records(df, columns, time_col_src)
            if not records:
                return 0

            pool = await self._get_pool()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # 1. Cria temp table
                    temp = f"_ts_import_{table.replace('.', '_')}"
                    await conn.execute(
                        f"CREATE TEMP TABLE {temp} "
                        f"(LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP"
                    )

                    # 2. COPY para temp
                    await conn.copy_records_to_table(
                        temp, records=records, columns=columns
                    )

                    # 3. INSERT hypertable com idempotência
                    cols_str = ", ".join(columns)
                    result = await conn.execute(
                        f"INSERT INTO {table} ({cols_str}) "
                        f"SELECT {cols_str} FROM {temp} "
                        f"ON CONFLICT {conflict_cols} DO NOTHING"
                    )

                    # Extrai contagem do resultado "INSERT 0 N"
                    inserted = int(result.split()[-1]) if result else len(records)

            log.info(
                "timescale_writer.write_ok",
                table=table,
                records_prepared=len(records),
                rows_inserted=inserted,
            )
            return inserted

        except Exception as exc:
            log.error(
                "timescale_writer.write_failed",
                table=table,
                error=str(exc),
                exc_info=True,
            )
            return -1

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_records(
        self,
        df: Any,
        columns: list[str],
        time_col_src: str,
    ) -> list[tuple]:
        """
        Constrói lista de tuplas para COPY.

        Renomeia time_col_src → "time" e converte date → datetime UTC.
        Converte NaN/NA → None.
        """
        import pandas as pd

        df = df.copy()

        # Renomeia coluna de tempo para "time"
        if time_col_src != "time" and time_col_src in df.columns:
            df = df.rename(columns={time_col_src: "time"})

        # Converte coluna de tempo para datetime UTC aware
        if "time" in df.columns:
            df["time"] = df["time"].apply(_to_utc_datetime)

        # Seleciona apenas colunas disponíveis na ordem correta
        available = [c for c in columns if c in df.columns]
        df = df[available]

        records = []
        for row in df.itertuples(index=False, name=None):
            records.append(tuple(_nan_to_none(v) for v in row))

        return records


class NoOpTimescaleWriter:
    """Implementação nula — testes ou TIMESCALE_URL não configurado."""

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
