"""
finanalytics_ai.application.services.auth_service
───────────────────────────────────────────────────
Orquestra registro, login e refresh de tokens.

Não contém lógica de negócio pura — delega ao domínio (User, erros)
e à infra (hasher, JWT, repositório). Camada de aplicação correta.
"""

from __future__ import annotations
from dataclasses import dataclass

from typing import TYPE_CHECKING

import structlog

from finanalytics_ai.domain.auth.entities import (
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    TokenPair,
    User,
    UserNotFoundError,
    UserRegistration,
)

if TYPE_CHECKING:
    from finanalytics_ai.infrastructure.auth.jwt_handler import JWTHandler
    from finanalytics_ai.infrastructure.auth.password_hasher import PasswordHasher
    from finanalytics_ai.infrastructure.database.repositories.user_repo import UserRepository

logger = structlog.get_logger(__name__)




@dataclass
class TOTPPendingResult:
    """Retorno do login quando 2FA está ativo."""
    totp_required: bool = True
    totp_token: str = ""

class AuthService:
    def __init__(
        self,
        user_repo: UserRepository,
        hasher: PasswordHasher,
        jwt: JWTHandler,
    ) -> None:
        self._repo = user_repo
        self._hasher = hasher
        self._jwt = jwt

    async def register(self, registration: UserRegistration) -> TokenPair:
        """
        Registra novo usuário.
        Lança EmailAlreadyExistsError se email já cadastrado.
        Retorna TokenPair para login imediato após registro.
        """
        if await self._repo.email_exists(registration.email):
            raise EmailAlreadyExistsError(registration.email)

        hashed = self._hasher.hash(registration.password)
        user = User.new(
            email=registration.email,
            hashed_password=hashed,
            full_name=registration.full_name,
        )
        user = await self._repo.create(user)
        logger.info("auth.registered", user_id=user.user_id)
        return self._jwt.create_token_pair(user)

    async def login(self, email: str, password: str, remember_me: bool = False) -> TokenPair:
        """
        Autentica usuário.
        Lança InvalidCredentialsError para email/senha errados (mensagem genérica
        — não indica qual dos dois está errado, por segurança).
        """
        user = await self._repo.find_by_email(email)
        if user is None:
            # Roda o hash mesmo assim para evitar timing attack por enumeração
            self._hasher.verify(password, "$2b$12$invalidhashpadding0000000000000000000000000")
            raise InvalidCredentialsError()

        if not self._hasher.verify(password, user.hashed_password):
            raise InvalidCredentialsError()

        user.ensure_active()
        await self._repo.update_last_login(user.user_id)
        logger.info("auth.login", user_id=user.user_id)
        return self._jwt.create_token_pair(user)

    async def refresh(self, refresh_token: str) -> TokenPair:
        """
        Gera novo par de tokens a partir do refresh token.
        Lança TokenInvalidError/TokenExpiredError se inválido.
        """
        payload = self._jwt.decode_refresh(refresh_token)
        user = await self._repo.find_by_id(payload.sub)
        if user is None:
            raise UserNotFoundError(payload.sub)
        user.ensure_active()
        logger.info("auth.refresh", user_id=user.user_id)
        return self._jwt.create_token_pair(user)

    async def get_current_user(self, user_id: str) -> User:
        """Busca usuário por ID (após decode do token)."""
        user = await self._repo.find_by_id(user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        user.ensure_active()
        return user

    async def save_totp_secret(
        self, user_id: str, secret: str, enabled: bool = False
    ) -> None:
        """Salva secret TOTP (pendente de confirmação ou ativo)."""
        await self._repo.update_totp(user_id, secret=secret, enabled=enabled)

    async def verify_and_enable_totp(self, user_id: str, code: str) -> bool:
        """Verifica código TOTP e ativa 2FA se correto."""
        from finanalytics_ai.infrastructure.auth.totp_handler import get_totp_handler
        user = await self._repo.get_by_id(user_id)
        if not user or not user.totp_secret:
            return False
        handler = get_totp_handler()
        if not handler.verify(user.totp_secret, code):
            return False
        await self._repo.update_totp(user_id, secret=user.totp_secret, enabled=True)
        return True

    async def disable_totp(self, user_id: str, code: str) -> bool:
        """Desativa 2FA após verificação do código atual."""
        from finanalytics_ai.infrastructure.auth.totp_handler import get_totp_handler
        user = await self._repo.get_by_id(user_id)
        if not user or not user.totp_secret or not user.totp_enabled:
            return False
        handler = get_totp_handler()
        if not handler.verify(user.totp_secret, code):
            return False
        await self._repo.update_totp(user_id, secret=None, enabled=False)
        return True

    async def authenticate_totp(self, totp_token: str, code: str) -> "TokenPair":
        """Valida token temporário de TOTP e retorna tokens reais."""
        from finanalytics_ai.infrastructure.auth.totp_handler import get_totp_handler
        # Decodifica token temporário (tipo "totp_pending")
        try:
            payload = self._jwt.decode(totp_token)
            if payload.token_type != "totp_pending":
                raise TokenInvalidError("Token inválido para 2FA.")
        except Exception as exc:
            from finanalytics_ai.domain.auth.entities import TokenInvalidError as TIE
            raise TIE("Token de 2FA inválido ou expirado.") from exc

        user = await self._repo.get_by_id(payload.sub)
        if not user or not user.is_active:
            from finanalytics_ai.domain.auth.entities import UserNotFoundError
            raise UserNotFoundError()
        if not user.totp_secret or not user.totp_enabled:
            from finanalytics_ai.domain.auth.entities import TokenInvalidError as TIE
            raise TIE("2FA não ativo para este usuário.")

        handler = get_totp_handler()
        if not handler.verify(user.totp_secret, code):
            from finanalytics_ai.domain.auth.entities import InvalidCredentialsError
            raise InvalidCredentialsError()

        logger.info("auth.totp.authenticated", user_id=user.user_id)
        return self._jwt.create_token_pair(user)
