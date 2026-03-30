"""
Fixtures de integracao para o Event Processor.

Usa testcontainers para subir PostgreSQL e Redis reais em Docker.
Os containers sao criados uma vez por sessao de teste (scope=session)
para minimizar overhead.

Pre-requisito: Docker rodando localmente.
Se Docker nao estiver disponivel, todos os testes sao pulados automaticamente.
"""
from __future__ import annotations

import pytest
import pytest_asyncio


def pytest_collection_modifyitems(items: list) -> None:
    """Skip todos os testes se Docker nao disponivel."""
    import shutil
    if not shutil.which("docker"):
        skip = pytest.mark.skip(reason="Docker nao encontrado -- testes de integracao ignorados")
        for item in items:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def postgres_url() -> str:
    """URL de conexao para PostgreSQL via testcontainers."""
    try:
        from testcontainers.postgres import PostgresContainer
        with PostgresContainer("postgres:16-alpine") as pg:
            url = pg.get_connection_url().replace("postgresql://", "postgresql+asyncpg://")
            # Criar tabela event_records
            import asyncio
            from sqlalchemy.ext.asyncio import create_async_engine
            from finanalytics_ai.infrastructure.event_processor.orm_models import Base

            async def _create_tables() -> None:
                engine = create_async_engine(url)
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                await engine.dispose()

            asyncio.get_event_loop().run_until_complete(_create_tables())
            yield url
    except Exception as exc:
        pytest.skip(f"Nao foi possivel iniciar PostgreSQL via testcontainers: {exc}")


@pytest.fixture(scope="session")
def redis_url() -> str:
    """URL de conexao para Redis via testcontainers."""
    try:
        from testcontainers.redis import RedisContainer
        with RedisContainer("redis:7-alpine") as redis:
            yield f"redis://localhost:{redis.get_exposed_port(6379)}"
    except Exception as exc:
        pytest.skip(f"Nao foi possivel iniciar Redis via testcontainers: {exc}")


@pytest_asyncio.fixture(scope="session")
async def session_factory(postgres_url: str):
    """AsyncSessionFactory conectada ao PostgreSQL de teste."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
    engine = create_async_engine(postgres_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def redis_client(redis_url: str):
    """Redis client conectado ao container de teste."""
    from redis.asyncio import from_url
    client = from_url(redis_url)
    yield client
    await client.aclose()
