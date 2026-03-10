"""
finanalytics_ai.infrastructure.auth.password_hasher
──────────────────────────────────────────────────────
Hashing de senhas com bcrypt via passlib.

Estratégia SHA-256 + bcrypt (pepper-hash):
  1. SHA-256(senha) → hex de 64 chars fixos
  2. bcrypt(hex)    → hash final armazenado no banco

  Isso resolve o limite de 72 bytes do bcrypt de forma segura:
  qualquer senha — curta ou longa — vira sempre 64 chars antes
  do bcrypt. Não há truncamento silencioso nem erro de tamanho.
  Segurança mantida: SHA-256 é pré-imagem resistente.
"""

from __future__ import annotations

import hashlib
import hmac
import os

try:
    from passlib.context import CryptContext

    _PASSLIB_AVAILABLE = True
except ImportError:
    _PASSLIB_AVAILABLE = False


def _sha256_hex(password: str) -> str:
    """Converte senha em hex SHA-256 de 64 chars — elimina limite de 72 bytes do bcrypt."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


class PasswordHasher:
    """Abstração para hashing e verificação de senhas."""

    def __init__(self, rounds: int = 12) -> None:
        if _PASSLIB_AVAILABLE:
            self._ctx = CryptContext(
                schemes=["bcrypt"],
                deprecated="auto",
                bcrypt__rounds=rounds,
            )
            self._use_passlib = True
        else:
            self._use_passlib = False

    def hash(self, plain_password: str) -> str:
        """Retorna hash bcrypt da senha para armazenar no banco."""
        prepared = _sha256_hex(plain_password)
        if self._use_passlib:
            return self._ctx.hash(prepared)
        salt = os.urandom(16).hex()
        digest = hmac.new(salt.encode(), prepared.encode(), hashlib.sha256).hexdigest()
        return f"sha256${salt}${digest}"

    def verify(self, plain_password: str, hashed_password: str) -> bool:
        """Verifica senha em tempo constante."""
        prepared = _sha256_hex(plain_password)
        if self._use_passlib:
            return self._ctx.verify(prepared, hashed_password)
        parts = hashed_password.split("$")
        if len(parts) != 3:
            return False
        _, salt, stored_digest = parts
        digest = hmac.new(salt.encode(), prepared.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(digest, stored_digest)

    def needs_rehash(self, hashed_password: str) -> bool:
        if self._use_passlib:
            return self._ctx.needs_update(hashed_password)
        return False


_hasher: PasswordHasher | None = None


def get_password_hasher(rounds: int = 12) -> PasswordHasher:
    global _hasher
    if _hasher is None:
        _hasher = PasswordHasher(rounds=rounds)
    return _hasher
