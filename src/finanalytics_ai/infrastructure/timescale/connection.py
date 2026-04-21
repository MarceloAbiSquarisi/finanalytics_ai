"""
infrastructure/timescale/connection.py
──────────────────────────────────────
Pool asyncpg para o TimescaleDB (porta 5433).

ts_pool_available() — probe de conectividade sem lançar exceção.
  Retorna False se TimescaleDB estiver offline (containers down, porta fechada).
  O app.py usa este check antes de tentar criar o pool completo:
    if await ts_pool_available(dsn):
        pool = await init_ts_pool(dsn)
  Isso elimina o stack trace de conexão recusada nos logs de startup.

Timeout de 5s no probe: suficiente para detectar porta fechada (imediato)
ou host inacessível (TCP timeout). Não bloqueia o startup da API.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

log = structlog.get_logger(__name__)


async def ts_pool_available(dsn: str) -> bool:
    """
    Verifica se o TimescaleDB esta acessivel sem lancar excecao.
    Retorna False silenciosamente se offline.
    """
    try:
        import asyncpg

        dsn_clean = dsn.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncio.wait_for(
            asyncpg.connect(dsn_clean, statement_cache_size=0),
            timeout=5.0,
        )
        await conn.close()
        return True
    except Exception as exc:
        log.debug("timescale.probe.failed", error=str(exc))
        return False


async def init_ts_pool(
    dsn: str,
    min_size: int = 2,
    max_size: int = 8,
) -> Any:  # asyncpg.Pool
    """
    Cria e retorna pool asyncpg para o TimescaleDB.
    So deve ser chamado apos ts_pool_available() retornar True.
    """
    import asyncpg

    dsn_clean = dsn.replace("postgresql+asyncpg://", "postgresql://")
    pool = await asyncpg.create_pool(
        dsn_clean,
        min_size=min_size,
        max_size=max_size,
        command_timeout=30,
        statement_cache_size=100,
    )
    log.info("timescale.pool.created", min_size=min_size, max_size=max_size)
    return pool
