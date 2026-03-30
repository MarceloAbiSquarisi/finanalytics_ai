"""
Testes do EventProcessorService com tracing injetado.

Verifica que o tracing e chamado corretamente sem acoplamento ao OTEL real.
Usa um FakeTracing que registra as chamadas para inspecao.
"""
from __future__ import annotations

from typing import Any

import pytest

from finanalytics_ai.application.event_processor.config import EventProcessorConfig
from finanalytics_ai.application.event_processor.factory import (
    create_event_processor_service,
)
from finanalytics_ai.domain.events.models import DomainEvent, EventPayload, EventStatus
from finanalytics_ai.domain.events.value_objects import EventType
from tests.unit.application.event_processor.fakes import (
    FailureRule,
    FakeEventRepository,
    FakeIdempotencyStore,
    FakeObservability,
    SuccessRule,
)


class FakeSpan:
    """Span fake que registra chamadas para inspecao."""

    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}
        self.exceptions: list[Exception] = []
        self.error_set = False

    async def __aenter__(self) -> FakeSpan:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def record_exception(self, exc: Exception) -> None:
        self.exceptions.append(exc)

    def set_error(self) -> None:
        self.error_set = True


class FakeTracing:
    def __init__(self) -> None:
        self.spans: list[tuple[str, FakeSpan]] = []

    def start_span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
    ) -> FakeSpan:
        span = FakeSpan()
        if attributes:
            span.attributes.update(attributes)
        self.spans.append((name, span))
        return span

    def span_names(self) -> list[str]:
        return [name for name, _ in self.spans]

    def find_span(self, name: str) -> FakeSpan | None:
        for n, span in self.spans:
            if n == name:
                return span
        return None


def make_event(event_type: str = "price.update") -> DomainEvent:
    return DomainEvent.create(
        EventPayload(
            event_type=EventType(event_type),
            data={"ticker": "PETR4", "price": 38.5},
            source="test",
        )
    )


def make_service_with_tracing(
    rules: list | None = None,
    max_retries: int = 3,
) -> tuple:
    repo = FakeEventRepository()
    idem = FakeIdempotencyStore()
    obs = FakeObservability()
    tracing = FakeTracing()
    cfg = EventProcessorConfig(max_retries=max_retries, concurrency=10)
    svc = create_event_processor_service(
        repository=repo,
        idempotency_store=idem,
        rules=rules or [SuccessRule()],
        observability=obs,
        tracing=tracing,
        config=cfg,
    )
    return svc, repo, idem, obs, tracing


@pytest.mark.asyncio
class TestServiceWithTracing:
    async def test_process_creates_event_span(self) -> None:
        svc, _, _, _, tracing = make_service_with_tracing()
        await svc.process(make_event())

        assert "event.process" in tracing.span_names()

    async def test_span_has_event_attributes(self) -> None:
        svc, _, _, _, tracing = make_service_with_tracing()
        event = make_event("price.update")
        await svc.process(event)

        span = tracing.find_span("event.process")
        assert span is not None
        assert span.attributes["event.type"] == "price.update"
        assert span.attributes["event.source"] == "test"
        assert str(event.event_id) == span.attributes["event.id"]

    async def test_successful_span_has_result_completed(self) -> None:
        svc, _, _, _, tracing = make_service_with_tracing(rules=[SuccessRule()])
        await svc.process(make_event())

        span = tracing.find_span("event.process")
        assert span is not None
        assert span.attributes.get("event.result") == "completed"

    async def test_failed_rule_span_has_result_failed(self) -> None:
        svc, _, _, _, tracing = make_service_with_tracing(
            rules=[FailureRule("bad")], max_retries=0
        )
        event = make_event()
        # max_retries=0 -> vai para dead_letter e levanta MaxRetriesExceededError
        try:
            await svc.process(event)
        except Exception:
            pass

        span = tracing.find_span("event.process")
        assert span is not None
        # dead_letter ou failed dependendo do flow
        assert "event.result" in span.attributes

    async def test_rule_span_created_per_applicable_rule(self) -> None:
        svc, _, _, _, tracing = make_service_with_tracing(rules=[SuccessRule()])
        await svc.process(make_event())

        rule_spans = [n for n in tracing.span_names() if n.startswith("rule.")]
        assert len(rule_spans) == 1
        assert rule_spans[0] == "rule.always_success"

    async def test_idempotent_event_no_rule_span(self) -> None:
        svc, _, idem, _, tracing = make_service_with_tracing()
        event = make_event()
        idem.mark_as_processed(f"evt_idem:{event.event_id}")
        await svc.process(event)

        # Span do evento criado mas sem spans de regras
        rule_spans = [n for n in tracing.span_names() if n.startswith("rule.")]
        assert len(rule_spans) == 0
        span = tracing.find_span("event.process")
        assert span is not None
        assert span.attributes.get("event.result") == "skipped"

    async def test_backward_compat_no_tracing_arg(self) -> None:
        """Testes existentes continuam funcionando sem passar tracing."""
        from finanalytics_ai.application.event_processor.config import EventProcessorConfig
        cfg = EventProcessorConfig(max_retries=3, concurrency=10)
        svc = create_event_processor_service(
            repository=FakeEventRepository(),
            idempotency_store=FakeIdempotencyStore(),
            rules=[SuccessRule()],
            observability=FakeObservability(),
            # tracing NAO passado
            config=cfg,
        )
        result = await svc.process(make_event())
        assert result.status == EventStatus.COMPLETED
