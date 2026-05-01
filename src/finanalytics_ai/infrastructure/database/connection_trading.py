"""Conexão paralela read-only ao schema trading_engine_orders.

Engine + session_factory dedicados, com pool menor que o principal (UI light).
None se `trading_engine_reader_url` não configurado — caller deve checar com
`is_trading_engine_enabled()` antes de criar sessions.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
import structlog

from finanalytics_ai.config import get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = structlog.get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def is_trading_engine_enabled() -> bool:
    return get_settings().trading_engine_reader_url is not None


def get_trading_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        if settings.trading_engine_reader_url is None:
            raise RuntimeError(
                "trading_engine_reader_url não configurado — set TRADING_ENGINE_READER_URL no .env"
            )
        _engine = create_async_engine(
            str(settings.trading_engine_reader_url),
            pool_size=settings.trading_engine_pool_size,
            max_overflow=settings.trading_engine_max_overflow,
            pool_pre_ping=True,
        )
        logger.info(
            "trading_engine.engine.created",
            pool_size=settings.trading_engine_pool_size,
        )
    return _engine


def get_trading_engine_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_trading_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def get_trading_engine_session() -> AsyncGenerator[AsyncSession, None]:
    factory = get_trading_engine_session_factory()
    async with factory() as session:
        # Read-only: nada de commit/rollback. Conexão volta limpa pro pool.
        yield session


async def close_trading_engine() -> None:
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None
        logger.info("trading_engine.engine.closed")
