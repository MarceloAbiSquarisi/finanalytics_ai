"""
finanalytics_ai.infrastructure.database.repositories.fintz_repo
────────────────────────────────────────────────────────────────
Repositório PostgreSQL para dados Fintz.

Schema real dos parquets (verificado em 2026-03-20):

  cotacoes:
    data, preco_abertura, preco_fechamento, preco_maximo, preco_medio,
    preco_minimo, quantidade_negociada, quantidade_negocios, ticker,
    volume_negociado (float64), fator_ajuste, preco_fechamento_ajustado,
    fator_ajuste_desdobramentos, preco_fechamento_ajustado_desdobramentos

  itens_contabeis (PIT):
    ticker, item, data, valor
    (tipo_periodo derivado do endpoint; sem ano/trimestre/tipoDemonstracao)

  indicadores (PIT):
    ticker, indicador, data, valor
"""

from __future__ import annotations

from datetime import UTC, datetime
import textwrap
from typing import TYPE_CHECKING, Any

import pandas as pd
from sqlalchemy import text
import structlog

from finanalytics_ai.infrastructure.database.connection import get_session

if TYPE_CHECKING:
    from finanalytics_ai.domain.fintz.entities import FintzDatasetSpec

logger = structlog.get_logger(__name__)

CHUNK_SIZE = 5_000


class FintzRepo:
    """
    Repositório PostgreSQL para dados Fintz.
    Implementa FintzRepository protocol via duck typing.
    """

    # ── Sync log ──────────────────────────────────────────────────────────────

    async def get_last_hash(self, dataset_key: str) -> str | None:
        sql = text(
            "SELECT file_hash FROM fintz_sync_log WHERE dataset_key = :key AND status = 'ok'"
        )
        async with get_session() as session:
            result = await session.execute(sql, {"key": dataset_key})
            row = result.fetchone()
            return str(row[0]) if row else None

    async def record_sync(
        self,
        dataset_key: str,
        file_hash: str,
        rows_upserted: int,
        status: str,
        error_message: str | None = None,
    ) -> None:
        sql = text(
            textwrap.dedent("""
            INSERT INTO fintz_sync_log
                (dataset_key, file_hash, rows_upserted, status, error_message, synced_at)
            VALUES
                (:key, :hash, :rows, :status, :error, :now)
            ON CONFLICT (dataset_key) DO UPDATE SET
                file_hash     = EXCLUDED.file_hash,
                rows_upserted = EXCLUDED.rows_upserted,
                status        = EXCLUDED.status,
                error_message = EXCLUDED.error_message,
                synced_at     = EXCLUDED.synced_at
        """)
        )
        async with get_session() as session:
            await session.execute(
                sql,
                {
                    "key": dataset_key,
                    "hash": file_hash,
                    "rows": rows_upserted,
                    "status": status,
                    "error": error_message,
                    "now": datetime.now(UTC),
                },
            )

    # ── Cotações OHLC ─────────────────────────────────────────────────────────

    async def upsert_cotacoes(self, df: pd.DataFrame) -> int:
        df = self._normalize_cotacoes(df)
        total = 0
        for chunk in self._chunks(df):
            rows = chunk.to_dict(orient="records")
            sql = text(
                textwrap.dedent("""
                INSERT INTO fintz_cotacoes (
                    ticker, data,
                    preco_abertura, preco_fechamento, preco_maximo, preco_medio, preco_minimo,
                    volume_negociado, quantidade_negociada, quantidade_negocios,
                    fator_ajuste, preco_fechamento_ajustado,
                    fator_ajuste_desdobramentos, preco_fechamento_ajustado_desdobramentos
                ) VALUES (
                    :ticker, :data,
                    :preco_abertura, :preco_fechamento, :preco_maximo, :preco_medio, :preco_minimo,
                    :volume_negociado, :quantidade_negociada, :quantidade_negocios,
                    :fator_ajuste, :preco_fechamento_ajustado,
                    :fator_ajuste_desdobramentos, :preco_fechamento_ajustado_desdobramentos
                )
                ON CONFLICT (ticker, data) DO UPDATE SET
                    preco_abertura                           = EXCLUDED.preco_abertura,
                    preco_fechamento                         = EXCLUDED.preco_fechamento,
                    preco_maximo                             = EXCLUDED.preco_maximo,
                    preco_medio                              = EXCLUDED.preco_medio,
                    preco_minimo                             = EXCLUDED.preco_minimo,
                    volume_negociado                         = EXCLUDED.volume_negociado,
                    quantidade_negociada                     = EXCLUDED.quantidade_negociada,
                    quantidade_negocios                      = EXCLUDED.quantidade_negocios,
                    fator_ajuste                             = EXCLUDED.fator_ajuste,
                    preco_fechamento_ajustado                = EXCLUDED.preco_fechamento_ajustado,
                    fator_ajuste_desdobramentos              = EXCLUDED.fator_ajuste_desdobramentos,
                    preco_fechamento_ajustado_desdobramentos = EXCLUDED.preco_fechamento_ajustado_desdobramentos
            """)
            )
            async with get_session() as session:
                await session.execute(sql, rows)
            total += len(rows)
        return total

    # ── Itens contábeis PIT ───────────────────────────────────────────────────

    async def upsert_itens_contabeis(self, df: pd.DataFrame, spec: FintzDatasetSpec) -> int:
        df = self._normalize_itens_contabeis(df, spec)
        total = 0
        for chunk in self._chunks(df):
            rows = chunk.to_dict(orient="records")
            sql = text(
                textwrap.dedent("""
                INSERT INTO fintz_itens_contabeis
                    (ticker, item, tipo_periodo, data_publicacao, valor)
                VALUES
                    (:ticker, :item, :tipo_periodo, :data_publicacao, :valor)
                ON CONFLICT (ticker, item, tipo_periodo, data_publicacao) DO UPDATE SET
                    valor = EXCLUDED.valor
            """)
            )
            async with get_session() as session:
                await session.execute(sql, rows)
            total += len(rows)
        return total

    # ── Indicadores PIT ───────────────────────────────────────────────────────

    # ── Metodos de leitura para FundamentalAnalysisService ───────────────────

    async def get_indicadores_latest(
        self,
        ticker: str,
        indicadores: list[str] | None = None,
    ) -> dict[str, Any]:
        """Snapshot mais recente de todos os indicadores de um ticker."""
        where = "WHERE ticker = :ticker"
        params: dict = {"ticker": ticker.upper()}
        if indicadores:
            where += " AND indicador = ANY(:inds)"
            params["inds"] = indicadores
        sql = text(f"""
            SELECT DISTINCT ON (indicador)
                indicador,
                valor,
                data_publicacao as data_ref
            FROM fintz_indicadores
            {where}
            ORDER BY indicador, data_publicacao DESC
        """)
        async with get_session() as session:
            result = await session.execute(sql, params)
            rows = result.fetchall()
        return {
            r.indicador: {
                "valor": float(r.valor) if r.valor is not None else None,
                "data_ref": str(r.data_ref),
            }
            for r in rows
        }

    async def get_indicadores(
        self,
        ticker: str,
        indicadores: list[str] | None = None,
        start: Any = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Serie historica de indicadores."""
        where = "WHERE ticker = :ticker"
        params: dict = {"ticker": ticker.upper()}
        if indicadores:
            where += " AND indicador = ANY(:inds)"
            params["inds"] = indicadores
        if start:
            where += " AND data_publicacao >= :start"
            params["start"] = start
        params["limit"] = limit
        sql = text(f"""
            SELECT indicador, data_publicacao as data, valor
            FROM fintz_indicadores
            {where}
            ORDER BY data_publicacao DESC, indicador
            LIMIT :limit
        """)
        async with get_session() as session:
            result = await session.execute(sql, params)
            rows = result.fetchall()
        return [
            {
                "indicador": r.indicador,
                "data": str(r.data),
                "valor": float(r.valor) if r.valor is not None else None,
            }
            for r in rows
        ]

    async def get_indicador_serie(
        self,
        ticker: str,
        indicador: str,
        start=None,
        limit: int = 300,
    ):
        where = "WHERE ticker = :ticker AND indicador = :indicador"
        params = {"ticker": ticker.upper(), "indicador": indicador}
        if start:
            where += " AND data_publicacao >= :start"
            params["start"] = start
        params["limit"] = limit
        sql = text(f"""
            SELECT data_publicacao as data, valor
            FROM fintz_indicadores
            {where}
            ORDER BY data_publicacao ASC
            LIMIT :limit
        """)
        async with get_session() as session:
            result = await session.execute(sql, params)
            rows = result.fetchall()
        return [
            {"data": str(r.data), "valor": float(r.valor) if r.valor is not None else None}
            for r in rows
        ]

    async def get_itens_contabeis(
        self,
        ticker: str,
        itens: list[str] | None = None,
        tipo_periodo: str | None = None,
        start: Any = None,
        limit: int = 80,
    ) -> list[dict[str, Any]]:
        """Serie historica de itens contabeis."""
        where = "WHERE ticker = :ticker"
        params: dict = {"ticker": ticker.upper()}
        if itens:
            where += " AND item = ANY(:itens)"
            params["itens"] = itens
        if tipo_periodo:
            where += " AND tipo_periodo = :tipo_periodo"
            params["tipo_periodo"] = tipo_periodo
        if start:
            where += " AND data_publicacao >= :start"
            params["start"] = start
        params["limit"] = limit
        sql = text(f"""
            SELECT item, tipo_periodo, data_publicacao, valor
            FROM fintz_itens_contabeis
            {where}
            ORDER BY data_publicacao DESC, item
            LIMIT :limit
        """)
        async with get_session() as session:
            result = await session.execute(sql, params)
            rows = result.fetchall()
        return [
            {
                "item": r.item,
                "tipo_periodo": r.tipo_periodo,
                "data_publicacao": str(r.data_publicacao),
                "valor": float(r.valor) if r.valor is not None else None,
            }
            for r in rows
        ]

    async def list_tickers(self, dataset: str = "cotacoes") -> list[str]:
        """Lista tickers únicos disponíveis num dataset Fintz (BUG19 fix 26/abr).

        dataset:
          - 'cotacoes' → fintz_cotacoes
          - 'indicadores' → fintz_indicadores
          - 'itens' → fintz_itens_contabeis
        """
        table_map = {
            "cotacoes": "fintz_cotacoes",
            "indicadores": "fintz_indicadores",
            "itens": "fintz_itens_contabeis",
        }
        table = table_map.get(dataset)
        if not table:
            raise ValueError(f"dataset desconhecido: {dataset}")
        sql = text(f"SELECT DISTINCT ticker FROM {table} ORDER BY ticker")
        async with get_session() as session:
            result = await session.execute(sql)
            rows = result.fetchall()
        return [r.ticker for r in rows if r.ticker]

    async def get_cotacoes(
        self,
        ticker: str,
        start: Any = None,
        end: Any = None,
        limit: int = 756,
    ) -> list[dict[str, Any]]:
        """Serie historica de cotacoes."""
        where = "WHERE ticker = :ticker"
        params: dict = {"ticker": ticker.upper()}
        if start:
            where += " AND data >= :start"
            params["start"] = start
        if end:
            where += " AND data <= :end"
            params["end"] = end
        params["limit"] = limit
        sql = text(f"""
            SELECT data, ticker,
                   preco_fechamento as fechamento,
                   preco_fechamento_ajustado as fechamento_ajustado,
                   preco_abertura as abertura,
                   preco_minimo as minimo,
                   preco_maximo as maximo,
                   volume_negociado as volume
            FROM fintz_cotacoes
            {where}
            ORDER BY data DESC
            LIMIT :limit
        """)
        async with get_session() as session:
            result = await session.execute(sql, params)
            rows = result.fetchall()
        return [
            {
                "data": str(r.data),
                "ticker": r.ticker,
                "fechamento": float(r.fechamento) if r.fechamento else None,
                "fechamento_ajustado": float(r.fechamento_ajustado)
                if r.fechamento_ajustado
                else None,
                "abertura": float(r.abertura) if r.abertura else None,
                "volume": float(r.volume) if r.volume else None,
            }
            for r in rows
        ]

    async def upsert_indicadores(self, df: pd.DataFrame, spec: FintzDatasetSpec) -> int:
        df = self._normalize_indicadores(df, spec)
        total = 0
        for chunk in self._chunks(df):
            rows = chunk.to_dict(orient="records")
            sql = text(
                textwrap.dedent("""
                INSERT INTO fintz_indicadores
                    (ticker, indicador, data_publicacao, valor)
                VALUES
                    (:ticker, :indicador, :data_publicacao, :valor)
                ON CONFLICT (ticker, indicador, data_publicacao) DO UPDATE SET
                    valor = EXCLUDED.valor
            """)
            )
            async with get_session() as session:
                await session.execute(sql, rows)
            total += len(rows)
        return total

    # ── Normalização ──────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_cotacoes(df: pd.DataFrame) -> pd.DataFrame:
        """
        Parquet já vem em snake_case — só precisa garantir tipos corretos.
        volume_negociado é float64 no parquet (pode ter NaN implícito).
        """
        # Garante que data é date (não datetime)
        df["data"] = pd.to_datetime(df["data"]).dt.date

        # Colunas numéricas — converte para float (aceita NaN)
        float_cols = [
            "preco_abertura",
            "preco_fechamento",
            "preco_maximo",
            "preco_medio",
            "preco_minimo",
            "volume_negociado",
            "fator_ajuste",
            "preco_fechamento_ajustado",
            "fator_ajuste_desdobramentos",
            "preco_fechamento_ajustado_desdobramentos",
        ]
        for col in float_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        # Arredonda volume para 2 casas (evita overflow NUMERIC(24,2))
        if "volume_negociado" in df.columns:
            df["volume_negociado"] = df["volume_negociado"].round(2)

        # Inteiros — NaN vira None (Int64 nullable)
        for col in ("quantidade_negociada", "quantidade_negocios"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

        # Garante colunas ausentes com None
        todas = [
            "ticker",
            "data",
            "preco_abertura",
            "preco_fechamento",
            "preco_maximo",
            "preco_medio",
            "preco_minimo",
            "volume_negociado",
            "quantidade_negociada",
            "quantidade_negocios",
            "fator_ajuste",
            "preco_fechamento_ajustado",
            "fator_ajuste_desdobramentos",
            "preco_fechamento_ajustado_desdobramentos",
        ]
        for col in todas:
            if col not in df.columns:
                df[col] = None

        # Converte Int64 nullable para objeto (psycopg aceita None, não pd.NA)
        for col in ("quantidade_negociada", "quantidade_negocios"):
            df[col] = df[col].where(df[col].notna(), other=None)

        return df[todas]

    @staticmethod
    def _normalize_itens_contabeis(
        df: pd.DataFrame,
        spec: FintzDatasetSpec,
    ) -> pd.DataFrame:
        """
        Parquet tem: ticker, item, data, valor
        tipo_periodo é derivado do endpoint (spec.params["tipoPeriodo"])
        """
        df = df.rename(columns={"data": "data_publicacao"})
        df["data_publicacao"] = pd.to_datetime(df["data_publicacao"]).dt.date
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
        df["valor"] = df["valor"].replace([float("inf"), float("-inf")], None)
        df["tipo_periodo"] = spec.params.get("tipoPeriodo", "")

        return df[["ticker", "item", "tipo_periodo", "data_publicacao", "valor"]]

    @staticmethod
    def _normalize_indicadores(
        df: pd.DataFrame,
        spec: FintzDatasetSpec,
    ) -> pd.DataFrame:
        """
        Parquet tem: ticker, indicador, data, valor
        """
        df = df.rename(columns={"data": "data_publicacao"})
        df["data_publicacao"] = pd.to_datetime(df["data_publicacao"]).dt.date
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
        df["valor"] = df["valor"].replace([float("inf"), float("-inf")], None)

        return df[["ticker", "indicador", "data_publicacao", "valor"]]

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _chunks(df: pd.DataFrame) -> Any:
        for start in range(0, len(df), CHUNK_SIZE):
            yield df.iloc[start : start + CHUNK_SIZE]
