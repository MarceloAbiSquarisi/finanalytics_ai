"""
Testes do TimescaleWriter v2.

Cobre:
  - Mapeamento correto de colunas snake_case (não camelCase)
  - Conversão date → datetime UTC aware
  - Conversão NaN/NA → None
  - Fluxo de idempotência (temp table + INSERT ON CONFLICT DO NOTHING)
  - Falha não-fatal (retorna -1, não lança)
  - DataFrame vazio → retorna 0
  - Dispatch por dataset_type
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

# ── Fixtures ──────────────────────────────────────────────────────────────────


def make_spec(dataset_type: str, tipo_periodo: str = "12M") -> MagicMock:
    spec = MagicMock()
    spec.dataset_type = dataset_type
    spec.params = {"tipoPeriodo": tipo_periodo}
    return spec


def make_cotacoes_df() -> pd.DataFrame:
    """DataFrame como FintzRepo._normalize_cotacoes retorna."""
    return pd.DataFrame(
        [
            {
                "ticker": "PETR4",
                "data": date(2025, 1, 2),
                "preco_abertura": 34.0,
                "preco_fechamento": 35.0,
                "preco_maximo": 35.5,
                "preco_medio": 34.8,
                "preco_minimo": 33.5,
                "volume_negociado": 1000000.0,
                "quantidade_negociada": 28571,
                "quantidade_negocios": 5000,
                "fator_ajuste": 1.0,
                "preco_fechamento_ajustado": 35.0,
                "fator_ajuste_desdobramentos": 1.0,
                "preco_fechamento_ajustado_desdobramentos": 35.0,
            },
            {
                "ticker": "PETR4",
                "data": date(2025, 1, 3),
                "preco_abertura": 35.0,
                "preco_fechamento": 36.0,
                "preco_maximo": 36.5,
                "preco_medio": 35.8,
                "preco_minimo": 34.5,
                "volume_negociado": 900000.0,
                "quantidade_negociada": 25000,
                "quantidade_negocios": 4500,
                "fator_ajuste": 1.0,
                "preco_fechamento_ajustado": 36.0,
                "fator_ajuste_desdobramentos": 1.0,
                "preco_fechamento_ajustado_desdobramentos": 36.0,
            },
        ]
    )


def make_itens_df() -> pd.DataFrame:
    """DataFrame como FintzRepo._normalize_itens_contabeis retorna."""
    return pd.DataFrame(
        [
            {
                "ticker": "PETR4",
                "item": "ReceitaLiquida",
                "tipo_periodo": "12M",
                "data_publicacao": date(2025, 3, 31),
                "valor": 500e9,
            },
            {
                "ticker": "PETR4",
                "item": "LucroLiquido",
                "tipo_periodo": "12M",
                "data_publicacao": date(2025, 3, 31),
                "valor": 100e9,
            },
        ]
    )


def make_indicadores_df() -> pd.DataFrame:
    """DataFrame como FintzRepo._normalize_indicadores retorna."""
    return pd.DataFrame(
        [
            {
                "ticker": "PETR4",
                "indicador": "P/L",
                "data_publicacao": date(2025, 1, 2),
                "valor": 8.5,
            },
            {
                "ticker": "PETR4",
                "indicador": "ROE",
                "data_publicacao": date(2025, 1, 2),
                "valor": 0.25,
            },
        ]
    )


def make_mock_conn(insert_result: str = "INSERT 0 2"):
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=insert_result)
    conn.copy_records_to_table = AsyncMock()
    conn.transaction = MagicMock()
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    return conn


def make_mock_pool(conn):
    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


# ── Testes de conversão ────────────────────────────────────────────────────────


class TestHelpers:
    def test_to_utc_datetime_from_date(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            _to_utc_datetime,
        )

        d = date(2025, 1, 15)
        result = _to_utc_datetime(d)
        assert isinstance(result, datetime)
        assert result.tzinfo == UTC
        assert result.year == 2025 and result.month == 1 and result.day == 15

    def test_to_utc_datetime_from_aware_datetime(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            _to_utc_datetime,
        )

        dt = datetime(2025, 1, 15, 10, 30, tzinfo=UTC)
        assert _to_utc_datetime(dt) == dt

    def test_to_utc_datetime_from_naive_datetime(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            _to_utc_datetime,
        )

        dt = datetime(2025, 1, 15, 10, 30)
        result = _to_utc_datetime(dt)
        assert result.tzinfo == UTC

    def test_to_utc_datetime_none(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            _to_utc_datetime,
        )

        assert _to_utc_datetime(None) is None

    def test_nan_to_none_nan(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            _nan_to_none,
        )

        assert _nan_to_none(float("nan")) is None

    def test_nan_to_none_value(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            _nan_to_none,
        )

        assert _nan_to_none(35.5) == 35.5
        assert _nan_to_none("PETR4") == "PETR4"
        assert _nan_to_none(None) is None


# ── Testes do writer ───────────────────────────────────────────────────────────


class TestPgTimescaleWriterCotacoes:
    @pytest.mark.asyncio
    async def test_write_cotacoes_happy_path(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            PgTimescaleWriter,
        )

        conn = make_mock_conn("INSERT 0 2")
        pool = make_mock_pool(conn)
        writer = PgTimescaleWriter("postgresql://localhost/test")
        writer._pool = pool

        result = await writer.write_cotacoes(make_cotacoes_df())

        assert result == 2
        conn.copy_records_to_table.assert_called_once()
        # Verifica que a tabela temporária foi criada
        create_call = conn.execute.call_args_list[0][0][0]
        assert "CREATE TEMP TABLE" in create_call
        assert "fintz_cotacoes_ts" in create_call
        # Verifica INSERT com ON CONFLICT
        insert_call = conn.execute.call_args_list[1][0][0]
        assert "ON CONFLICT" in insert_call
        assert "DO NOTHING" in insert_call

    @pytest.mark.asyncio
    async def test_write_cotacoes_time_column_renamed(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            PgTimescaleWriter,
        )

        conn = make_mock_conn("INSERT 0 2")
        pool = make_mock_pool(conn)
        writer = PgTimescaleWriter("postgresql://localhost/test")
        writer._pool = pool

        await writer.write_cotacoes(make_cotacoes_df())

        # Verifica que records têm datetime UTC (não date)
        copy_call = conn.copy_records_to_table.call_args
        records = copy_call[1]["records"]
        first_time = records[0][0]  # primeira coluna = time
        assert isinstance(first_time, datetime)
        assert first_time.tzinfo == UTC

    @pytest.mark.asyncio
    async def test_write_cotacoes_nan_converted_to_none(self):

        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            PgTimescaleWriter,
        )

        df = make_cotacoes_df()
        df.loc[0, "preco_abertura"] = float("nan")

        conn = make_mock_conn("INSERT 0 2")
        pool = make_mock_pool(conn)
        writer = PgTimescaleWriter("postgresql://localhost/test")
        writer._pool = pool

        await writer.write_cotacoes(df)

        copy_call = conn.copy_records_to_table.call_args
        records = copy_call[1]["records"]
        # preco_abertura é a 3ª coluna (índice 2) após time, ticker
        preco_abertura_idx = [
            "time",
            "ticker",
            "preco_fechamento",
            "preco_fechamento_ajustado",
            "preco_abertura",
        ].index("preco_abertura")
        assert records[0][preco_abertura_idx] is None

    @pytest.mark.asyncio
    async def test_write_cotacoes_empty_df_returns_zero(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            PgTimescaleWriter,
        )

        writer = PgTimescaleWriter("postgresql://localhost/test")
        result = await writer.write_cotacoes(pd.DataFrame())
        assert result == 0

    @pytest.mark.asyncio
    async def test_write_cotacoes_failure_returns_minus_one(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            PgTimescaleWriter,
        )

        writer = PgTimescaleWriter("postgresql://localhost/test")
        writer._get_pool = AsyncMock(side_effect=Exception("connection refused"))
        result = await writer.write_cotacoes(make_cotacoes_df())
        assert result == -1


class TestPgTimescaleWriterItens:
    @pytest.mark.asyncio
    async def test_write_itens_contabeis_happy_path(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            PgTimescaleWriter,
        )

        conn = make_mock_conn("INSERT 0 2")
        pool = make_mock_pool(conn)
        writer = PgTimescaleWriter("postgresql://localhost/test")
        writer._pool = pool

        result = await writer.write_itens_contabeis(make_itens_df(), make_spec("item_contabil"))

        assert result == 2
        insert_call = conn.execute.call_args_list[1][0][0]
        assert "fintz_itens_contabeis_ts" in insert_call
        assert "ON CONFLICT" in insert_call

    @pytest.mark.asyncio
    async def test_write_itens_uses_data_publicacao(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            PgTimescaleWriter,
        )

        conn = make_mock_conn("INSERT 0 2")
        pool = make_mock_pool(conn)
        writer = PgTimescaleWriter("postgresql://localhost/test")
        writer._pool = pool

        await writer.write_itens_contabeis(make_itens_df(), make_spec("item_contabil"))

        copy_call = conn.copy_records_to_table.call_args
        records = copy_call[1]["records"]
        # primeira coluna = time (convertida de data_publicacao)
        assert isinstance(records[0][0], datetime)
        assert records[0][0].tzinfo == UTC


class TestPgTimescaleWriterIndicadores:
    @pytest.mark.asyncio
    async def test_write_indicadores_happy_path(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            PgTimescaleWriter,
        )

        conn = make_mock_conn("INSERT 0 2")
        pool = make_mock_pool(conn)
        writer = PgTimescaleWriter("postgresql://localhost/test")
        writer._pool = pool

        result = await writer.write_indicadores(make_indicadores_df(), make_spec("indicador"))

        assert result == 2
        insert_call = conn.execute.call_args_list[1][0][0]
        assert "fintz_indicadores_ts" in insert_call

    @pytest.mark.asyncio
    async def test_write_indicadores_uses_data_publicacao(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            PgTimescaleWriter,
        )

        conn = make_mock_conn("INSERT 0 2")
        pool = make_mock_pool(conn)
        writer = PgTimescaleWriter("postgresql://localhost/test")
        writer._pool = pool

        await writer.write_indicadores(make_indicadores_df(), make_spec("indicador"))

        copy_call = conn.copy_records_to_table.call_args
        records = copy_call[1]["records"]
        assert isinstance(records[0][0], datetime)
        assert records[0][0].tzinfo == UTC


class TestPgTimescaleWriterDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_cotacoes(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            PgTimescaleWriter,
        )

        writer = PgTimescaleWriter("postgresql://localhost/test")
        writer.write_cotacoes = AsyncMock(return_value=100)
        result = await writer.write(make_cotacoes_df(), make_spec("cotacoes"))
        writer.write_cotacoes.assert_called_once()
        assert result == 100

    @pytest.mark.asyncio
    async def test_dispatch_itens_contabeis(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            PgTimescaleWriter,
        )

        writer = PgTimescaleWriter("postgresql://localhost/test")
        writer.write_itens_contabeis = AsyncMock(return_value=50)
        spec = make_spec("item_contabil")
        result = await writer.write(make_itens_df(), spec)
        writer.write_itens_contabeis.assert_called_once()
        assert result == 50

    @pytest.mark.asyncio
    async def test_dispatch_indicadores(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            PgTimescaleWriter,
        )

        writer = PgTimescaleWriter("postgresql://localhost/test")
        writer.write_indicadores = AsyncMock(return_value=30)
        spec = make_spec("indicador")
        result = await writer.write(make_indicadores_df(), spec)
        writer.write_indicadores.assert_called_once()
        assert result == 30

    @pytest.mark.asyncio
    async def test_dispatch_unknown_returns_zero(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            PgTimescaleWriter,
        )

        writer = PgTimescaleWriter("postgresql://localhost/test")
        result = await writer.write(pd.DataFrame(), make_spec("desconhecido"))
        assert result == 0


class TestNoOpTimescaleWriter:
    @pytest.mark.asyncio
    async def test_all_return_zero(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            NoOpTimescaleWriter,
        )

        w = NoOpTimescaleWriter()
        assert await w.write(pd.DataFrame(), make_spec("cotacoes")) == 0
        assert await w.write_cotacoes(pd.DataFrame()) == 0
        assert await w.write_itens_contabeis(pd.DataFrame(), None) == 0
        assert await w.write_indicadores(pd.DataFrame(), None) == 0

    @pytest.mark.asyncio
    async def test_close_no_error(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            NoOpTimescaleWriter,
        )

        await NoOpTimescaleWriter().close()
