"""
Domain layer — Event entities.

Regras:
- Zero dependências de infra aqui (sem SQLAlchemy, sem aiohttp).
- Imutabilidade via frozen=True onde faz sentido.
- Erros de domínio lançados como DomainError (nunca deixar ValueError vazar).

Decisão arquitetural:
    Usamos dataclasses (frozen) para entidades de valor puro e Pydantic v2
    para entidades que chegam da borda do sistema (deserialização de JSON/Parquet).
    A separação evita poluir o modelo de domínio com lógica de serialização.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
import uuid

# ──────────────────────────────────────────────────────────────────────────────
# Value objects / Enums
# ──────────────────────────────────────────────────────────────────────────────


class EventStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class EventType(StrEnum):
    """Tipos de eventos suportados pelo pipeline.

    Novo tipo → adicione aqui + implemente BusinessRule correspondente.
    Sem magic strings espalhadas no código.
    """

    FINTZ_SYNC_COMPLETED = "fintz.sync.completed"
    FINTZ_SYNC_FAILED = "fintz.sync.failed"
    PORTFOLIO_REBALANCE = "portfolio.rebalance"
    ALERT_TRIGGERED = "alert.triggered"


# ──────────────────────────────────────────────────────────────────────────────
# Core entity
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EventId:
    """Wrapper tipado para ID de evento.

    Trade-off: overhead vs. type-safety.
    Preferimos o overhead mínimo para evitar bugs de 'passou str onde esperava UUID'.
    """

    value: uuid.UUID

    @classmethod
    def new(cls) -> EventId:
        return cls(value=uuid.uuid4())

    @classmethod
    def from_str(cls, raw: str) -> EventId:
        try:
            return cls(value=uuid.UUID(raw))
        except ValueError as exc:
            raise InvalidEventIdError(f"EventId inválido: {raw!r}") from exc

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True)
class Event:
    """Entidade central do domínio.

    frozen=True garante imutabilidade após criação — eventos não mudam,
    o que criamos são *novos* eventos de status.

    Payload é Any proposital: o domínio não valida estrutura interna do payload
    (responsabilidade do parser de cada EventType).
    """

    id: EventId
    event_type: EventType
    payload: dict[str, Any]
    source: str
    created_at: datetime
    correlation_id: str | None = None  # rastreamento entre sistemas

    @classmethod
    def create(
        cls,
        event_type: EventType,
        payload: dict[str, Any],
        source: str,
        correlation_id: str | None = None,
    ) -> Event:
        return cls(
            id=EventId.new(),
            event_type=event_type,
            payload=payload,
            source=source,
            created_at=datetime.now(tz=UTC),
            correlation_id=correlation_id,
        )


@dataclass
class EventProcessingRecord:
    """Registro mutável do processamento de um evento.

    Separado de Event porque tem ciclo de vida diferente:
    Event é imutável; ProcessingRecord evolui (PENDING → PROCESSING → COMPLETED).

    Esta separação também facilita o teste da máquina de estados de forma isolada.
    """

    event_id: EventId
    status: EventStatus
    attempt: int = 0
    last_error: str | None = None
    processed_at: datetime | None = None
    result_metadata: dict[str, Any] = field(default_factory=dict)

    def mark_processing(self) -> None:
        if self.status not in (EventStatus.PENDING, EventStatus.FAILED):
            raise InvalidStatusTransitionError(
                f"Não é possível transitar de {self.status} para PROCESSING"
            )
        self.status = EventStatus.PROCESSING
        self.attempt += 1

    def mark_completed(self, metadata: dict[str, Any] | None = None) -> None:
        self.status = EventStatus.COMPLETED
        self.processed_at = datetime.now(tz=UTC)
        if metadata:
            self.result_metadata.update(metadata)

    def mark_failed(self, error: str, max_retries: int) -> None:
        self.last_error = error
        if self.attempt >= max_retries:
            self.status = EventStatus.DEAD_LETTER
        else:
            self.status = EventStatus.FAILED


# ──────────────────────────────────────────────────────────────────────────────
# Domain exceptions
# ──────────────────────────────────────────────────────────────────────────────


class DomainError(Exception):
    """Base para todos os erros de domínio.

    Decisão: hierarquia própria evita capturar exceções de infra (IOError, etc.)
    acidentalmente. Em code reviews: nunca faça `except Exception` no domínio.
    """

    pass


class InvalidEventIdError(DomainError):
    pass


class InvalidStatusTransitionError(DomainError):
    pass


class EventValidationError(DomainError):
    """Payload não corresponde ao EventType declarado."""

    pass
