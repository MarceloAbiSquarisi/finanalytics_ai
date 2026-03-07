"""
Injeção de Dependência para rotas FastAPI.

Design decision: FastAPI Depends() como ponte entre o framework e
nossa DI manual. As funções aqui são as únicas que conhecem tanto
FastAPI quanto a infraestrutura concreta. Domínio e Application
permanecem limpos.
"""
from __future__ import annotations
from typing import AsyncGenerator, TYPE_CHECKING
from fastapi import Depends
from starlette.requests import Request
from sqlalchemy.ext.asyncio import AsyncSession
from finanalytics_ai.infrastructure.database.connection import get_session_factory
from finanalytics_ai.infrastructure.database.repositories.portfolio_repo import SQLPortfolioRepository
from finanalytics_ai.infrastructure.database.repositories.event_store_repo import SQLEventStore
from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient
from finanalytics_ai.application.services.portfolio_service import PortfolioService
from finanalytics_ai.application.services.event_processor import EventProcessorService

# Singleton do cliente BRAPI (reutiliza conexão HTTP)
_brapi_client: BrapiClient | None = None

def get_brapi_client() -> BrapiClient:
    global _brapi_client
    if _brapi_client is None:
        _brapi_client = BrapiClient()
    return _brapi_client


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_portfolio_service(
    session: AsyncSession = Depends(get_db_session),
    brapi: BrapiClient = Depends(get_brapi_client),
) -> PortfolioService:
    repo = SQLPortfolioRepository(session)
    return PortfolioService(repo=repo, market_data=brapi)


async def get_event_processor(
    session: AsyncSession = Depends(get_db_session),
    brapi: BrapiClient = Depends(get_brapi_client),
) -> EventProcessorService:
    store = SQLEventStore(session)
    return EventProcessorService(event_store=store, market_data=brapi)


async def get_watchlist_service(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> "WatchlistService":
    """
    Cria WatchlistService com sessão gerenciada pelo get_db_session.
    Garante commit/rollback automático após cada request.
    market_client vem do app.state (injetado no startup).
    """
    from finanalytics_ai.application.services.watchlist_service import WatchlistService
    from finanalytics_ai.infrastructure.database.repositories.watchlist_repo import WatchlistRepository
    from fastapi import HTTPException

    market = getattr(request.app.state, "market_client", None)
    if market is None:
        raise HTTPException(status_code=503, detail="Market data client não disponível.")
    return WatchlistService(WatchlistRepository(session), market)
