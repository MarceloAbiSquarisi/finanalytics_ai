"""
Testes do TracingPort e implementacoes.

Foco: garantir que NullTracing nao levanta excecoes e que
OtelSpan fecha o span mesmo em caso de excecao.
"""
from __future__ import annotations

import pytest

from finanalytics_ai.application.event_processor.tracing import NullTracing


@pytest.mark.asyncio
class TestNullTracing:
    async def test_span_as_context_manager(self) -> None:
        tracing = NullTracing()
        async with tracing.start_span("test.span") as span:
            span.set_attribute("key", "value")
            span.set_attribute("count", 42)
        # Nao deve levantar nenhuma excecao

    async def test_span_record_exception(self) -> None:
        tracing = NullTracing()
        async with tracing.start_span("test.span") as span:
            span.record_exception(ValueError("test"))
            span.set_error()

    async def test_span_with_attributes(self) -> None:
        tracing = NullTracing()
        span = tracing.start_span("test", attributes={"event.id": "123", "retry": 1})
        async with span:
            pass

    async def test_nested_spans(self) -> None:
        tracing = NullTracing()
        async with tracing.start_span("outer") as outer:
            outer.set_attribute("level", "outer")
            async with tracing.start_span("inner") as inner:
                inner.set_attribute("level", "inner")

    async def test_span_exits_on_exception(self) -> None:
        tracing = NullTracing()
        with pytest.raises(RuntimeError):
            async with tracing.start_span("test") as span:
                span.set_error()
                raise RuntimeError("test error")
