"""
Injecao de Dependencia para rotas FastAPI.
"""

from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from finanalytics_ai.application.services.event_processor import EventProcessor
from finanalytics_ai.application.services.portfolio_service import PortfolioService
from finanalytics_ai.application.services.watchlist_service import WatchlistService
from finanalytics_ai.domain.auth.entities import User
from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient
from finanalytics_ai.infrastructure.adapters.cvm_client import CvmClient, get_cvm_client
from finanalytics_ai.infrastructure.adapters.dados_mercado_client import (
    DadosDeMercadoClient,
    get_dados_mercado_client,
)
from finanalytics_ai.infrastructure.adapters.focus_client import FocusClient, get_focus_client
from finanalytics_ai.infrastructure.database.connection import get_session_factory
from finanalytics_ai.infrastructure.database.connection_trading import (
    get_trading_engine_session_factory,
    is_trading_engine_enabled,
)
from finanalytics_ai.infrastructure.database.repositories.event_store_repo import SQLEventStore
from finanalytics_ai.infrastructure.database.repositories.portfolio_repo import (
    SQLPortfolioRepository,
)

EventProcessorService = EventProcessor

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


def get_cvm() -> CvmClient:
    """Retorna o singleton do CvmClient (CVM Dados Abertos — gratuito)."""
    return get_cvm_client()


def get_focus() -> FocusClient:
    """Retorna o singleton do FocusClient (BCB Olinda API — gratuito)."""
    return get_focus_client()


def get_dados_mercado() -> DadosDeMercadoClient:
    """Retorna o singleton do DadosDeMercadoClient (token gratuito necessário)."""
    return get_dados_mercado_client()


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_trading_engine_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Read-only session pro schema trading_engine_orders.

    Levanta 503 se TRADING_ENGINE_READER_URL não estiver configurada — UI
    mostra empty state com link pra docs.
    """
    if not is_trading_engine_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="trading_engine_reader_url não configurado",
        )
    factory = get_trading_engine_session_factory()
    async with factory() as session:
        yield session


async def get_current_user(
    token: str = Depends(oauth2_scheme), session: AsyncSession = Depends(get_db_session)
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
        ) from None

    user = await UserRepository(session).find_by_id(payload.sub)
    if user is None or not user.is_active:
        raise credentials_exception
    return user


async def require_sudo(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    """
    Exige sudo_token valido no header X-Sudo-Token. Uso:
        @router.post(..., dependencies=[Depends(require_sudo)])
    ou:
        async def handler(user: User = Depends(require_sudo)): ...

    Fluxo esperado do cliente:
      1. Chama POST /api/v1/auth/sudo com {password} -> recebe sudo_token (5min)
      2. Chama endpoint destrutivo com header X-Sudo-Token: <token>

    Erros:
      401 "Sudo confirmation required" -> cliente deve abrir modal FASudo
      401 "Sudo token expired"         -> cliente renova via /auth/sudo
      403 "Sudo user mismatch"         -> token nao pertence ao usuario logado
    """
    from finanalytics_ai.domain.auth.entities import TokenExpiredError, TokenInvalidError
    from finanalytics_ai.infrastructure.auth.jwt_handler import get_jwt_handler
    from finanalytics_ai.infrastructure.database.repositories.user_repo import UserRepository

    token = request.headers.get("X-Sudo-Token") or request.headers.get("x-sudo-token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sudo confirmation required.",
            headers={"X-Sudo-Required": "true"},
        )
    try:
        payload = get_jwt_handler().decode(token)
    except TokenExpiredError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sudo token expired.",
            headers={"X-Sudo-Required": "true"},
        ) from None
    except TokenInvalidError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sudo token invalid.",
            headers={"X-Sudo-Required": "true"},
        ) from None

    if payload.token_type != "sudo":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type for sudo.",
            headers={"X-Sudo-Required": "true"},
        )
    if payload.sub != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sudo user mismatch.",
        )
    user = await UserRepository(session).find_by_id(payload.sub)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive.")
    return user


async def get_portfolio_service(
    session: AsyncSession = Depends(get_db_session), brapi: BrapiClient = Depends(get_brapi_client)
) -> PortfolioService:
    repo = SQLPortfolioRepository(session)
    return PortfolioService(repo=repo, market_data=brapi)


async def get_event_processor(
    session: AsyncSession = Depends(get_db_session), brapi: BrapiClient = Depends(get_brapi_client)
) -> EventProcessorService:
    store = SQLEventStore(session)
    return EventProcessorService(event_store=store, market_data=brapi)


async def get_watchlist_service(
    request: Request, session: AsyncSession = Depends(get_db_session)
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
