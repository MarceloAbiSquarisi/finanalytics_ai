"""
tests/unit/domain/test_auth.py
────────────────────────────────
Testes unitários para o domínio de autenticação.
Sem I/O: sem banco, sem rede, sem arquivos.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../src"))

from finanalytics_ai.domain.auth.entities import (
    AuthErrorCode,
    EmailAlreadyExistsError,
    InactiveUserError,
    InsufficientPermissionsError,
    InvalidCredentialsError,
    TokenExpiredError,
    TokenInvalidError,
    TokenPayload,
    User,
    UserNotFoundError,
    UserRegistration,
    UserRole,
)

# ── User entity ───────────────────────────────────────────────────────────────


class TestUser:
    def test_new_user_generates_uuid(self):
        u = User.new("test@example.com", "hash", "Test User")
        assert len(u.user_id) == 36
        assert "-" in u.user_id

    def test_new_user_two_calls_different_ids(self):
        u1 = User.new("a@b.com", "h", "A")
        u2 = User.new("a@b.com", "h", "A")
        assert u1.user_id != u2.user_id

    def test_email_lowercased(self):
        u = User.new("TEST@EXAMPLE.COM", "h", "Test")
        assert u.email == "test@example.com"

    def test_default_role_is_user(self):
        u = User.new("a@b.com", "h", "A")
        assert u.role == UserRole.USER

    def test_is_active_by_default(self):
        u = User.new("a@b.com", "h", "A")
        assert u.is_active is True

    def test_ensure_active_raises_for_inactive(self):
        u = User.new("a@b.com", "h", "A")
        u.is_active = False
        with pytest.raises(InactiveUserError):
            u.ensure_active()

    def test_ensure_active_passes_for_active(self):
        u = User.new("a@b.com", "h", "A")
        u.ensure_active()  # não deve lançar

    def test_ensure_admin_raises_for_user(self):
        u = User.new("a@b.com", "h", "A")
        with pytest.raises(InsufficientPermissionsError):
            u.ensure_admin()

    def test_ensure_admin_passes_for_admin(self):
        u = User.new("a@b.com", "h", "A", role=UserRole.ADMIN)
        u.ensure_admin()  # não deve lançar


# ── UserRegistration ──────────────────────────────────────────────────────────


class TestUserRegistration:
    def test_valid_registration(self):
        r = UserRegistration("test@example.com", "password123", "Test User")
        assert r.email == "test@example.com"

    def test_email_normalized(self):
        r = UserRegistration("  TEST@EXAMPLE.COM  ", "pass1234", "Name")
        assert r.email == "test@example.com"

    def test_short_password_raises(self):
        with pytest.raises(ValueError, match="8 caracteres"):
            UserRegistration("a@b.com", "short", "Name")

    def test_invalid_email_raises(self):
        with pytest.raises(ValueError, match="Email inválido"):
            UserRegistration("not-an-email", "password123", "Name")

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="Nome"):
            UserRegistration("a@b.com", "password123", "   ")

    def test_exact_8_chars_password_ok(self):
        r = UserRegistration("a@b.com", "12345678", "Name")
        assert r.password == "12345678"


# ── Auth Exceptions ───────────────────────────────────────────────────────────


class TestAuthExceptions:
    def test_invalid_credentials_code(self):
        e = InvalidCredentialsError()
        assert e.code == AuthErrorCode.INVALID_CREDENTIALS
        assert "inválidos" in e.message

    def test_token_expired_code(self):
        e = TokenExpiredError()
        assert e.code == AuthErrorCode.TOKEN_EXPIRED

    def test_token_invalid_code(self):
        e = TokenInvalidError("detalhe")
        assert e.code == AuthErrorCode.TOKEN_INVALID
        assert "detalhe" in e.message

    def test_email_already_exists(self):
        e = EmailAlreadyExistsError("a@b.com")
        assert e.code == AuthErrorCode.EMAIL_ALREADY_EXISTS
        assert "a@b.com" in e.message

    def test_user_not_found(self):
        e = UserNotFoundError("abc123")
        assert "abc123" in e.message

    def test_all_errors_are_auth_error_subclasses(self):
        from finanalytics_ai.domain.auth.entities import AuthError

        for cls in [
            InvalidCredentialsError,
            TokenExpiredError,
            TokenInvalidError,
            UserNotFoundError,
            EmailAlreadyExistsError,
            InactiveUserError,
            InsufficientPermissionsError,
        ]:
            assert issubclass(cls, AuthError)


# ── TokenPayload ──────────────────────────────────────────────────────────────


class TestTokenPayload:
    def test_frozen_immutable(self):
        p = TokenPayload(
            sub="u1", email="a@b.com", role="user", exp=9999999999, token_type="access"
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            p.sub = "other"  # type: ignore

    def test_access_type(self):
        p = TokenPayload(
            sub="u1", email="a@b.com", role="user", exp=9999999999, token_type="access"
        )
        assert p.token_type == "access"


# ── JWT Handler ───────────────────────────────────────────────────────────────


class TestJWTHandler:
    @pytest.fixture
    def handler(self):
        from finanalytics_ai.infrastructure.auth.jwt_handler import JWTHandler

        return JWTHandler(
            secret_key="test-secret-key-32-chars-minimum!",
            access_expire_minutes=30,
            refresh_expire_days=7,
        )

    @pytest.fixture
    def user(self):
        return User.new("jwt@test.com", "hashed", "JWT Tester")

    def test_create_access_token_is_string(self, handler, user):
        token = handler.create_access_token(user)
        assert isinstance(token, str)
        assert len(token) > 10

    def test_create_refresh_token_is_string(self, handler, user):
        token = handler.create_refresh_token(user)
        assert isinstance(token, str)

    def test_access_and_refresh_different(self, handler, user):
        at = handler.create_access_token(user)
        rt = handler.create_refresh_token(user)
        assert at != rt

    def test_decode_access_token(self, handler, user):
        token = handler.create_access_token(user)
        payload = handler.decode(token)
        assert payload.sub == user.user_id
        assert payload.email == user.email
        assert payload.token_type == "access"

    def test_decode_refresh_token(self, handler, user):
        token = handler.create_refresh_token(user)
        payload = handler.decode(token)
        assert payload.token_type == "refresh"

    def test_decode_refresh_via_method(self, handler, user):
        token = handler.create_refresh_token(user)
        payload = handler.decode_refresh(token)
        assert payload.sub == user.user_id

    def test_decode_refresh_rejects_access_token(self, handler, user):
        access_token = handler.create_access_token(user)
        with pytest.raises(TokenInvalidError):
            handler.decode_refresh(access_token)

    def test_invalid_token_raises(self, handler):
        with pytest.raises((TokenInvalidError, TokenExpiredError)):
            handler.decode("not.a.valid.token")

    def test_tampered_token_raises(self, handler, user):
        token = handler.create_access_token(user)
        tampered = token[:-5] + "XXXXX"
        with pytest.raises((TokenInvalidError, TokenExpiredError)):
            handler.decode(tampered)

    def test_wrong_secret_raises(self, handler, user):
        from finanalytics_ai.infrastructure.auth.jwt_handler import JWTHandler

        other = JWTHandler(secret_key="completely-different-secret-key!")
        token = handler.create_access_token(user)
        with pytest.raises((TokenInvalidError, TokenExpiredError)):
            other.decode(token)

    def test_create_token_pair(self, handler, user):
        pair = handler.create_token_pair(user)
        assert pair.access_token
        assert pair.refresh_token
        assert pair.access_token != pair.refresh_token
        assert pair.expires_in == 30 * 60
        assert pair.token_type == "bearer"

    def test_expired_token_raises(self, handler, user):
        # Constrói token com exp 1 hora no passado — sem sleep, sem flakiness
        from finanalytics_ai.infrastructure.auth.jwt_handler import _BACKEND

        past_exp = int(time.time()) - 3600  # 1h atrás, inequivocamente expirado

        if _BACKEND == "jose":
            from jose import jwt as _jose_jwt

            payload = {
                "sub": user.user_id,
                "email": user.email,
                "role": "user",
                "exp": past_exp,
                "token_type": "access",
                "jti": "test-expired",
            }
            token = _jose_jwt.encode(payload, handler.secret_key, algorithm=handler.algorithm)
        elif _BACKEND == "pyjwt":
            import jwt as _pyjwt

            payload = {
                "sub": user.user_id,
                "email": user.email,
                "role": "user",
                "exp": past_exp,
                "token_type": "access",
                "jti": "test-expired",
            }
            token = _pyjwt.encode(payload, handler.secret_key, algorithm=handler.algorithm)
        else:
            pytest.skip("Nenhum backend JWT disponível")

        with pytest.raises((TokenExpiredError, TokenInvalidError)):
            handler.decode(token)

    def test_payload_has_jti(self, handler, user):
        token = handler.create_access_token(user)
        payload = handler.decode(token)
        # JTI pode ser vazio no fallback, mas não deve ser None
        assert payload.jti is not None

    def test_role_preserved_in_token(self, handler):
        admin = User.new("admin@test.com", "h", "Admin", role=UserRole.ADMIN)
        token = handler.create_access_token(admin)
        payload = handler.decode(token)
        assert payload.role == "admin"


# ── Password Hasher ───────────────────────────────────────────────────────────


class TestPasswordHasher:
    @pytest.fixture
    def hasher(self):
        from finanalytics_ai.infrastructure.auth.password_hasher import PasswordHasher

        return PasswordHasher(rounds=4)  # rounds baixo para testes rápidos

    def test_hash_is_string(self, hasher):
        h = hasher.hash("my_password")
        assert isinstance(h, str)

    def test_hash_not_equal_to_plain(self, hasher):
        h = hasher.hash("my_password")
        assert h != "my_password"

    def test_verify_correct_password(self, hasher):
        h = hasher.hash("correct_password")
        assert hasher.verify("correct_password", h) is True

    def test_verify_wrong_password(self, hasher):
        h = hasher.hash("correct_password")
        assert hasher.verify("wrong_password", h) is False

    def test_same_password_different_hashes(self, hasher):
        h1 = hasher.hash("same_password")
        h2 = hasher.hash("same_password")
        # bcrypt gera salt aleatório — hashes devem ser diferentes
        assert h1 != h2

    def test_both_hashes_verify_same_password(self, hasher):
        h1 = hasher.hash("same_password")
        h2 = hasher.hash("same_password")
        assert hasher.verify("same_password", h1)
        assert hasher.verify("same_password", h2)

    def test_empty_password(self, hasher):
        h = hasher.hash("")
        assert hasher.verify("", h)
        assert not hasher.verify("not_empty", h)
