"""
finanalytics_ai.domain.auth.entities
─────────────────────────────────────
Entidades e value objects do domínio de autenticação.

Design decisions:
  User.user_id é UUID v4 gerado no domínio (não no banco).
    → Permite criar o objeto antes de persistir, facilitando testes.
    → Evita dependência do banco para gerar IDs.

  Senhas NUNCA transitam pelo domínio em texto puro após registro.
    → Domínio recebe hashed_password pronto (responsabilidade da infra).
    → UserRegistration carrega senha em texto apenas durante o registro,
       antes de ser passada ao hasher.

  Roles como enum string:
    → Extensível sem migração de banco (coluna VARCHAR).
    → USER é o padrão; ADMIN permite acesso a endpoints de diagnóstico.

  TokenPayload é imutável (frozen dataclass):
    → Tokens decodificados são lidos, nunca modificados.
    → Falha rapidamente se código tenta mutar payload.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class UserRole(str, Enum):
    USER  = "user"
    ADMIN = "admin"


class AuthErrorCode(str, Enum):
    INVALID_CREDENTIALS = "invalid_credentials"
    TOKEN_EXPIRED       = "token_expired"
    TOKEN_INVALID       = "token_invalid"
    USER_NOT_FOUND      = "user_not_found"
    EMAIL_ALREADY_EXISTS = "email_already_exists"
    INACTIVE_USER       = "inactive_user"
    INSUFFICIENT_PERMISSIONS = "insufficient_permissions"


# ── Exceções customizadas ─────────────────────────────────────────────────────

class AuthError(Exception):
    """Base para erros de autenticação/autorização."""
    def __init__(self, code: AuthErrorCode, message: str) -> None:
        super().__init__(message)
        self.code    = code
        self.message = message


class InvalidCredentialsError(AuthError):
    def __init__(self) -> None:
        super().__init__(AuthErrorCode.INVALID_CREDENTIALS, "Email ou senha inválidos.")


class TokenExpiredError(AuthError):
    def __init__(self) -> None:
        super().__init__(AuthErrorCode.TOKEN_EXPIRED, "Token expirado.")


class TokenInvalidError(AuthError):
    def __init__(self, detail: str = "") -> None:
        msg = f"Token inválido. {detail}".strip()
        super().__init__(AuthErrorCode.TOKEN_INVALID, msg)


class UserNotFoundError(AuthError):
    def __init__(self, identifier: str = "") -> None:
        super().__init__(AuthErrorCode.USER_NOT_FOUND, f"Usuário não encontrado: {identifier}")


class EmailAlreadyExistsError(AuthError):
    def __init__(self, email: str) -> None:
        super().__init__(AuthErrorCode.EMAIL_ALREADY_EXISTS, f"Email já cadastrado: {email}")


class InactiveUserError(AuthError):
    def __init__(self) -> None:
        super().__init__(AuthErrorCode.INACTIVE_USER, "Conta desativada.")


class InsufficientPermissionsError(AuthError):
    def __init__(self) -> None:
        super().__init__(AuthErrorCode.INSUFFICIENT_PERMISSIONS, "Permissão insuficiente.")


# ── Entidades ─────────────────────────────────────────────────────────────────

@dataclass
class User:
    """Usuário autenticado do sistema."""
    user_id:        str
    email:          str
    hashed_password: str
    full_name:      str
    role:           UserRole       = UserRole.USER
    is_active:      bool           = True
    created_at:     Optional[datetime] = None
    last_login_at:  Optional[datetime] = None

    @staticmethod
    def new(email: str, hashed_password: str, full_name: str,
            role: UserRole = UserRole.USER) -> "User":
        """Factory: cria novo usuário com UUID gerado no domínio."""
        return User(
            user_id         = str(uuid.uuid4()),
            email           = email.lower().strip(),
            hashed_password = hashed_password,
            full_name       = full_name.strip(),
            role            = role,
            is_active       = True,
        )

    def ensure_active(self) -> None:
        if not self.is_active:
            raise InactiveUserError()

    def ensure_admin(self) -> None:
        if self.role != UserRole.ADMIN:
            raise InsufficientPermissionsError()


@dataclass(frozen=True)
class TokenPayload:
    """Payload decodificado de um JWT. Imutável por design."""
    sub:      str        # user_id
    email:    str
    role:     str
    exp:      int        # Unix timestamp de expiração
    token_type: str      # "access" | "refresh"
    jti:      str = ""   # JWT ID — para revogação futura


@dataclass
class UserRegistration:
    """DTO de entrada para registro de novo usuário."""
    email:     str
    password:  str        # texto puro — descartado após hash
    full_name: str

    def __post_init__(self) -> None:
        self.email     = self.email.lower().strip()
        self.full_name = self.full_name.strip()
        if len(self.password) < 8:
            raise ValueError("Senha deve ter no mínimo 8 caracteres.")
        if not self.email or "@" not in self.email:
            raise ValueError("Email inválido.")
        if not self.full_name:
            raise ValueError("Nome completo obrigatório.")


@dataclass
class TokenPair:
    """Par de tokens retornado no login."""
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    expires_in:    int = 1800   # segundos até expirar o access token
