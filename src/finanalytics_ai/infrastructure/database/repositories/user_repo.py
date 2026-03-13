"""
finanalytics_ai.infrastructure.database.repositories.user_repo
──────────────────────────────────────────────────────────────
Modelo SQLAlchemy e repositório assíncrono para User.

Design decisions:
  email como UNIQUE index:
    Login por email é o caso de uso padrão.
    Index separado acelera lookup sem precisar de query full scan.

  hashed_password na tabela users:
    Alternativa seria tabela separada credentials.
    Preferimos simplicidade — uma tabela, uma query para login.

  last_login_at atualizado no repositório (não no domínio):
    Efeito colateral de infraestrutura — o domínio não precisa saber disso.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import Boolean, DateTime, String, func, select
from sqlalchemy.orm import Mapped, mapped_column

from finanalytics_ai.domain.auth.entities import User, UserRole
from finanalytics_ai.infrastructure.database.connection import Base

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


# ── ORM Model ─────────────────────────────────────────────────────────────────


class UserModel(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=UserRole.USER.value)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ── Repository ────────────────────────────────────────────────────────────────


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, user: User) -> User:
        """Persiste novo usuário. Lança IntegrityError se email duplicado."""
        model = UserModel(
            user_id=user.user_id,
            email=user.email,
            hashed_password=user.hashed_password,
            full_name=user.full_name,
            role=user.role.value,
            is_active=user.is_active,
        )
        self._session.add(model)
        await self._session.flush()
        logger.info("user.created", user_id=user.user_id, email=user.email)
        return self._to_domain(model)

    async def find_by_email(self, email: str) -> User | None:
        stmt = select(UserModel).where(UserModel.email == email.lower().strip())
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def find_by_id(self, user_id: str) -> User | None:
        stmt = select(UserModel).where(UserModel.user_id == user_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def update_last_login(self, user_id: str) -> None:
        stmt = select(UserModel).where(UserModel.user_id == user_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model:
            model.last_login_at = datetime.now(UTC)
            await self._session.flush()

    async def email_exists(self, email: str) -> bool:
        stmt = select(UserModel.user_id).where(UserModel.email == email.lower().strip())
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def count(self) -> int:
        stmt = select(func.count()).select_from(UserModel)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    @staticmethod
    def _to_domain(m: UserModel) -> User:
        return User(
            user_id=m.user_id,
            email=m.email,
            hashed_password=m.hashed_password,
            full_name=m.full_name,
            role=UserRole(m.role),
            is_active=m.is_active,
            created_at=m.created_at,
            last_login_at=m.last_login_at,
        )
