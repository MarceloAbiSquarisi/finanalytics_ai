"""
Testes do TimescaleFintzRepository.
Usa mock do asyncpg.Pool — sem banco real.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest


def make_pool(fetch_return=None, fetchrow_return=None):
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=fetch_return or [])
    pool.fetchrow = AsyncMock(return_value=fetchrow_return)
    return pool


def make_row(**kwargs):
    row = MagicMock()
    row.__getitem__ = lambda self, k: kwargs[k]
    row.keys = lambda: kwargs.keys()
    # Make dict(row) work
    row.__iter__ = lambda self: iter(kwargs.items())
    return kwargs  # return dict directly since we mock dict(r)


class TestTimescaleFintzRepository:
    @pytest.mark.asyncio
    async def test_get_cotacoes_sem_filtros(self):
        from finanalytics_ai.infrastructure.timescale.fintz_repo import TimescaleFintzRepository

        rows = [
            {
                "data": date(2025, 1, 2),
                "ticker": "PETR4",
                "fechamento": 35.0,
                "fechamento_ajustado": 35.0,
                "abertura": 34.0,
                "minimo": 33.5,
                "maximo": 35.5,
                "volume": 1000000,
                "medio": 34.8,
                "fator_ajuste": 1.0,
                "negocios": 5000,
            }
        ]
        pool = make_pool(fetch_return=rows)
        repo = TimescaleFintzRepository(pool)

        result = await repo.get_cotacoes("petr4")

        pool.fetch.assert_called_once()
        call_args = pool.fetch.call_args
        query = call_args[0][0]
        assert "fintz_cotacoes_ts" in query
        assert "PETR4" in call_args[0][1:]

    @pytest.mark.asyncio
    async def test_get_cotacoes_com_datas(self):
        from finanalytics_ai.infrastructure.timescale.fintz_repo import TimescaleFintzRepository

        pool = make_pool(fetch_return=[])
        repo = TimescaleFintzRepository(pool)

        await repo.get_cotacoes(
            "VALE3",
            start=date(2024, 1, 1),
            end=date(2024, 12, 31),
            limit=100,
        )

        query = pool.fetch.call_args[0][0]
        assert ">=" in query
        assert "<=" in query

    @pytest.mark.asyncio
    async def test_get_indicadores_latest(self):
        from finanalytics_ai.infrastructure.timescale.fintz_repo import TimescaleFintzRepository

        rows = [
            {"indicador": "P/L", "valor": 8.5, "data_ref": date(2025, 1, 2)},
            {"indicador": "ROE", "valor": 0.25, "data_ref": date(2025, 1, 2)},
        ]
        pool = make_pool(fetch_return=rows)
        repo = TimescaleFintzRepository(pool)

        result = await repo.get_indicadores_latest("PETR4")

        assert "P/L" in result
        assert "ROE" in result

    @pytest.mark.asyncio
    async def test_get_indicadores_latest_com_filtro(self):
        from finanalytics_ai.infrastructure.timescale.fintz_repo import TimescaleFintzRepository

        pool = make_pool(fetch_return=[])
        repo = TimescaleFintzRepository(pool)

        await repo.get_indicadores_latest("PETR4", indicadores=["P/L", "ROE"])

        query = pool.fetch.call_args[0][0]
        assert "ANY" in query

    @pytest.mark.asyncio
    async def test_get_indicadores_serie(self):
        from finanalytics_ai.infrastructure.timescale.fintz_repo import TimescaleFintzRepository

        rows = [
            {"data": date(2025, 1, 2), "valor": 8.5},
            {"data": date(2024, 12, 30), "valor": 7.9},
        ]
        pool = make_pool(fetch_return=rows)
        repo = TimescaleFintzRepository(pool)

        result = await repo.get_indicadores_serie("PETR4", "P/L")

        assert len(result) == 2
        assert result[0]["valor"] == 8.5

    @pytest.mark.asyncio
    async def test_get_itens_latest(self):
        from finanalytics_ai.infrastructure.timescale.fintz_repo import TimescaleFintzRepository

        rows = [
            {"item": "Receita Líquida", "valor": 50000000000, "data_ref": date(2025, 1, 2)},
        ]
        pool = make_pool(fetch_return=rows)
        repo = TimescaleFintzRepository(pool)

        result = await repo.get_itens_latest("PETR4", tipo_periodo="12M")

        assert "Receita Líquida" in result
        assert result["Receita Líquida"]["valor"] == 50000000000.0

    @pytest.mark.asyncio
    async def test_get_itens_contabeis_tipo_periodo(self):
        from finanalytics_ai.infrastructure.timescale.fintz_repo import TimescaleFintzRepository

        pool = make_pool(fetch_return=[])
        repo = TimescaleFintzRepository(pool)

        await repo.get_itens_contabeis("VALE3", tipo_periodo="TRIMESTRAL")

        query = pool.fetch.call_args[0][0]
        assert "tipo_periodo" in query

    @pytest.mark.asyncio
    async def test_list_tickers(self):
        from finanalytics_ai.infrastructure.timescale.fintz_repo import TimescaleFintzRepository

        rows = [{"ticker": "PETR4"}, {"ticker": "VALE3"}, {"ticker": "ITUB4"}]
        pool = make_pool(fetch_return=rows)
        repo = TimescaleFintzRepository(pool)

        result = await repo.list_tickers("cotacoes")

        assert "PETR4" in result
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_get_coverage(self):
        from finanalytics_ai.infrastructure.timescale.fintz_repo import TimescaleFintzRepository

        mock_row = {"inicio": date(2010, 1, 4), "fim": date(2025, 11, 4), "registros": 5000}
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=mock_row)
        repo = TimescaleFintzRepository(pool)

        result = await repo.get_coverage("PETR4")

        assert result["ticker"] == "PETR4"
        assert "cotacoes" in result
        assert "indicadores" in result
        assert "itens_contabeis" in result

    @pytest.mark.asyncio
    async def test_get_cotacoes_agregadas(self):
        from finanalytics_ai.infrastructure.timescale.fintz_repo import TimescaleFintzRepository

        pool = make_pool(fetch_return=[])
        repo = TimescaleFintzRepository(pool)

        await repo.get_cotacoes_agregadas("PETR4", bucket="1 month")

        query = pool.fetch.call_args[0][0]
        assert "time_bucket" in query
        assert "FIRST" in query
        assert "LAST" in query
