"""
finanalytics_ai.infrastructure.database.repositories.admin_repo
Admin repository: financial_agents ORM + CRUD
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import Boolean, DateTime, String, Text, func, select
from sqlalchemy.orm import Mapped, mapped_column
import structlog

from finanalytics_ai.infrastructure.database.connection import Base

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


class FinancialAgentModel(Base):
    __tablename__ = "financial_agents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    agent_type: Mapped[str] = mapped_column(String(30), nullable=False, default="corretora")
    country: Mapped[str] = mapped_column(String(3), nullable=False, default="BRA")
    website: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FinancialAgentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def _row(self, m: FinancialAgentModel) -> dict:
        return {
            "id": m.id,
            "name": m.name,
            "code": m.code,
            "agent_type": m.agent_type,
            "country": m.country,
            "website": m.website,
            "is_active": m.is_active,
            "note": m.note,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }

    async def list_all(self) -> list[dict]:
        res = await self._s.execute(select(FinancialAgentModel).order_by(FinancialAgentModel.name))
        return [self._row(m) for m in res.scalars().all()]

    async def get(self, agent_id: str) -> dict | None:
        res = await self._s.execute(
            select(FinancialAgentModel).where(FinancialAgentModel.id == agent_id)
        )
        m = res.scalar_one_or_none()
        return self._row(m) if m else None

    async def create(self, data: dict) -> dict:
        m = FinancialAgentModel(
            id=str(uuid.uuid4()),
            name=data["name"],
            code=data.get("code"),
            agent_type=data.get("agent_type", "corretora"),
            country=data.get("country", "BRA"),
            website=data.get("website"),
            is_active=data.get("is_active", True),
            note=data.get("note"),
        )
        self._s.add(m)
        await self._s.flush()
        logger.info("financial_agent.created", id=m.id, name=m.name)
        return self._row(m)

    async def update(self, agent_id: str, data: dict) -> dict | None:
        res = await self._s.execute(
            select(FinancialAgentModel).where(FinancialAgentModel.id == agent_id)
        )
        m = res.scalar_one_or_none()
        if not m:
            return None
        for k, v in data.items():
            if hasattr(m, k) and v is not None:
                setattr(m, k, v)
        m.updated_at = datetime.now(UTC)
        await self._s.flush()
        return self._row(m)

    async def delete(self, agent_id: str) -> bool:
        res = await self._s.execute(
            select(FinancialAgentModel).where(FinancialAgentModel.id == agent_id)
        )
        m = res.scalar_one_or_none()
        if not m:
            return False
        await self._s.delete(m)
        await self._s.flush()
        return True
