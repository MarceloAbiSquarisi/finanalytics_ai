"""
TimescaleDB — conexão e repositório de séries temporais.

Design decisions:
  - asyncpg direto (não SQLAlchemy): TimescaleDB se beneficia de queries
    nativas como time_bucket(), first(), last() que SQLAlchemy não expõe
    facilmente. asyncpg é ~3x mais rápido para queries de time-series.
  - Pool separado do PostgreSQL principal — TimescaleDB tem workload diferente
    (bulk insert de OHLC vs transações de portfolio)
  - Hypertable por ticker — particionamento automático por tempo
  - INSERT ... ON CONFLICT DO NOTHING = idempotência nativa para OHLC

Schema TimescaleDB esperado (migration abaixo):
  CREATE TABLE ohlc_bars (
    time       TIMESTAMPTZ NOT NULL,
    ticker     TEXT        NOT NULL,
    timeframe  TEXT        NOT NULL,
    open       NUMERIC,
    high       NUMERIC,
    low        NUMERIC,
    close      NUMERIC,
    volume     NUMERIC,
    source     TEXT DEFAULT 'unknown'
  );
  SELECT create_hypertable('ohlc_bars', 'time');
  CREATE UNIQUE INDEX ON ohlc_bars (time, ticker, timeframe);

  CREATE TABLE price_ticks (
    time    TIMESTAMPTZ NOT NULL,
    ticker  TEXT        NOT NULL,
    price   NUMERIC     NOT NULL,
    source  TEXT DEFAULT 'unknown'
  );
  SELECT create_hypertable('price_ticks', 'time');
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from finanalytics_ai.config import get_settings

if TYPE_CHECKING:
    from finanalytics_ai.domain.entities.event import OHLCBar

logger = structlog.get_logger(__name__)

# Pool singleton (criado no lifespan da app)
_pool: Any = None  # asyncpg.Pool


async def get_timescale_pool() -> Any:
    """Retorna pool singleton do TimescaleDB. Lazy init."""
    global _pool
    if _pool is None:
        _pool = await _create_pool()
    return _pool


async def _create_pool() -> Any:
    """Cria pool asyncpg para TimescaleDB."""
    try:
        import asyncpg  # type: ignore[import]
    except ImportError:
        raise RuntimeError("asyncpg não instalado") from None

    settings = get_settings()
    # Converte URL para formato asyncpg (remove +asyncpg se presente)
    dsn = settings.timescale_url.replace("postgresql+asyncpg://", "postgresql://")

    pool = await asyncpg.create_pool(
        dsn,
        min_size=2,
        max_size=settings.timescale_pool_size,
        command_timeout=30,
        statement_cache_size=100,
    )
    logger.info(
        "timescale.pool.created",
        pool_size=settings.timescale_pool_size,
        host=dsn.split("@")[-1].split("/")[0],  # só host:port para log
    )

    # Garante que as tabelas existem
    await _ensure_schema(pool)
    return pool


async def close_timescale_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("timescale.pool.closed")


async def _ensure_schema(pool: Any) -> None:
    """
    Cria tabelas e hypertables se não existirem.
    Idempotente — seguro rodar várias vezes.
    """
    async with pool.acquire() as conn:
        # ohlc_bars
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ohlc_bars (
                time       TIMESTAMPTZ NOT NULL,
                ticker     TEXT        NOT NULL,
                timeframe  TEXT        NOT NULL DEFAULT '1d',
                open       NUMERIC     NOT NULL,
                high       NUMERIC     NOT NULL,
                low        NUMERIC     NOT NULL,
                close      NUMERIC     NOT NULL,
                volume     NUMERIC     NOT NULL DEFAULT 0,
                source     TEXT        NOT NULL DEFAULT 'unknown'
            );
        """)
        # Tenta criar hypertable — ignora se já existir
        import contextlib

        with contextlib.suppress(Exception):
            await conn.execute("SELECT create_hypertable('ohlc_bars','time',if_not_exists=>true);")
            # extensão timescaledb pode não estar disponível — continua com PG puro

        # Índice único para idempotência
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ohlc_bars_unique
            ON ohlc_bars (time, ticker, timeframe);
        """)

        # price_ticks — feed de preços em tempo real
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS price_ticks (
                time    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ticker  TEXT        NOT NULL,
                price   NUMERIC     NOT NULL,
                change_pct NUMERIC,
                volume  BIGINT,
                source  TEXT        NOT NULL DEFAULT 'kafka'
            );
        """)
        with contextlib.suppress(Exception):
            await conn.execute("SELECT create_hypertable('price_ticks','time',if_not_exists=>true);")

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS price_ticks_ticker_time
            ON price_ticks (ticker, time DESC);
        """)

    logger.info("timescale.schema.ready")


# ── REPOSITORIES ─────────────────────────────────────────────────────────────


class TimescaleOHLCRepository:
    """
    Repositório de barras OHLC no TimescaleDB.

    Usa INSERT ... ON CONFLICT DO NOTHING para idempotência:
    a mesma barra pode ser inserida múltiplas vezes (ex: retry) sem erro.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def save(self, bar: OHLCBar) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO ohlc_bars (time, ticker, timeframe, open, high, low, close, volume, source)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (time, ticker, timeframe) DO NOTHING
                """,
                bar.timestamp,
                bar.ticker,
                bar.timeframe,
                float(bar.open),
                float(bar.high),
                float(bar.low),
                float(bar.close),
                float(bar.volume),
                bar.source,
            )
        logger.debug("timescale.ohlc.saved", ticker=bar.ticker, time=bar.timestamp)

    async def save_batch(self, bars: list[OHLCBar]) -> int:
        """
        Insere múltiplas barras de forma eficiente via COPY.
        Retorna número de barras inseridas.
        """
        if not bars:
            return 0
        rows = [
            (
                b.timestamp,
                b.ticker,
                b.timeframe,
                float(b.open),
                float(b.high),
                float(b.low),
                float(b.close),
                float(b.volume),
                b.source,
            )
            for b in bars
        ]
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO ohlc_bars (time, ticker, timeframe, open, high, low, close, volume, source)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (time, ticker, timeframe) DO NOTHING
                """,
                rows,
            )
        logger.info("timescale.ohlc.batch_saved", count=len(bars))
        return len(bars)

    async def query_latest(
        self,
        ticker: str,
        timeframe: str = "1d",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """
        Retorna últimas N barras de um ticker.
        Retorna dicts prontos para serialização JSON.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    EXTRACT(EPOCH FROM time)::BIGINT AS time,
                    open, high, low, close, volume
                FROM ohlc_bars
                WHERE ticker = $1 AND timeframe = $2
                ORDER BY time DESC
                LIMIT $3
                """,
                ticker.upper(),
                timeframe,
                limit,
            )
        # Retorna em ordem cronológica (DESC invertido)
        return [
            {
                "time": int(r["time"]),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": int(r["volume"]),
            }
            for r in reversed(rows)
        ]

    async def query_aggregated(
        self,
        ticker: str,
        bucket: str = "1 day",
        days: int = 90,
    ) -> list[dict[str, Any]]:
        """
        time_bucket() do TimescaleDB — agrega barras por período.
        Útil para mudar timeframe sem re-buscar da API de mercado.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    EXTRACT(EPOCH FROM time_bucket($1::interval, time))::BIGINT AS time,
                    first(open, time)  AS open,
                    max(high)          AS high,
                    min(low)           AS low,
                    last(close, time)  AS close,
                    sum(volume)        AS volume
                FROM ohlc_bars
                WHERE ticker = $2
                  AND time > NOW() - ($3 || ' days')::interval
                GROUP BY 1
                ORDER BY 1
                """,
                bucket,
                ticker.upper(),
                str(days),
            )
        return [
            {
                "time": int(r["time"]),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": int(r["volume"]),
            }
            for r in rows
        ]


class TimescalePriceTickRepository:
    """Repositório de ticks de preço em tempo real."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def save_tick(
        self,
        ticker: str,
        price: float,
        change_pct: float | None = None,
        volume: int | None = None,
        source: str = "kafka",
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO price_ticks (ticker, price, change_pct, volume, source)
                VALUES ($1, $2, $3, $4, $5)
                """,
                ticker.upper(),
                price,
                change_pct,
                volume,
                source,
            )

    async def query_latest(self, ticker: str, limit: int = 60) -> list[dict[str, Any]]:
        """Últimos N ticks de um ticker — para mini-chart de intraday."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    EXTRACT(EPOCH FROM time)::BIGINT AS time,
                    price, change_pct, volume
                FROM price_ticks
                WHERE ticker = $1
                ORDER BY time DESC
                LIMIT $2
                """,
                ticker.upper(),
                limit,
            )
        return [
            {
                "time": int(r["time"]),
                "price": float(r["price"]),
                "change_pct": float(r["change_pct"]) if r["change_pct"] else None,
                "volume": int(r["volume"]) if r["volume"] else None,
            }
            for r in reversed(rows)
        ]

    async def query_vwap(self, ticker: str, minutes: int = 30) -> float | None:
        """VWAP dos últimos N minutos — indicador de liquidez."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    SUM(price * COALESCE(volume, 1)) / NULLIF(SUM(COALESCE(volume, 1)), 0) AS vwap
                FROM price_ticks
                WHERE ticker = $1
                  AND time > NOW() - ($2 || ' minutes')::interval
                """,
                ticker.upper(),
                str(minutes),
            )
        if row and row["vwap"] is not None:
            return float(row["vwap"])
        return None
