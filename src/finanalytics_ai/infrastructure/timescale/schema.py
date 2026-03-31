"""
infrastructure/timescale/schema.py
────────────────────────────────────
Criacao idempotente do schema OHLC no TimescaleDB.

init_schema() e idempotente — seguro rodar no startup sem verificar
se as tabelas ja existem. IF NOT EXISTS garante isso.

Hypertable: a extensao TimescaleDB pode nao estar presente no ambiente
(ex: PostgreSQL puro em dev). create_hypertable e envolvido em
contextlib.suppress para nao quebrar — a tabela continua funcionando
como tabela regular sem particionamento por tempo.
"""
from __future__ import annotations

import contextlib
from typing import Any

import structlog

log = structlog.get_logger(__name__)


async def init_schema(pool: Any) -> None:  # pool: asyncpg.Pool
    """
    Cria tabelas ohlc_bars e price_ticks se nao existirem.
    Tenta criar hypertables — ignora silenciosamente se TimescaleDB
    extension nao estiver disponivel.
    """
    async with pool.acquire() as conn:

        # ohlc_bars — barras OHLC diarias e intraday
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ohlc_bars (
                time      TIMESTAMPTZ NOT NULL,
                ticker    TEXT        NOT NULL,
                timeframe TEXT        NOT NULL DEFAULT '1d',
                open      NUMERIC     NOT NULL,
                high      NUMERIC     NOT NULL,
                low       NUMERIC     NOT NULL,
                close     NUMERIC     NOT NULL,
                volume    NUMERIC     NOT NULL DEFAULT 0,
                source    TEXT        NOT NULL DEFAULT 'unknown'
            );
        """)

        with contextlib.suppress(Exception):
            await conn.execute(
                "SELECT create_hypertable('ohlc_bars','time',if_not_exists=>true);"
            )

        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ohlc_bars_unique
            ON ohlc_bars (time, ticker, timeframe);
        """)

        # price_ticks — feed de precos em tempo real
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS price_ticks (
                time       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ticker     TEXT        NOT NULL,
                price      NUMERIC     NOT NULL,
                change_pct NUMERIC,
                volume     BIGINT,
                source     TEXT        NOT NULL DEFAULT 'market'
            );
        """)

        with contextlib.suppress(Exception):
            await conn.execute(
                "SELECT create_hypertable('price_ticks','time',if_not_exists=>true);"
            )

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS price_ticks_ticker_time
            ON price_ticks (ticker, time DESC);
        """)

    log.info("timescale.schema.ready")