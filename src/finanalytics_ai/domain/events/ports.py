"""
Domain ports — contratos via Protocol (duck typing estático).

Por que Protocol e não ABC?
- ABC força herança; Protocol usa structural subtyping (PEP 544).
- Infra implementa sem importar nada do domínio → dependência apenas para dentro.
- mypy valida em tempo de type-check sem overhead de runtime.

Regra: nenhum arquivo de infra é importado aqui.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from finanalytics_ai.domain.events.entities import (
    Event,
    EventId,
    EventProcessingRecord,
    EventType,
)


# ──────────────────────────────────────────────────────────────────────────────
# Storage port
# ──────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class EventRepository(Protocol):
    """Contrato de persistência de eventos.

    runtime_checkable permite isinstance() em testes sem implementar a classe
    concreta — útil para validar mocks.
    """

    async def save_event(self, event: Event) -> None:
        """Persiste o evento (idempotente por event.id)."""
        ...

    async def get_processing_record(
        self, event_id: EventId
    ) -> EventProcessingRecord | None:
        """Retorna o registro de processamento ou None se não existir."""
        ...

    async def upsert_processing_record(self, record: EventProcessingRecord) -> None:
        """Cria ou atualiza o registro de processamento (ON CONFLICT UPDATE)."""
        ...

    async def get_pending_events(
        self, event_type: EventType | None = None, limit: int = 100
    ) -> list[Event]:
        """Lista eventos pendentes de processamento, opcionalmente filtrado por tipo."""
        ...


# ──────────────────────────────────────────────────────────────────────────────
# Business rule port
# ──────────────────────────────────────────────────────────────────────────────


class BusinessRule(Protocol):
    """Contrato para regras de negócio plugáveis.

    Cada regra é responsável por um tipo de evento. O processador injeta
    todas as regras registradas e despacha para a correta.

    Alternativa considerada: usar um dicionário EventType → callable.
    Preferimos Protocol porque é mais expressivo, testável e permite que
    a regra carregue seu próprio estado (ex: thresholds configuráveis).
    """

    @property
    def handles(self) -> frozenset[EventType]:
        """Tipos de evento que esta regra processa."""
        ...

    async def apply(
        self, event: Event
    ) -> dict[str, Any]:
        """Aplica a regra e retorna metadados do resultado.

        Deve lançar BusinessRuleError em caso de violação de regra (não infra).
        Erros de infra devem propagar normalmente para acionar retry.
        """
        ...


# ──────────────────────────────────────────────────────────────────────────────
# Observability port
# ──────────────────────────────────────────────────────────────────────────────


class ObservabilityPort(Protocol):
    """Hook de observabilidade injetado no processador.

    Permite trocar Prometheus por OpenTelemetry, Datadog, etc.
    sem alterar o código de aplicação.
    """

    def record_event_processed(self, event_type: str, status: str) -> None: ...
    def record_processing_duration(self, event_type: str, duration_s: float) -> None: ...
    def record_retry(self, event_type: str, attempt: int) -> None: ...
