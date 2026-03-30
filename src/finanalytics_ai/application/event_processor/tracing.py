"""
Port de tracing para o EventProcessorService.

Decisao de separar TracingPort do ObservabilityPort:
- Metricas (ObservabilityPort): operacoes unidimensionais, sem estado entre chamadas
- Tracing (TracingPort): spans tem inicio/fim, carregam contexto, propagam trace-id

Usar context manager (SpanContext) garante que o span seja fechado mesmo em excecoes,
sem precisar de try/finally espalhado pelo servico.

Implementacoes:
- NullTracing: no-op para testes (padrao — sem dependencia de OTEL em testes unit)
- OtelTracing: OpenTelemetry real (producao)
"""
from __future__ import annotations

from typing import Any, Protocol


class SpanContext(Protocol):
    """Contexto de um span ativo. Usado como async context manager."""

    async def __aenter__(self) -> SpanContext: ...
    async def __aexit__(self, *args: Any) -> None: ...

    def set_attribute(self, key: str, value: str | int | float | bool) -> None:
        """Adiciona atributo ao span atual."""
        ...

    def record_exception(self, exc: Exception) -> None:
        """Registra excecao no span sem encerrar (o caller decide o status)."""
        ...

    def set_error(self) -> None:
        """Marca o span como erro (status ERROR no OTEL)."""
        ...


class TracingPort(Protocol):
    """Port de tracing — injetado no EventProcessorService."""

    def start_span(self, name: str, attributes: dict[str, str | int] | None = None) -> SpanContext:
        """Cria um span. Usar como: async with tracing.start_span('event.process'):"""
        ...


# ── Implementacoes ────────────────────────────────────────────────────────────

class _NullSpan:
    """Span no-op — zero overhead em testes."""

    async def __aenter__(self) -> _NullSpan:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def set_attribute(self, key: str, value: str | int | float | bool) -> None:
        pass

    def record_exception(self, exc: Exception) -> None:
        pass

    def set_error(self) -> None:
        pass


class NullTracing:
    """TracingPort no-op — para testes unitarios e dev sem OTEL configurado."""

    def start_span(
        self,
        name: str,
        attributes: dict[str, str | int] | None = None,
    ) -> _NullSpan:
        return _NullSpan()


class OtelSpan:
    """Wrapper sobre opentelemetry.trace.Span como async context manager."""

    def __init__(self, span: Any) -> None:
        self._span = span
        self._ctx: Any = None

    async def __aenter__(self) -> OtelSpan:
        self._ctx = self._span.__enter__()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_val is not None:
            self.record_exception(exc_val)
            self.set_error()
        self._span.__exit__(exc_type, exc_val, exc_tb)

    def set_attribute(self, key: str, value: str | int | float | bool) -> None:
        self._span.set_attribute(key, value)

    def record_exception(self, exc: Exception) -> None:
        self._span.record_exception(exc)

    def set_error(self) -> None:
        from opentelemetry.trace import StatusCode
        self._span.set_status(StatusCode.ERROR)


class OtelTracing:
    """
    TracingPort usando OpenTelemetry.

    tracer_name: nome do tracer (normalmente o nome do servico).
    Criado uma vez no startup e reutilizado.
    """

    def __init__(self, tracer_name: str = "finanalytics.event_processor") -> None:
        try:
            from opentelemetry import trace
            self._tracer = trace.get_tracer(tracer_name)
            self._available = True
        except ImportError:
            self._available = False
            self._null = NullTracing()

    def start_span(
        self,
        name: str,
        attributes: dict[str, str | int] | None = None,
    ) -> Any:
        if not self._available:
            return self._null.start_span(name, attributes)
        span = self._tracer.start_span(name)
        if attributes:
            for k, v in attributes.items():
                span.set_attribute(k, v)
        return OtelSpan(span)
