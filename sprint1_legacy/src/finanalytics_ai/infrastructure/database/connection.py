"""
Gerenciamento de conexão assíncrona com PostgreSQL via SQLAlchemy + asyncpg.

Design decision: AsyncEngine com pool configurável via settings.
create_async_engine é lazy — não conecta até o primeiro uso.
O session_factory usa AsyncSession com expire_on_commit=False para
evitar lazy loading implícito em código async.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from finanalytics_ai.config import get_settings
from finanalytics_ai.exceptions import DatabaseError

logger = structlog.get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


class Base(DeclarativeBase):
    """Base declarativa para todos os modelos ORM."""

    pass


def get_engine() -> AsyncEngine:
    """Retorna engine singleton, criando se necessário."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            str(settings.database_url),
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            echo=settings.database_echo,
            pool_pre_ping=True,  # detecta conexões mortas automaticamente
        )
        logger.info("database.engine.created", pool_size=settings.database_pool_size)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,  # evita lazy-load após commit em async
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager para sessions com rollback automático em erro.

    Usage:
        async with get_session() as session:
            result = await session.execute(stmt)
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception as exc:
            await session.rollback()
            raise DatabaseError(
                message=f"Erro na sessão do banco: {exc}",
                context={"error": str(exc)},
            ) from exc


async def close_engine() -> None:
    """Fecha o engine no shutdown da aplicação."""
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None
        logger.info("database.engine.closed")
