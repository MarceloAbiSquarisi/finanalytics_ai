"""
Testes de integração — EventProcessor com Postgres real.

Requerem banco de dados rodando. Marcados com @pytest.mark.integration.

Para rodar:
    # Apenas integração
    uv run pytest tests/integration/ -v -m integration

    # Todos (unit + integration) — precisa de banco
    DATABASE_URL=postgresql+asyncpg://finanalytics:secret@localhost:5432/finanalytics_test \\
    uv run pytest -v

    # Excluir integração (CI sem banco)
    uv run pytest tests/unit/ -v

Pré-requisito:
    1. Postgres rodando (docker compose up postgres).
    2. Banco de teste criado:
       docker exec -it finanalytics_postgres psql -U finanalytics -c "CREATE DATABASE finanalytics_test;"
    3. Migrations aplicadas:
       DATABASE_URL=...finanalytics_test uv run alembic upgrade head

Decisão de fixture:
    Usamos um único engine por sessão de testes (session-scoped) para
    evitar overhead de conexão. Cada teste recebe uma transação própria
    que é revertida no teardown — o banco fica limpo sem truncate.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from finanalytics_ai.application.services.event_processor import EventProcessor
from finanalytics_ai.application.services.event_publisher import EventPublisher
from finanalytics_ai.config import Settings
from finanalytics_ai.container import build_event_processor
from finanalytics_ai.domain.events.entities import EventStatus, EventType
from finanalytics_ai.infrastructure.database.repositories.event_repository import (
    PostgresEventRepository,
)

# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def integration_settings() -> Settings:
    """Settings com DATABASE_URL apontando para banco de teste.

    O banco de teste é separado do de desenvolvimento para evitar
    poluição de dados.
    """
    return Settings(
        database_url="postgresql+asyncpg://finanalytics:secret@localhost:5432/finanalytics_test",  # type: ignore[arg-type]
        app_secret_key="integration-test-secret",
        log_level="ERROR",
        metrics_enabled=False,
        event_max_retries=3,
        event_retry_base_delay=0.01,  # acelera testes de retry
    )


@pytest_asyncio.fixture(scope="session")
async def engine(integration_settings: Settings):  # type: ignore[no-untyped-def]
    eng = create_async_engine(
        str(integration_settings.database_url),
        pool_size=2,
        max_overflow=0,
        echo=False,
    )
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:  # type: ignore[no-untyped-def]
    """Fixture de sessão com rollback automático após cada teste.

    Padrão 'nested transaction': abre uma SAVEPOINT, roda o teste,
    faz ROLLBACK TO SAVEPOINT — banco limpo sem truncate/delete.
    """
    async with engine.connect() as conn:
        await conn.begin()
        async with AsyncSession(bind=conn, expire_on_commit=False) as s:  # type: ignore[call-arg]
            nested = await conn.begin_nested()
            yield s
            await nested.rollback()
        await conn.rollback()


# ──────────────────────────────────────────────────────────────────────────────
# Testes
# ──────────────────────────────────────────────────────────────────────────────


class TestEventProcessorIntegration:
    async def test_publish_and_process_completes(
        self, session: AsyncSession, integration_settings: Settings
    ) -> None:
        """Ciclo completo: publish → process → verify no banco."""
        publisher = EventPublisher(session)
        event = await publisher.publish_fintz_sync_completed(
            dataset="cotacoes",
            rows_synced=500,
            errors=10,
            duration_s=3.0,
        )

        processor = build_event_processor(session, integration_settings)
        record = await processor.process(event)

        assert record.status == EventStatus.COMPLETED
        assert record.result_metadata.get("dataset") == "cotacoes"

        # Verifica persistência real no banco
        repo = PostgresEventRepository(session)
        persisted = await repo.get_processing_record(event.id)
        assert persisted is not None
        assert persisted.status == EventStatus.COMPLETED

    async def test_idempotency_on_real_db(
        self, session: AsyncSession, integration_settings: Settings
    ) -> None:
        """Reprocessar o mesmo evento não cria duplicata no banco."""
        from finanalytics_ai.exceptions import EventAlreadyProcessedError

        publisher = EventPublisher(session)
        event = await publisher.publish_fintz_sync_completed(
            dataset="indicadores", rows_synced=100, errors=0, duration_s=1.0
        )
        processor = build_event_processor(session, integration_settings)
        await processor.process(event)

        with pytest.raises(EventAlreadyProcessedError):
            await processor.process(event)

    async def test_dead_letter_requeue_cycle(
        self, session: AsyncSession, integration_settings: Settings
    ) -> None:
        """Evento vai para dead-letter, é recolocado em fila e processado com sucesso."""
        publisher = EventPublisher(session)
        # Publica com alta taxa de erro → vai para dead-letter
        event = await publisher.publish_fintz_sync_completed(
            dataset="cotacoes",
            rows_synced=10,
            errors=990,   # 99% de erro — acima do threshold de 10%
            duration_s=1.0,
        )

        processor = build_event_processor(session, integration_settings)
        record = await processor.process(event)
        assert record.status == EventStatus.DEAD_LETTER

        # Recoloca em fila com payload corrigido não é possível aqui
        # (o evento é imutável), mas podemos fazer requeue para testar o fluxo
        repo = PostgresEventRepository(session)
        requeued = await repo.requeue_dead_letter(event.id)
        assert requeued is True

        # Agora o registro está em 'pending' com attempt=0
        record_after = await repo.get_processing_record(event.id)
        assert record_after is not None
        assert record_after.status == EventStatus.PENDING
        assert record_after.attempt == 0

    async def test_get_pending_events_returns_published(
        self, session: AsyncSession
    ) -> None:
        publisher = EventPublisher(session)
        await publisher.publish_fintz_sync_completed(
            dataset="cotacoes", rows_synced=1, errors=0, duration_s=0.1
        )

        repo = PostgresEventRepository(session)
        pending = await repo.get_pending_events(
            event_type=EventType.FINTZ_SYNC_COMPLETED, limit=10
        )
        assert len(pending) >= 1
        assert all(e.event_type == EventType.FINTZ_SYNC_COMPLETED for e in pending)
