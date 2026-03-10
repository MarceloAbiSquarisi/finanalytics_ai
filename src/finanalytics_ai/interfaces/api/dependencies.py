"""
Injeção de Dependência para rotas FastAPI.

Design decision: FastAPI Depends() como ponte entre o framework e
nossa DI manual. As funções aqui são as únicas que conhecem tanto
FastAPI quanto a infraestrutura concreta. Domínio e Application
permanecem limpos.

Auth:
  get_current_user — extrai Bearer token do header Authorization,
  decodifica JWT e retorna o User do banco.
  Rotas protegidas: Depends(get_current_user).
  Rotas públicas (login, register, health, docs): sem Depends.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from finanalytics_ai.application.services.event_processor import EventProcessorService
from finanalytics_ai.application.services.portfolio_service import PortfolioService
from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient
from finanalytics_ai.infrastructure.database.connection import get_session_factory
from finanalytics_ai.infrastructure.database.repositories.event_store_repo import SQLEventStore
from finanalytics_ai.infrastructure.database.repositories.portfolio_repo import (
    SQLPortfolioRepository,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession
    from starlette.requests import Request

    from finanalytics_ai.application.services.watchlist_service import WatchlistService
    from finanalytics_ai.domain.auth.entities import User

# OAuth2PasswordBearer lê o token do header Authorization: Bearer <token>
# tokenUrl aponta para o endpoint de login compatível com Swagger UI
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

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


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    """
    Dependência de autenticação para rotas protegidas.

    Fluxo:
      1. OAuth2PasswordBearer extrai token do header Authorization: Bearer <t>
      2. JWTHandler decodifica e valida assinatura + expiração
      3. UserRepository busca User no banco pelo sub (user_id)
      4. Retorna User ou lança 401

    Design decision — não levantamos 403 aqui:
      Verificação de role/permissão é responsabilidade de cada rota
      (ex: require_admin). get_current_user só garante autenticação.
    """
    from finanalytics_ai.domain.auth.entities import TokenExpiredError, TokenInvalidError
    from finanalytics_ai.infrastructure.auth.jwt_handler import get_jwt_handler
    from finanalytics_ai.infrastructure.database.repositories.user_repo import UserRepository

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Não autenticado. Faça login para continuar.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = get_jwt_handler().decode(token)
        if payload.token_type != "access":
            raise credentials_exception
    except (TokenExpiredError, TokenInvalidError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado ou inválido. Faça login novamente.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await UserRepository(session).find_by_id(payload.sub)
    if user is None or not user.is_active:
        raise credentials_exception
    return user


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
) -> WatchlistService:
    """
    Cria WatchlistService com sessão gerenciada pelo get_db_session.
    Garante commit/rollback automático após cada request.
    market_client vem do app.state (injetado no startup).
    """
    from fastapi import HTTPException

    from finanalytics_ai.application.services.watchlist_service import WatchlistService
    from finanalytics_ai.infrastructure.database.repositories.watchlist_repo import (
        WatchlistRepository,
    )

    market = getattr(request.app.state, "market_client", None)
    if market is None:
        raise HTTPException(status_code=503, detail="Market data client não disponível.")
    return WatchlistService(WatchlistRepository(session), market)
