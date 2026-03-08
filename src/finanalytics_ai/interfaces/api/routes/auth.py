"""
finanalytics_ai.interfaces.api.routes.auth
───────────────────────────────────────────
POST /api/v1/auth/register   — cria conta + retorna tokens
POST /api/v1/auth/login      — autentica + retorna tokens
POST /api/v1/auth/refresh    — renova access token via refresh token
GET  /api/v1/auth/me         — retorna dados do usuário logado
POST /api/v1/auth/logout     — invalida tokens (client-side; sem blacklist por ora)
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from finanalytics_ai.application.services.auth_service import AuthService
from finanalytics_ai.domain.auth.entities import (
    UserRegistration, AuthError, TokenPair, User,
    EmailAlreadyExistsError, InvalidCredentialsError,
    TokenExpiredError, TokenInvalidError,
)
from finanalytics_ai.infrastructure.auth.jwt_handler import get_jwt_handler
from finanalytics_ai.infrastructure.auth.password_hasher import get_password_hasher
from finanalytics_ai.infrastructure.database.repositories.user_repo import UserRepository
from finanalytics_ai.interfaces.api.dependencies import get_db_session, get_current_user

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/auth", tags=["Autenticação"])


# ── Schemas de request/response ───────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email:     str  = Field(..., min_length=5, max_length=255)
    password:  str  = Field(..., min_length=8, max_length=128)
    full_name: str  = Field(..., min_length=2, max_length=255)


class LoginRequest(BaseModel):
    email:    str = Field(..., min_length=5)
    password: str = Field(..., min_length=1)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    expires_in:    int


class UserResponse(BaseModel):
    user_id:   str
    email:     str
    full_name: str
    role:      str
    is_active: bool


# ── DI ────────────────────────────────────────────────────────────────────────

def _svc(session: AsyncSession) -> AuthService:
    return AuthService(
        user_repo = UserRepository(session),
        hasher    = get_password_hasher(),
        jwt       = get_jwt_handler(),
    )


def _auth_error_to_http(err: AuthError) -> HTTPException:
    """Mapeia erros de domínio para HTTP status codes."""
    from finanalytics_ai.domain.auth.entities import AuthErrorCode
    code_map = {
        AuthErrorCode.INVALID_CREDENTIALS:       status.HTTP_401_UNAUTHORIZED,
        AuthErrorCode.TOKEN_EXPIRED:             status.HTTP_401_UNAUTHORIZED,
        AuthErrorCode.TOKEN_INVALID:             status.HTTP_401_UNAUTHORIZED,
        AuthErrorCode.USER_NOT_FOUND:            status.HTTP_404_NOT_FOUND,
        AuthErrorCode.EMAIL_ALREADY_EXISTS:      status.HTTP_409_CONFLICT,
        AuthErrorCode.INACTIVE_USER:             status.HTTP_403_FORBIDDEN,
        AuthErrorCode.INSUFFICIENT_PERMISSIONS:  status.HTTP_403_FORBIDDEN,
    }
    http_code = code_map.get(err.code, status.HTTP_400_BAD_REQUEST)
    return HTTPException(status_code=http_code, detail=err.message)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    session: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    """Cria nova conta e retorna par de tokens para login imediato."""
    try:
        reg   = UserRegistration(body.email, body.password, body.full_name)
        pair  = await _svc(session).register(reg)
        return TokenResponse(
            access_token  = pair.access_token,
            refresh_token = pair.refresh_token,
            expires_in    = pair.expires_in,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except AuthError as e:
        raise _auth_error_to_http(e)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    session: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    """Autentica usuário e retorna par de tokens."""
    try:
        pair = await _svc(session).login(body.email, body.password)
        return TokenResponse(
            access_token  = pair.access_token,
            refresh_token = pair.refresh_token,
            expires_in    = pair.expires_in,
        )
    except AuthError as e:
        raise _auth_error_to_http(e)



@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    session: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    """Renova o access token usando o refresh token."""
    try:
        pair = await _svc(session).refresh(body.refresh_token)
        return TokenResponse(
            access_token  = pair.access_token,
            refresh_token = pair.refresh_token,
            expires_in    = pair.expires_in,
        )
    except AuthError as e:
        raise _auth_error_to_http(e)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    """Retorna dados do usuário autenticado."""
    return UserResponse(
        user_id   = current_user.user_id,
        email     = current_user.email,
        full_name = current_user.full_name,
        role      = current_user.role.value,
        is_active = current_user.is_active,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(current_user: User = Depends(get_current_user)) -> None:
    """
    Logout client-side: o cliente descarta o token.
    Sem blacklist de tokens nesta sprint (adicionaremos com Redis na próxima).
    """
    logger.info("auth.logout", user_id=current_user.user_id)
