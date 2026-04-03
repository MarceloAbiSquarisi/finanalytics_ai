"""
FastAPI application — entry point principal.

Registra todos os routers, configura o ciclo de vida (lifespan)
e injeta as dependências de infraestrutura.

Arquitetura de lifespan:
    Usamos o novo padrão `@asynccontextmanager lifespan` (FastAPI 0.93+)
    em vez de `@app.on_event("startup")` (deprecated).
    Isso garante cleanup correto mesmo em caso de erro no startup.

Uso:
    # Desenvolvimento
    uv run uvicorn finanalytics_ai.app:app --reload --host 0.0.0.0 --port 8000

    # Docker
    CMD ["uvicorn", "finanalytics_ai.app:app", "--host", "0.0.0.0", "--port", "8000"]
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from finanalytics_ai.config import get_settings
from finanalytics_ai.container_v2 import bootstrap_v2 as bootstrap, build_engine_v2 as build_engine, build_session_factory_v2 as build_session_factory
from finanalytics_ai.interfaces.api.routes import admin_events
from finanalytics_ai.observability.logging import get_logger

log = get_logger(__name__)
settings = get_settings()

# ──────────────────────────────────────────────────────────────────────────────
# Engine / session factory — singleton por processo
# ──────────────────────────────────────────────────────────────────────────────

_engine = build_engine(settings)
_session_factory = build_session_factory(_engine)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency que fornece uma AsyncSession por request.

    Injetada nos routers via `session: AsyncSession = Depends(get_db)`.
    A sessão é fechada automaticamente ao sair do contexto.

    Nota: não abrimos transação aqui — cada endpoint decide se precisa
    de transação explícita. Isso evita transações desnecessariamente longas.
    """
    async with _session_factory() as session:
        yield session


# ──────────────────────────────────────────────────────────────────────────────
# Lifespan
# ──────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Inicializa e finaliza recursos da aplicação."""
    bootstrap(settings)
    log.info(
        "app_startup",
        environment=settings.environment,
        debug=settings.debug,
    )

    yield  # ← aplicação rodando

    log.info("app_shutdown")
    await _engine.dispose()


# ──────────────────────────────────────────────────────────────────────────────
# App
# ──────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Finanalytics AI",
    version="0.1.0",
    description="Plataforma de análise financeira com pipeline de eventos assíncrono.",
    lifespan=lifespan,
    # Em produção: desabilitar docs (ou proteger com auth)
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.debug else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────────────────────────────────────
# Override de dependency para injetar a session factory real no admin router
#
# O admin_events.py declara get_db como placeholder (NotImplementedError).
# Aqui fazemos o override sem tocar no router — DI explícita via app.
# ──────────────────────────────────────────────────────────────────────────────

app.include_router(
    admin_events.router,
    dependencies=[],
)
app.dependency_overrides[admin_events.get_db] = get_db


# ──────────────────────────────────────────────────────────────────────────────
# Health check
# ──────────────────────────────────────────────────────────────────────────────


@app.get("/health", tags=["ops"])
async def health() -> dict[str, Any]:
    """Health check para load balancer / Kubernetes liveness probe."""
    return {
        "status": "ok",
        "environment": settings.environment,
        "version": "0.1.0",
    }


@app.get("/health/db", tags=["ops"])
async def health_db(session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Verifica conectividade com o banco de dados."""
    from sqlalchemy import text

    try:
        await session.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as exc:
        log.error("health_db_failed", error=str(exc))
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="database unavailable")
