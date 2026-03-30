"""
Testes unitarios do EventProcessorService.

Cobertura alvo: todos os caminhos do fluxo de processamento.
- Happy path: evento processado com sucesso
- Idempotencia: evento duplicado ignorado
- Regra falha: evento marcado como FAILED
- Erro transitorio: idempotencia liberada para retry
- Erro permanente: vai para dead-letter, sem release de idempotencia
- Max retries: dead-letter apos esgotar tentativas
"""
from __future__ import annotations

import pytest

from finanalytics_ai.application.event_processor.config import EventProcessorConfig
from finanalytics_ai.application.event_processor.factory import create_event_processor_service
from finanalytics_ai.domain.events.exceptions import (
    MaxRetriesExceededError,
    PermanentError,
    TransientError,
)
from finanalytics_ai.domain.events.models import (
    DomainEvent,
    EventPayload,
    EventStatus,
    ProcessingResult,
)
from finanalytics_ai.domain.events.value_objects import EventType
from tests.unit.application.event_processor.fakes import (
    ExplodingRule,
    FailureRule,
    FakeEventRepository,
    FakeIdempotencyStore,
    FakeObservability,
    SuccessRule,
)


def make_event(event_type: str = "price.update") -> DomainEvent:
    return DomainEvent.create(
        EventPayload(
            event_type=EventType(event_type),
            data={"ticker": "PETR4", "price": 38.5},
            source="test",
        )
    )


def make_service(
    rules=None,
    max_retries: int = 3,
    repo: FakeEventRepository | None = None,
    idem: FakeIdempotencyStore | None = None,
) -> tuple:
    repo = repo or FakeEventRepository()
    idem = idem or FakeIdempotencyStore()
    obs = FakeObservability()
    # Fix: passa max_retries via config explicito para nao depender do .env
    config = EventProcessorConfig(
        max_retries=max_retries,
        concurrency=10,
        retry_base_delay=1.0,
        retry_max_delay=30.0,
    )
    svc = create_event_processor_service(
        repository=repo,
        idempotency_store=idem,
        rules=rules or [SuccessRule()],
        observability=obs,
        config=config,
    )
    return svc, repo, idem, obs


@pytest.mark.asyncio
class TestHappyPath:
    async def test_successful_processing(self) -> None:
        svc, repo, _idem, _obs = make_service()
        event = make_event()
        result = await svc.process(event)

        assert result.status == EventStatus.COMPLETED
        assert event.event_id in repo.store
        assert repo.store[event.event_id].status == EventStatus.COMPLETED

    async def test_observability_recorded(self) -> None:
        svc, _, _, obs = make_service()
        await svc.process(make_event())

        assert len(obs.processing_times) == 1
        assert obs.statuses[0][1] == "completed"

    async def test_upsert_called_twice(self) -> None:
        # Uma vez ao marcar PROCESSING, outra ao marcar COMPLETED
        svc, repo, _, _ = make_service()
        await svc.process(make_event())
        assert len(repo.upsert_calls) == 2


@pytest.mark.asyncio
class TestIdempotency:
    async def test_duplicate_event_returns_skipped(self) -> None:
        svc, repo, idem, _ = make_service()
        event = make_event()

        idem.mark_as_processed(f"evt_idem:{event.event_id}")
        result = await svc.process(event)

        assert result.status == EventStatus.SKIPPED
        assert len(repo.upsert_calls) == 0  # NAO persistiu

    async def test_idempotency_key_released_on_transient_error(self) -> None:
        svc, _, idem, _ = make_service(
            rules=[ExplodingRule(TransientError("timeout"))]
        )
        event = make_event()

        with pytest.raises(TransientError):
            await svc.process(event)

        key = f"evt_idem:{event.event_id}"
        assert key in idem.release_calls  # chave liberada para retry

    async def test_idempotency_key_NOT_released_on_permanent_error(self) -> None:
        svc, _, idem, _ = make_service(
            rules=[ExplodingRule(PermanentError("bad data"))]
        )
        event = make_event()

        with pytest.raises(PermanentError):
            await svc.process(event)

        assert len(idem.release_calls) == 0  # NAO liberou


@pytest.mark.asyncio
class TestBusinessRules:
    async def test_rule_failure_marks_event_failed(self) -> None:
        svc, repo, _, _ = make_service(rules=[FailureRule("preco invalido")])
        event = make_event()
        result = await svc.process(event)

        assert result.status == EventStatus.FAILED
        persisted = repo.store[event.event_id]
        assert persisted.status == EventStatus.FAILED

    async def test_rules_applied_in_order_stop_on_first_failure(self) -> None:
        applied: list[str] = []

        class TrackingRule:
            def __init__(self, rule_name: str, succeed: bool) -> None:
                self.name = rule_name
                self._succeed = succeed

            def applies_to(self, event: DomainEvent) -> bool:
                return True

            async def apply(self, event: DomainEvent) -> ProcessingResult:
                applied.append(self.name)
                if self._succeed:
                    return ProcessingResult.success(event.event_id)
                return ProcessingResult.failure(event.event_id, f"{self.name} failed")

        svc, _, _, _ = make_service(
            rules=[
                TrackingRule("rule_1", True),
                TrackingRule("rule_2", False),
                TrackingRule("rule_3", True),
            ]
        )
        await svc.process(make_event())
        # rule_3 nunca deve executar — pipeline para no primeiro FAILED
        assert applied == ["rule_1", "rule_2"]

    async def test_no_applicable_rules_returns_completed(self) -> None:
        class NeverAppliesRule:
            name = "never"

            def applies_to(self, event: DomainEvent) -> bool:
                return False

            async def apply(self, event: DomainEvent) -> ProcessingResult:
                return ProcessingResult.success(event.event_id)

        svc, _, _, _ = make_service(rules=[NeverAppliesRule()])
        result = await svc.process(make_event())
        assert result.status == EventStatus.COMPLETED


@pytest.mark.asyncio
class TestRetryAndDeadLetter:
    async def test_max_retries_exceeded_sends_to_dead_letter(self) -> None:
        # max_retries=0: qualquer falha vai direto para dead-letter
        svc, repo, _, _ = make_service(
            rules=[FailureRule("persistente")],
            max_retries=0,
        )
        event = make_event()

        with pytest.raises(MaxRetriesExceededError):
            await svc.process(event)

        assert repo.store[event.event_id].status == EventStatus.DEAD_LETTER

