"""
Modelos ORM da camada de infraestrutura.

NOTA: este modulo NAO usa 'from __future__ import annotations'.
SQLAlchemy 2.x resolve Mapped[X] via eval() em runtime -- as anotacoes
precisam ser strings reais resoluveis no namespace do modulo.
Com PEP 563 (from __future__ import annotations), tudo vira string e
o SQLAlchemy nao consegue resolver 'datetime', 'uuid.UUID', etc.
"""

from datetime import datetime
from typing import Any
import uuid

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class EventRecord(Base):
    """
    Representacao persistida de um DomainEvent.

    Indices:
    - PK: event_id (UUID gerado pelo dominio)
    - idx_status: buscas por eventos pendentes/falhados
    - idx_event_type: metricas e filtros
    - idx_created_at: paginacao e limpeza

    JSONB para payload.data: eventos sao heterogeneos por natureza.
    """

    __tablename__ = "event_records"

    event_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(256), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    payload_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_event_records_status", "status"),
        Index("idx_event_records_event_type", "event_type"),
        Index("idx_event_records_created_at", "created_at"),
        Index("idx_event_records_source_type", "source", "event_type"),
    )
