"""
Testes do PgTimescaleWriter e NoOpTimescaleWriter.

Usa mock do asyncpg.Pool para não depender de banco real.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest


def make_spec(dataset_type: str = "cotacoes") -> MagicMock:
    spec = MagicMock()
    spec.dataset_type = dataset_type
    spec.key = dataset_type
    return spec


def make_df(empty: bool = False) -> MagicMock:
    import pandas as pd
    if empty:
        return pd.DataFrame()
    df = MagicMock()
    df.empty = False
    df.rename = MagicMock(return_value=df)
    df.__getitem__ = MagicMock(return_value=df)
    df.itertuples = MagicMock(return_value=iter([(1.0, "PETR4", 30.0)]))
    return df


class TestNoOpTimescaleWriter:
    @pytest.mark.asyncio
    async def test_write_retorna_zero(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            NoOpTimescaleWriter,
        )
        writer = NoOpTimescaleWriter()
        result = await writer.write(make_df(), make_spec())
        assert result == 0

    @pytest.mark.asyncio
    async def test_write_cotacoes_retorna_zero(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            NoOpTimescaleWriter,
        )
        writer = NoOpTimescaleWriter()
        assert await writer.write_cotacoes(make_df()) == 0

    @pytest.mark.asyncio
    async def test_close_sem_erro(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            NoOpTimescaleWriter,
        )
        writer = NoOpTimescaleWriter()
        await writer.close()  # não deve lançar


class TestPgTimescaleWriter:
    @pytest.mark.asyncio
    async def test_write_despacha_cotacoes(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            PgTimescaleWriter,
        )
        writer = PgTimescaleWriter("postgresql://localhost/test")
        writer.write_cotacoes = AsyncMock(return_value=50)
        result = await writer.write(make_df(), make_spec("cotacoes"))
        writer.write_cotacoes.assert_called_once()
        assert result == 50

    @pytest.mark.asyncio
    async def test_write_despacha_itens_contabeis(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            PgTimescaleWriter,
        )
        writer = PgTimescaleWriter("postgresql://localhost/test")
        writer.write_itens_contabeis = AsyncMock(return_value=200)
        spec = make_spec("item_contabil")
        result = await writer.write(make_df(), spec)
        writer.write_itens_contabeis.assert_called_once()
        assert result == 200

    @pytest.mark.asyncio
    async def test_write_despacha_indicadores(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            PgTimescaleWriter,
        )
        writer = PgTimescaleWriter("postgresql://localhost/test")
        writer.write_indicadores = AsyncMock(return_value=150)
        spec = make_spec("indicador")
        result = await writer.write(make_df(), spec)
        writer.write_indicadores.assert_called_once()
        assert result == 150

    @pytest.mark.asyncio
    async def test_write_tipo_desconhecido_retorna_zero(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            PgTimescaleWriter,
        )
        writer = PgTimescaleWriter("postgresql://localhost/test")
        result = await writer.write(make_df(), make_spec("desconhecido"))
        assert result == 0

    @pytest.mark.asyncio
    async def test_write_df_falha_retorna_menos_um(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            PgTimescaleWriter,
        )
        writer = PgTimescaleWriter("postgresql://localhost/test")
        # Simula falha no pool
        writer._get_pool = AsyncMock(side_effect=Exception("connection refused"))
        result = await writer._write_df(
            make_df(), "fintz_cotacoes_ts", ["time", "ticker"], {"data": "time"}
        )
        assert result == -1

    @pytest.mark.asyncio
    async def test_write_df_vazio_retorna_zero(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            PgTimescaleWriter,
        )
        import pandas as pd
        writer = PgTimescaleWriter("postgresql://localhost/test")
        result = await writer._write_df(
            pd.DataFrame(), "fintz_cotacoes_ts", ["time"], {}
        )
        assert result == 0

    @pytest.mark.asyncio
    async def test_dsn_normaliza_asyncpg(self):
        from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
            PgTimescaleWriter,
        )
        writer = PgTimescaleWriter("postgresql+asyncpg://user:pw@localhost/db")
        assert "postgresql+asyncpg" not in writer._dsn
        assert writer._dsn.startswith("postgresql://")
