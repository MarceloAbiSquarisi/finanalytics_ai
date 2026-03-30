"""
Entidades e agregados do dominio de eventos.

Decisao arquitetural:
- DomainEvent eh uma entidade (tem identidade via event_id)
- EventPayload eh um value object (sem identidade propria)
- ProcessingResult eh um value object (resultado imutavel)

Usamos dataclasses em vez de Pydantic no dominio por principio:
o dominio nao deve saber nada sobre serializacao. Pydantic entra
na borda (HTTP, Kafka deserialization) — veja infrastructure/event_processor.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from finanalytics_ai.domain.events.value_objects import CorrelationId, EventType


class EventStatus(StrEnum):
    """
    Status como str Enum para facilitar persistencia sem conversao adicional.
    SKIPPED: evento recebido novamente — idempotencia ativada.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    DEAD_LETTER = "dead_letter"  # esgotou retries


@dataclass(frozen=True)
class EventPayload:
    """
    Dados brutos do evento. Frozen para garantir que regras de negocio
    nao alterem o payload original — fundamental para auditoria e replay.
    """

    event_type: EventType
    data: dict[str, Any]
    source: str
    correlation_id: CorrelationId | None = None

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("EventPayload.source nao pode ser vazio")


@dataclass
class DomainEvent:
    """
    Entidade central do agregado.

    Nao frozen: o status muda ao longo do ciclo de vida.
    retry_count eh mutavel por design — o servico precisa incrementa-lo
    sem criar nova instancia (evita overhead desnecessario).

    Invariante: created_at sempre em UTC, nunca None apos criacao.
    """

    event_id: uuid.UUID
    payload: EventPayload
    status: EventStatus
    created_at: datetime
    processed_at: datetime | None = None
    error_message: str | None = None
    retry_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, payload: EventPayload) -> DomainEvent:
        """Factory method — unico ponto de criacao garantindo invariantes."""
        return cls(
            event_id=uuid.uuid4(),
            payload=payload,
            status=EventStatus.PENDING,
            created_at=datetime.now(tz=UTC),
        )

    def mark_processing(self) -> None:
        if self.status not in (EventStatus.PENDING, EventStatus.FAILED):
            raise ValueError(
                f"Transicao invalida: {self.status} -> PROCESSING. "
                "Apenas PENDING e FAILED podem ir para PROCESSING."
            )
        self.status = EventStatus.PROCESSING

    def mark_completed(self, output: dict[str, Any] | None = None) -> None:
        self.status = EventStatus.COMPLETED
        self.processed_at = datetime.now(tz=UTC)
        if output:
            self.metadata["output"] = output

    def mark_failed(self, error: str, *, increment_retry: bool = True) -> None:
        self.status = EventStatus.FAILED
        self.error_message = error
        if increment_retry:
            self.retry_count += 1

    def mark_dead_letter(self, reason: str) -> None:
        self.status = EventStatus.DEAD_LETTER
        self.error_message = reason

    @property
    def is_retriable(self) -> bool:
        return self.status == EventStatus.FAILED

    @property
    def idempotency_key(self) -> str:
        return str(self.event_id)


@dataclass(frozen=True)
class ProcessingResult:
    """
    Resultado imutavel do processamento — retornado pelo servico.
    Separado da entidade para evitar acoplamento entre camadas:
    a infraestrutura recebe o Result, nao a entidade completa.
    """

    event_id: uuid.UUID
    status: EventStatus
    output: dict[str, Any] | None = None
    error: str | None = None

    @classmethod
    def success(
        cls, event_id: uuid.UUID, output: dict[str, Any] | None = None
    ) -> ProcessingResult:
        return cls(event_id=event_id, status=EventStatus.COMPLETED, output=output)

    @classmethod
    def failure(cls, event_id: uuid.UUID, error: str) -> ProcessingResult:
        return cls(event_id=event_id, status=EventStatus.FAILED, error=error)

    @classmethod
    def skipped(cls, event_id: uuid.UUID) -> ProcessingResult:
        return cls(event_id=event_id, status=EventStatus.SKIPPED)
