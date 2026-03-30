"""
Fakes (test doubles) para os ports da camada de aplicacao.

Decisao: Fakes em vez de Mocks (unittest.mock.MagicMock).
Motivo:
- Fakes sao classes reais que implementam o contrato — capturam erros de interface
- Mocks nao verificam se o contrato (Protocol) esta sendo satisfeito
- Fakes sao mais legíveis em testes — o estado pode ser inspecionado diretamente
- Fakes permitem simular comportamentos complexos (ex: falha na 2a chamada)

Trade-off: fakes requerem mais codigo inicial. Vale para ports que sao
usados em muitos testes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from finanalytics_ai.domain.events.models import DomainEvent, EventStatus, ProcessingResult

if TYPE_CHECKING:
    import uuid


class FakeEventRepository:
    def __init__(self) -> None:
        self.store: dict[uuid.UUID, DomainEvent] = {}
        self.upsert_calls: list[DomainEvent] = []
        self._fail_on_upsert: Exception | None = None

    def set_fail_on_upsert(self, exc: Exception) -> None:
        self._fail_on_upsert = exc

    async def upsert(self, event: DomainEvent) -> None:
        if self._fail_on_upsert:
            raise self._fail_on_upsert
        self.store[event.event_id] = event
        self.upsert_calls.append(event)

    async def find_by_id(self, event_id: uuid.UUID) -> DomainEvent | None:
        return self.store.get(event_id)

    async def find_by_status(
        self, status: EventStatus, *, limit: int = 100
    ) -> list[DomainEvent]:
        return [e for e in self.store.values() if e.status == status][:limit]


class FakeIdempotencyStore:
    def __init__(self) -> None:
        self._store: dict[str, bool] = {}
        self.check_and_set_calls: list[str] = []
        self.release_calls: list[str] = []

    def mark_as_processed(self, key: str) -> None:
        """Helper de teste: pre-popula a store para simular evento ja processado."""
        self._store[key] = True

    async def check_and_set(self, key: str, ttl_seconds: int) -> bool:
        self.check_and_set_calls.append(key)
        if key in self._store:
            return True
        self._store[key] = True
        return False

    async def release(self, key: str) -> None:
        self.release_calls.append(key)
        self._store.pop(key, None)


class FakeObservability:
    def __init__(self) -> None:
        self.processing_times: list[tuple[str, float]] = []
        self.statuses: list[tuple[str, str]] = []
        self.retries: list[tuple[str, int]] = []

    def record_processing_time(self, event_type: str, duration_ms: float) -> None:
        self.processing_times.append((event_type, duration_ms))

    def record_event_status(self, event_type: str, status: str) -> None:
        self.statuses.append((event_type, status))

    def record_retry(self, event_type: str, retry_count: int) -> None:
        self.retries.append((event_type, retry_count))


class SuccessRule:
    """Regra que sempre passa."""

    name = "always_success"

    def applies_to(self, event: DomainEvent) -> bool:
        return True

    async def apply(self, event: DomainEvent) -> ProcessingResult:
        return ProcessingResult.success(event.event_id, {"rule": self.name})


class FailureRule:
    """Regra que sempre falha (erro de negocio, nao excecao)."""

    name = "always_failure"

    def __init__(self, message: str = "rule failed") -> None:
        self.message = message

    def applies_to(self, event: DomainEvent) -> bool:
        return True

    async def apply(self, event: DomainEvent) -> ProcessingResult:
        return ProcessingResult.failure(event.event_id, self.message)


class ExplodingRule:
    """Regra que levanta excecao (simula erro de infraestrutura)."""

    name = "exploding_rule"

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def applies_to(self, event: DomainEvent) -> bool:
        return True

    async def apply(self, event: DomainEvent) -> ProcessingResult:
        raise self.exc

