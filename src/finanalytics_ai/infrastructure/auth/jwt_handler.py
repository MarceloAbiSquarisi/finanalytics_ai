"""
finanalytics_ai.infrastructure.auth.jwt_handler
─────────────────────────────────────────────────
Criação e validação de JWTs com python-jose ou PyJWT.

Algoritmo: HS256 (simétrico) com APP_SECRET_KEY.
  Trade-off: RS256 (assimétrico) seria melhor para arquitetura
  multi-serviço (chave pública pública). Para monolito/monorepo
  atual, HS256 é suficiente e operacionalmente mais simples.

Dois tipos de token:
  access:  30min — enviado no header Authorization: Bearer <token>
  refresh: 7 dias — enviado no body do /auth/refresh

JTI (JWT ID):
  UUID único por token — base para revogação futura (Redis blacklist).
  Não implementado nesta sprint, mas estrutura está pronta.

Design decision — não usar cookies httpOnly por padrão:
  O frontend atual é HTML puro sem SSR. Cookies httpOnly exigem
  que o servidor defina Set-Cookie, o que funciona bem. Mas para
  facilitar uso da API via curl/Swagger, optamos por Bearer token.
  Cookies httpOnly podem ser adicionados como camada extra depois.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from finanalytics_ai.domain.auth.entities import (
    TokenExpiredError,
    TokenInvalidError,
    TokenPair,
    TokenPayload,
    User,
)

logger = structlog.get_logger(__name__)

# ── Tentar importar python-jose (preferido) ou PyJWT (fallback) ───────────────
try:
    from jose import jwt as jose_jwt

    _BACKEND = "jose"
except ImportError:
    try:
        import jwt as pyjwt

        _BACKEND = "pyjwt"
    except ImportError:
        _BACKEND = "none"


class JWTHandler:
    """Cria e valida JWTs de acesso e refresh."""

    def __init__(
        self,
        secret_key: str,
        algorithm: str = "HS256",
        access_expire_minutes: int = 30,
        refresh_expire_days: int = 7,
    ) -> None:
        self.secret_key = secret_key
        self.algorithm = algorithm
        self.access_expire_minutes = access_expire_minutes
        self.refresh_expire_days = refresh_expire_days

    def create_access_token(self, user: User) -> str:
        return self._create_token(user, "access", timedelta(minutes=self.access_expire_minutes))

    def create_refresh_token(self, user: User) -> str:
        return self._create_token(user, "refresh", timedelta(days=self.refresh_expire_days))

    def create_token_pair(self, user: User) -> TokenPair:
        return TokenPair(
            access_token=self.create_access_token(user),
            refresh_token=self.create_refresh_token(user),
            expires_in=self.access_expire_minutes * 60,
        )

    def decode(self, token: str) -> TokenPayload:
        """
        Decodifica e valida token. Lança TokenExpiredError ou TokenInvalidError.
        """
        if _BACKEND == "none":
            return self._decode_fallback(token)

        try:
            if _BACKEND == "jose":
                payload = jose_jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            else:  # pyjwt
                payload = pyjwt.decode(token, self.secret_key, algorithms=[self.algorithm])

            return TokenPayload(
                sub=payload["sub"],
                email=payload["email"],
                role=payload.get("role", "user"),
                exp=payload["exp"],
                token_type=payload.get("token_type", "access"),
                jti=payload.get("jti", ""),
            )

        except Exception as exc:
            exc_name = type(exc).__name__
            if "Expired" in exc_name or "expired" in str(exc).lower():
                logger.warning("jwt.token_expired")
                raise TokenExpiredError()
            logger.warning("jwt.token_invalid", error=str(exc))
            raise TokenInvalidError(str(exc))

    def decode_refresh(self, token: str) -> TokenPayload:
        """Decodifica refresh token e valida que é do tipo correto."""
        payload = self.decode(token)
        if payload.token_type != "refresh":
            raise TokenInvalidError("Esperado refresh token.")
        return payload

    # ── Internals ─────────────────────────────────────────────────────────────

    def _create_token(self, user: User, token_type: str, delta: timedelta) -> str:
        now = datetime.now(UTC)
        claims: dict[str, Any] = {
            "sub": user.user_id,
            "email": user.email,
            "role": user.role.value,
            "token_type": token_type,
            "jti": str(uuid.uuid4()),
            "iat": int(now.timestamp()),
            "exp": int((now + delta).timestamp()),
        }
        if _BACKEND == "jose":
            return jose_jwt.encode(claims, self.secret_key, algorithm=self.algorithm)
        if _BACKEND == "pyjwt":
            return pyjwt.encode(claims, self.secret_key, algorithm=self.algorithm)
        return self._encode_fallback(claims)

    # ── Fallback sem biblioteca JWT (apenas dev/testes) ───────────────────────

    def _encode_fallback(self, claims: dict[str, Any]) -> str:
        """HMAC-SHA256 fallback — funcional para testes sem python-jose/PyJWT."""
        import base64
        import hashlib
        import hmac as _hmac
        import json

        header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode()
        payload = (
            base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode())
            .rstrip(b"=")
            .decode()
        )
        msg = f"{header}.{payload}".encode()
        sig = _hmac.new(self.secret_key.encode(), msg, hashlib.sha256).digest()
        sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
        return f"{header}.{payload}.{sig_b64}"

    def _decode_fallback(self, token: str) -> TokenPayload:
        import base64
        import hashlib
        import hmac as _hmac
        import json
        import time

        try:
            parts = token.split(".")
            if len(parts) != 3:
                raise TokenInvalidError("Formato inválido.")
            header_b64, payload_b64, sig_b64 = parts
            # Verifica assinatura HMAC-SHA256
            msg = f"{header_b64}.{payload_b64}".encode()
            expected = _hmac.new(self.secret_key.encode(), msg, hashlib.sha256).digest()
            expected_b64 = base64.urlsafe_b64encode(expected).rstrip(b"=").decode()
            if not _hmac.compare_digest(sig_b64, expected_b64):
                raise TokenInvalidError("Assinatura inválida.")
            pad = payload_b64 + "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(pad))
            if payload.get("exp", 0) < time.time():
                raise TokenExpiredError()
            return TokenPayload(
                sub=payload["sub"],
                email=payload["email"],
                role=payload.get("role", "user"),
                exp=payload["exp"],
                token_type=payload.get("token_type", "access"),
                jti=payload.get("jti", ""),
            )
        except (TokenExpiredError, TokenInvalidError):
            raise
        except Exception as exc:
            raise TokenInvalidError(str(exc))


# ── Singleton ─────────────────────────────────────────────────────────────────
_handler: JWTHandler | None = None


def get_jwt_handler() -> JWTHandler:
    global _handler
    if _handler is None:
        from finanalytics_ai.config import get_settings

        s = get_settings()
        _handler = JWTHandler(
            secret_key=s.app_secret_key,
            access_expire_minutes=getattr(s, "jwt_access_expire_minutes", 30),
            refresh_expire_days=getattr(s, "jwt_refresh_expire_days", 7),
        )
    return _handler
