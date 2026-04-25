"""
finanalytics_ai.interfaces.api.routes.auth
───────────────────────────────────────────
POST /api/v1/auth/register   — cria conta + retorna tokens
POST /api/v1/auth/login      — autentica + retorna tokens
POST /api/v1/auth/refresh    — renova access token via refresh token
GET  /api/v1/auth/me         — retorna dados do usuário logado
POST /api/v1/auth/logout     — invalida tokens (client-side; sem blacklist por ora)
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from finanalytics_ai.application.services.auth_service import AuthService
from finanalytics_ai.domain.auth.entities import AuthError, User, UserRegistration
from finanalytics_ai.infrastructure.auth.jwt_handler import get_jwt_handler
from finanalytics_ai.infrastructure.auth.password_hasher import get_password_hasher
from finanalytics_ai.infrastructure.database.repositories.user_repo import UserRepository
from finanalytics_ai.interfaces.api.dependencies import get_current_user, get_db_session

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/auth", tags=["Autenticação"])

# ── Schemas de request/response ───────────────────────────────────────────────


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(..., min_length=2, max_length=255)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=5)
    password: str = Field(..., min_length=1)
    remember_me: bool = Field(default=False, description="Manter logado por 7 dias")


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    user_id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    is_admin: bool = False


# ── DI ────────────────────────────────────────────────────────────────────────


def _svc(session: AsyncSession) -> AuthService:
    return AuthService(
        user_repo=UserRepository(session), hasher=get_password_hasher(), jwt=get_jwt_handler()
    )


def _auth_error_to_http(err: AuthError) -> HTTPException:
    """Mapeia erros de domínio para HTTP status codes."""
    from finanalytics_ai.domain.auth.entities import AuthErrorCode

    code_map = {
        AuthErrorCode.INVALID_CREDENTIALS: status.HTTP_401_UNAUTHORIZED,
        AuthErrorCode.TOKEN_EXPIRED: status.HTTP_401_UNAUTHORIZED,
        AuthErrorCode.TOKEN_INVALID: status.HTTP_401_UNAUTHORIZED,
        AuthErrorCode.USER_NOT_FOUND: status.HTTP_404_NOT_FOUND,
        AuthErrorCode.EMAIL_ALREADY_EXISTS: status.HTTP_409_CONFLICT,
        AuthErrorCode.INACTIVE_USER: status.HTTP_403_FORBIDDEN,
        AuthErrorCode.INSUFFICIENT_PERMISSIONS: status.HTTP_403_FORBIDDEN,
    }
    http_code = code_map.get(err.code, status.HTTP_400_BAD_REQUEST)
    return HTTPException(status_code=http_code, detail=err.message)


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, session: AsyncSession = Depends(get_db_session)
) -> TokenResponse:
    """Cria nova conta e retorna par de tokens para login imediato."""
    try:
        reg = UserRegistration(body.email, body.password, body.full_name)
        pair = await _svc(session).register(reg)
        return TokenResponse(
            access_token=pair.access_token,
            refresh_token=pair.refresh_token,
            expires_in=pair.expires_in,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except AuthError as e:
        raise _auth_error_to_http(e) from e


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest, session: AsyncSession = Depends(get_db_session)
) -> TokenResponse:
    """Autentica usuário e retorna par de tokens."""
    try:
        pair = await _svc(session).login(body.email, body.password, remember_me=body.remember_me)
        return TokenResponse(
            access_token=pair.access_token,
            refresh_token=pair.refresh_token,
            expires_in=pair.expires_in,
        )
    except AuthError as e:
        raise _auth_error_to_http(e) from e


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest, session: AsyncSession = Depends(get_db_session)
) -> TokenResponse:
    """Renova o access token usando o refresh token."""
    try:
        pair = await _svc(session).refresh(body.refresh_token)
        return TokenResponse(
            access_token=pair.access_token,
            refresh_token=pair.refresh_token,
            expires_in=pair.expires_in,
        )
    except AuthError as e:
        raise _auth_error_to_http(e) from e


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    """Retorna dados do usuário autenticado."""
    return UserResponse(
        user_id=current_user.user_id,
        email=current_user.email,
        full_name=current_user.full_name,
        role=current_user.role.value,
        is_active=current_user.is_active,
        is_admin=bool(getattr(current_user, "is_admin", False)),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(current_user: User = Depends(get_current_user)) -> None:
    """
    Logout client-side: o cliente descarta o token.
    Sem blacklist de tokens nesta sprint (adicionaremos com Redis na próxima).
    """
    logger.info("auth.logout", user_id=current_user.user_id)


# ── Sudo mode (re-autenticacao para acoes destrutivas) ─────────────────────────


class SudoRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=128)
    ttl_minutes: int = Field(default=5, ge=1, le=15)


class SudoResponse(BaseModel):
    sudo_token: str
    expires_in: int


@router.post("/sudo", response_model=SudoResponse)
async def sudo_confirm(
    body: SudoRequest,
    current_user: User = Depends(get_current_user),
) -> SudoResponse:
    """
    Re-autentica o usuario logado via senha e emite sudo_token (JWT tipo 'sudo',
    padrao 5min). Deve ser chamado ANTES de qualquer acao destrutiva; o cliente
    envia o sudo_token no header X-Sudo-Token. Inspirado no GitHub sudo mode.
    """
    hasher = get_password_hasher()
    if not hasher.verify(body.password, current_user.hashed_password):
        logger.warning("auth.sudo.failed", user_id=current_user.user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Senha incorreta.",
        )
    token = get_jwt_handler().create_sudo_token(current_user, ttl_minutes=body.ttl_minutes)
    logger.info("auth.sudo.granted", user_id=current_user.user_id, ttl=body.ttl_minutes)
    return SudoResponse(sudo_token=token, expires_in=body.ttl_minutes * 60)


# ── Reset de Senha ────────────────────────────────────────────────────────────


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., min_length=5)


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=8)
    new_password: str = Field(..., min_length=8, max_length=128)


@router.post("/forgot-password", status_code=200)
async def forgot_password(
    body: ForgotPasswordRequest, request: Request, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """
    Solicita redefinição de senha.

    - Gera um token seguro válido por 30 minutos
    - Se SMTP configurado: envia e-mail com o link de reset
    - Se não configurado (dev): retorna o token diretamente na resposta

    Sempre retorna 200 mesmo se o e-mail não existir (segurança contra enumeration).
    """
    from datetime import UTC, datetime, timedelta
    import secrets

    from finanalytics_ai.config import get_settings
    from finanalytics_ai.infrastructure.database.repositories.user_repo import UserRepository
    from finanalytics_ai.infrastructure.email.email_sender import get_email_sender

    repo = UserRepository(session)
    settings = get_settings()
    user = await repo.find_by_email(body.email)

    # Resposta genérica — não revela se o e-mail existe
    generic = {"message": "Se o e-mail estiver cadastrado, você receberá as instruções em breve."}

    if not user:
        return generic

    # Gera token seguro
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.reset_token_expire_minutes)
    await repo.set_reset_token(user.user_id, token, expires_at)
    await session.commit()

    # Monta URL de reset
    base_url = str(request.base_url).rstrip("/")
    reset_url = f"{base_url}/reset-password?token={token}"

    # Tenta enviar e-mail
    sender = get_email_sender()
    email_sent = sender.send_reset_password(user.email, user.full_name, reset_url)

    logger.info(
        "auth.forgot_password",
        user_id=user.user_id,
        email_sent=email_sent,
        smtp_configured=sender.is_configured,
    )

    if email_sent:
        return generic

    # Modo dev: retorna token e URL diretamente (sem SMTP configurado)
    return {
        "message": "SMTP não configurado — use o link abaixo para redefinir a senha.",
        "dev_reset_url": reset_url,
        "dev_token": token,
        "expires_in_minutes": settings.reset_token_expire_minutes,
    }


@router.post("/reset-password", status_code=200)
async def reset_password(
    body: ResetPasswordRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """
    Redefine a senha usando o token recebido por e-mail.
    O token é invalidado após o uso.
    """
    from datetime import UTC, datetime

    from sqlalchemy import select as sa_select

    from finanalytics_ai.infrastructure.auth.password_hasher import get_password_hasher
    from finanalytics_ai.infrastructure.database.repositories.user_repo import (
        UserModel,
        UserRepository,
    )

    repo = UserRepository(session)

    # Busca direto pelo token no model para acessar reset_token_exp
    result = await session.execute(sa_select(UserModel).where(UserModel.reset_token == body.token))
    model = result.scalar_one_or_none()

    if not model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Token inválido ou já utilizado.")

    if not model.reset_token_exp or model.reset_token_exp < datetime.now(UTC):
        model.reset_token = None
        model.reset_token_exp = None
        await session.commit()
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Token expirado. Solicite um novo link."
        )

    # Atualiza senha e invalida token
    hasher = get_password_hasher()
    model.hashed_password = hasher.hash(body.new_password)
    model.reset_token = None
    model.reset_token_exp = None
    await session.commit()

    logger.info("auth.password_reset", user_id=model.user_id)
    return {"message": "Senha redefinida com sucesso. Você já pode fazer login."}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password", status_code=200)
async def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    try:
        await _svc(session).change_password(
            str(current_user.user_id), body.current_password, body.new_password
        )
        return {"message": "Senha alterada com sucesso"}
    except HTTPException:
        raise
    except Exception as err:
        from finanalytics_ai.domain.auth.entities import AuthError

        if isinstance(err, AuthError):
            raise _auth_error_to_http(err) from err
        raise HTTPException(status_code=400, detail=str(err)) from err


# ── 2FA endpoints ─────────────────────────────────────────────────────────────


class TOTPVerifyRequest(BaseModel):
    code: str


class TOTPDisableRequest(BaseModel):
    code: str


@router.post("/2fa/setup", status_code=200)
async def setup_2fa(
    current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Gera novo secret TOTP e retorna QR code base64. Nao ativa ainda."""
    from finanalytics_ai.infrastructure.auth.totp_handler import get_totp_handler

    handler = get_totp_handler()
    secret = handler.generate_secret()
    qr_b64 = handler.get_qr_base64(secret, current_user.email)
    uri = handler.get_provisioning_uri(secret, current_user.email)
    svc = _svc(session)
    await svc.save_totp_secret(str(current_user.user_id), secret, enabled=False)
    return {"secret": secret, "qr_base64": qr_b64, "uri": uri}


@router.post("/2fa/enable", status_code=200)
async def enable_2fa(
    body: TOTPVerifyRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Verifica codigo TOTP e ativa 2FA."""
    svc = _svc(session)
    ok = await svc.verify_and_enable_totp(str(current_user.user_id), body.code)
    if not ok:
        raise HTTPException(status_code=400, detail="Codigo TOTP invalido")
    return {"message": "2FA ativado com sucesso"}


@router.post("/2fa/disable", status_code=200)
async def disable_2fa(
    body: TOTPDisableRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Desativa 2FA verificando o codigo atual."""
    svc = _svc(session)
    ok = await svc.disable_totp(str(current_user.user_id), body.code)
    if not ok:
        raise HTTPException(status_code=400, detail="Codigo TOTP invalido")
    return {"message": "2FA desativado com sucesso"}
