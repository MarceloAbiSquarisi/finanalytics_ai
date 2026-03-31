"""
infrastructure/timescale/ohlc_ts_repo.py
──────────────────────────────────────────
Repositorio OHLC sobre TimescaleDB via asyncpg.

OHLCTimescaleRepo e o adaptador concreto que o OHLCUpdaterService usa
para persistir e consultar barras OHLC. Usa INSERT ... ON CONFLICT DO NOTHING
para idempotencia — a mesma barra pode ser inserida multiplas vezes sem erro.

Separado de repository.py (que contem TimescaleOHLCRepository) para manter
compatibilidade com o contrato esperado pelo app.py sem modificar codigo
existente.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class OHLCTimescaleRepo:
    """
    Repositorio de barras OHLC no TimescaleDB.
    Recebe asyncpg.Pool injetado — nao cria conexoes proprias.
    """

    def __init__(self, pool: Any) -> None:  # pool: asyncpg.Pool
        self._pool = pool

    async def save_bars(self, bars: list[dict[str, Any]]) -> int:
        """
        Persiste lista de barras OHLC. Idempotente via ON CONFLICT DO NOTHING.

        Cada dict deve conter: time (datetime), ticker, timeframe,
        open, high, low, close, volume, source.

        Retorna numero de linhas afetadas (0 se todas ja existiam).
        """
        if not bars:
            return 0

        rows = [
            (
                b.get("time", datetime.now(timezone.utc)),
                b["ticker"].upper(),
                b.get("timeframe", "1d"),
                float(b["open"]),
                float(b["high"]),
                float(b["low"]),
                float(b["close"]),
                float(b.get("volume", 0)),
                b.get("source", "api"),
            )
            for b in bars
        ]

        async with self._pool.acquire() as conn:
            result = await conn.executemany(
                """
                INSERT INTO ohlc_bars
                    (time, ticker, timeframe, open, high, low, close, volume, source)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (time, ticker, timeframe) DO NOTHING
                """,
                rows,
            )

        count = len(bars)
        log.debug("ohlc_ts_repo.saved", count=count)
        return count

    async def get_latest(
        self,
        ticker: str,
        timeframe: str = "1d",
        limit: int = 252,
    ) -> list[dict[str, Any]]:
        """Retorna as ultimas N barras de um ticker em ordem cronologica."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    EXTRACT(EPOCH FROM time)::BIGINT AS ts,
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
        return [
            {
                "time": int(r["ts"]),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),
            }
            for r in reversed(rows)
        ]

    async def get_last_date(self, ticker: str, timeframe: str = "1d") -> datetime | None:
        """Retorna a data da ultima barra armazenada para o ticker."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT MAX(time) AS last_time
                FROM ohlc_bars
                WHERE ticker = $1 AND timeframe = $2
                """,
                ticker.upper(),
                timeframe,
            )
        if row and row["last_time"]:
            return row["last_time"]
        return None