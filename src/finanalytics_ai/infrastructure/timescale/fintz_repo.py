"""
infrastructure/timescale/fintz_repo.py

Queries otimizadas nas hypertables Fintz do TimescaleDB.

Usa asyncpg direto (não SQLAlchemy) — hypertables têm planos de query
dinâmicos que se beneficiam de statement_cache_size=0 e queries parametrizadas
simples sem ORM overhead.

Design:
  - Recebe asyncpg.Pool injetado (compartilha o pool do timescale existente)
  - Retorna dicts simples — sem dataclasses de domínio para não acoplar
    o domínio à infra TimescaleDB
  - Todas as queries usam time bucketing nativo do TimescaleDB
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import asyncpg

from finanalytics_ai.observability.logging import get_logger

log = get_logger(__name__)


class TimescaleFintzRepository:
    """Queries nas hypertables fintz_* do TimescaleDB (market_data DB)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # ── Cotações ──────────────────────────────────────────────────────────────

    async def get_cotacoes(
        self,
        ticker: str,
        start: date | None = None,
        end: date | None = None,
        limit: int = 252,
    ) -> list[dict[str, Any]]:
        """
        Retorna série histórica de cotações para um ticker.

        Default: últimos 252 pregões (~1 ano).
        """
        where = "WHERE ticker = $1"
        params: list[Any] = [ticker.upper()]

        if start:
            params.append(datetime(start.year, start.month, start.day, tzinfo=UTC))
            where += f" AND time >= ${len(params)}"
        if end:
            params.append(datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=UTC))
            where += f" AND time <= ${len(params)}"

        params.append(limit)
        query = f"""
            SELECT
                time::date                          AS data,
                ticker,
                preco_fechamento                    AS fechamento,
                preco_fechamento_ajustado           AS fechamento_ajustado,
                preco_abertura                      AS abertura,
                preco_minimo                        AS minimo,
                preco_maximo                        AS maximo,
                volume_negociado                    AS volume,
                preco_medio                         AS medio,
                fator_ajuste,
                quantidade_negocios                 AS negocios
            FROM fintz_cotacoes_ts
            {where}
            ORDER BY time DESC
            LIMIT ${len(params)}
        """
        rows = await self._pool.fetch(query, *params)
        return [dict(r) for r in rows]

    async def get_cotacoes_agregadas(
        self,
        ticker: str,
        bucket: str = "1 month",
        start: date | None = None,
        limit: int = 60,
    ) -> list[dict[str, Any]]:
        """
        Cotações agregadas por bucket temporal (time_bucket nativo TimescaleDB).

        bucket: '1 week' | '1 month' | '3 months' | '1 year'
        """
        start_ts = datetime(start.year, start.month, start.day, tzinfo=UTC) if start else None
        where = "WHERE ticker = $2"
        params: list[Any] = [bucket, ticker.upper()]

        if start_ts:
            params.append(start_ts)
            where += f" AND time >= ${len(params)}"

        params.append(limit)
        query = f"""
            SELECT
                time_bucket($1::interval, time)     AS periodo,
                ticker,
                FIRST(preco_abertura, time)         AS abertura,
                MAX(preco_maximo)                   AS maximo,
                MIN(preco_minimo)                   AS minimo,
                LAST(preco_fechamento_ajustado, time) AS fechamento_ajustado,
                SUM(volume_negociado)               AS volume_total,
                COUNT(*)                            AS pregoes
            FROM fintz_cotacoes_ts
            {where}
            GROUP BY periodo, ticker
            ORDER BY periodo DESC
            LIMIT ${len(params)}
        """
        rows = await self._pool.fetch(query, *params)
        return [dict(r) for r in rows]

    # ── Indicadores ───────────────────────────────────────────────────────────

    async def get_indicadores(
        self,
        ticker: str,
        indicadores: list[str] | None = None,
        start: date | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Série histórica de indicadores fundamentalistas para um ticker.

        indicadores: filtro opcional, ex: ['P/L', 'P/VP', 'ROE', 'DY']
        """
        where = "WHERE ticker = $1"
        params: list[Any] = [ticker.upper()]

        if indicadores:
            params.append(indicadores)
            where += f" AND indicador = ANY(${len(params)})"
        if start:
            params.append(datetime(start.year, start.month, start.day, tzinfo=UTC))
            where += f" AND time >= ${len(params)}"

        params.append(limit)
        query = f"""
            SELECT
                time::date  AS data,
                ticker,
                indicador,
                valor
            FROM fintz_indicadores_ts
            {where}
            ORDER BY time DESC, indicador
            LIMIT ${len(params)}
        """
        rows = await self._pool.fetch(query, *params)
        return [dict(r) for r in rows]

    async def get_indicadores_latest(
        self,
        ticker: str,
        indicadores: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Snapshot mais recente de todos os indicadores de um ticker.
        Retorna dict {indicador: valor}.
        """
        where = "WHERE ticker = $1"
        params: list[Any] = [ticker.upper()]

        if indicadores:
            params.append(indicadores)
            where += f" AND indicador = ANY(${len(params)})"

        query = f"""
            SELECT DISTINCT ON (indicador)
                indicador,
                valor,
                time::date AS data_ref
            FROM fintz_indicadores_ts
            {where}
            ORDER BY indicador, time DESC
        """
        rows = await self._pool.fetch(query, *params)
        return {
            r["indicador"]: {
                "valor": float(r["valor"]) if r["valor"] is not None else None,
                "data_ref": str(r["data_ref"]),
            }
            for r in rows
        }

    async def get_indicadores_serie(
        self,
        ticker: str,
        indicador: str,
        start: date | None = None,
        limit: int = 252,
    ) -> list[dict[str, Any]]:
        """Série temporal de um único indicador para análise de tendência."""
        where = "WHERE ticker = $1 AND indicador = $2"
        params: list[Any] = [ticker.upper(), indicador]

        if start:
            params.append(datetime(start.year, start.month, start.day, tzinfo=UTC))
            where += f" AND time >= ${len(params)}"

        params.append(limit)
        query = f"""
            SELECT time::date AS data, valor
            FROM fintz_indicadores_ts
            {where}
            ORDER BY time DESC
            LIMIT ${len(params)}
        """
        rows = await self._pool.fetch(query, *params)
        return [
            {"data": str(r["data"]), "valor": float(r["valor"]) if r["valor"] else None}
            for r in rows
        ]

    # ── Itens Contábeis ───────────────────────────────────────────────────────

    async def get_itens_contabeis(
        self,
        ticker: str,
        itens: list[str] | None = None,
        tipo_periodo: str | None = None,
        start: date | None = None,
        limit: int = 80,
    ) -> list[dict[str, Any]]:
        """
        Série histórica de itens contábeis.

        tipo_periodo: '12M' | 'TRIMESTRAL'
        itens: ex: ['Receita Líquida', 'Lucro Líquido', 'EBITDA']
        """
        where = "WHERE ticker = $1"
        params: list[Any] = [ticker.upper()]

        if itens:
            params.append(itens)
            where += f" AND item = ANY(${len(params)})"
        if tipo_periodo:
            params.append(tipo_periodo)
            where += f" AND tipo_periodo = ${len(params)}"
        if start:
            params.append(datetime(start.year, start.month, start.day, tzinfo=UTC))
            where += f" AND time >= ${len(params)}"

        params.append(limit)
        query = f"""
            SELECT
                time::date  AS data_publicacao,
                ticker,
                item,
                tipo_periodo,
                valor
            FROM fintz_itens_contabeis_ts
            {where}
            ORDER BY time DESC, item
            LIMIT ${len(params)}
        """
        rows = await self._pool.fetch(query, *params)
        return [dict(r) for r in rows]

    async def get_itens_latest(
        self,
        ticker: str,
        tipo_periodo: str = "12M",
        itens: list[str] | None = None,
    ) -> dict[str, Any]:
        """Snapshot mais recente dos itens contábeis de um ticker."""
        where = "WHERE ticker = $1 AND tipo_periodo = $2"
        params: list[Any] = [ticker.upper(), tipo_periodo]

        if itens:
            params.append(itens)
            where += f" AND item = ANY(${len(params)})"

        query = f"""
            SELECT DISTINCT ON (item)
                item,
                valor,
                time::date AS data_ref
            FROM fintz_itens_contabeis_ts
            {where}
            ORDER BY item, time DESC
        """
        rows = await self._pool.fetch(query, *params)
        return {
            r["item"]: {
                "valor": float(r["valor"]) if r["valor"] is not None else None,
                "data_ref": str(r["data_ref"]),
            }
            for r in rows
        }

    # ── Utilitários ───────────────────────────────────────────────────────────

    async def list_tickers(self, dataset: str = "cotacoes") -> list[str]:
        """Lista tickers disponíveis em uma hypertable."""
        table_map = {
            "cotacoes": "fintz_cotacoes_ts",
            "indicadores": "fintz_indicadores_ts",
            "itens": "fintz_itens_contabeis_ts",
        }
        table = table_map.get(dataset, "fintz_cotacoes_ts")
        rows = await self._pool.fetch(f"SELECT DISTINCT ticker FROM {table} ORDER BY ticker")
        return [r["ticker"] for r in rows]

    async def get_coverage(self, ticker: str) -> dict[str, Any]:
        """Retorna cobertura temporal de dados para um ticker."""
        queries = {
            "cotacoes": "SELECT MIN(time)::date AS inicio, MAX(time)::date AS fim, COUNT(*) AS registros FROM fintz_cotacoes_ts WHERE ticker = $1",
            "indicadores": "SELECT MIN(time)::date AS inicio, MAX(time)::date AS fim, COUNT(*) AS registros FROM fintz_indicadores_ts WHERE ticker = $1",
            "itens_contabeis": "SELECT MIN(time)::date AS inicio, MAX(time)::date AS fim, COUNT(*) AS registros FROM fintz_itens_contabeis_ts WHERE ticker = $1",
        }
        result: dict[str, Any] = {"ticker": ticker.upper()}
        for key, q in queries.items():
            row = await self._pool.fetchrow(q, ticker.upper())
            result[key] = {
                "inicio": str(row["inicio"]) if row["inicio"] else None,
                "fim": str(row["fim"]) if row["fim"] else None,
                "registros": row["registros"],
            }
        return result
