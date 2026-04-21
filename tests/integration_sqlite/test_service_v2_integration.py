"""
Testes de integracao do EventProcessorService V2.

Comportamento real do servico (verificado em service.py):
- TransientError: servico marca evento como FAILED + RE-RAISE para o caller.
  O worker e quem controla o timing do retry. O teste deve usar pytest.raises.
- PermanentError: servico marca como DEAD_LETTER + RE-RAISE.
- ProcessingResult.success(): retorna SEM o output da regra (output=None).
  O output da regra e armazenado em event.metadata["output"], nao no Result.
- Idempotencia: segundo processamento retorna ProcessingResult.skipped().

Testamos o comportamento OBSERVAVEL — o que o repositorio persiste
e o que o servico retorna/lanca — nao a implementacao interna.
"""

from __future__ import annotations

import pytest

from finanalytics_ai.application.event_processor.factory import create_event_processor_service
from finanalytics_ai.domain.events.exceptions import TransientError
from finanalytics_ai.domain.events.models import (
    DomainEvent,
    EventPayload,
    EventStatus,
    ProcessingResult,
)
from finanalytics_ai.domain.events.value_objects import EventType

# ── Helpers ────────────────────────────────────────────────────────────────


def _make_event(event_type: str = "price.update") -> DomainEvent:
    payload = EventPayload(
        event_type=EventType(event_type),
        data={"ticker": "PETR4", "price": 38.50},
        source="test",
    )
    return DomainEvent.create(payload)


class _SuccessRule:
    name = "success_rule"

    def applies_to(self, event: DomainEvent) -> bool:
        return True

    async def apply(self, event: DomainEvent) -> ProcessingResult:
        return ProcessingResult.success(event.event_id, {"processed": True})


class _AlwaysTransientRule:
    """Sempre lanca TransientError — simula servico externo indisponivel."""

    name = "always_transient"

    def __init__(self, message: str = "database timeout") -> None:
        self.message = message

    def applies_to(self, event: DomainEvent) -> bool:
        return True

    async def apply(self, event: DomainEvent) -> ProcessingResult:
        raise TransientError(self.message, event_id=event.event_id)


class _NoOpObservability:
    def record_processing_time(self, event_type: str, duration_ms: float) -> None:
        pass

    def record_event_status(self, event_type: str, status: str) -> None:
        pass

    def record_retry(self, event_type: str, retry_count: int) -> None:
        pass


def _make_service(repository, idempotency_store, rules):
    return create_event_processor_service(
        repository=repository,
        idempotency_store=idempotency_store,
        rules=rules,
        observability=_NoOpObservability(),
    )


# ── TestIdempotencia ───────────────────────────────────────────────────────


class TestIdempotencia:
    async def test_segundo_processamento_retorna_skipped(
        self, sql_repository, idempotency_store
    ) -> None:
        """
        Mesmo evento processado duas vezes:
        - 1a chamada: COMPLETED
        - 2a chamada: SKIPPED (idempotencia ativada)
        """
        event = _make_event()
        service = _make_service(sql_repository, idempotency_store, [_SuccessRule()])

        result1 = await service.process(event)
        result2 = await service.process(event)

        assert result1.status == EventStatus.COMPLETED
        assert result2.status == EventStatus.SKIPPED

    async def test_eventos_diferentes_processados_independentemente(
        self, sql_repository, idempotency_store
    ) -> None:
        """Dois eventos distintos: ambos completados independentemente."""
        event_a = _make_event()
        event_b = _make_event()
        service = _make_service(sql_repository, idempotency_store, [_SuccessRule()])

        result_a = await service.process(event_a)
        result_b = await service.process(event_b)

        assert result_a.status == EventStatus.COMPLETED
        assert result_b.status == EventStatus.COMPLETED
        assert result_a.event_id != result_b.event_id


# ── TestRetry ─────────────────────────────────────────────────────────────


class TestRetry:
    async def test_transient_error_re_raised_e_evento_marcado_failed(
        self, sql_repository, idempotency_store
    ) -> None:
        """
        Design do servico: TransientError e RE-RAISED apos marcar evento como FAILED.
        O worker e responsavel pelo timing do retry — nao o servico.

        Por que re-raise e nao retornar ProcessingResult.failure()?
            O worker precisa saber que ocorreu um erro transitorio para
            aplicar backoff exponencial antes do proximo ciclo.
            Se o servico engolisse a excecao e retornasse FAILED silenciosamente,
            o worker nao saberia quando fazer o retry.
        """
        event = _make_event()
        service = _make_service(sql_repository, idempotency_store, [_AlwaysTransientRule()])

        with pytest.raises(TransientError):
            await service.process(event)

        # Mesmo re-raising, o evento DEVE estar persistido como FAILED
        persisted = await sql_repository.find_by_id(event.event_id)
        assert persisted is not None
        assert persisted.status == EventStatus.FAILED
        assert persisted.retry_count == 1

    async def test_error_message_persistida_antes_do_re_raise(
        self, sql_repository, idempotency_store
    ) -> None:
        """
        Mensagem de erro deve ser persistida mesmo quando a excecao
        e re-raised — garante rastreabilidade para debugging.
        """
        mensagem = "timeout conectando ao redis"
        event = _make_event()
        service = _make_service(
            sql_repository, idempotency_store, [_AlwaysTransientRule(message=mensagem)]
        )

        with pytest.raises(TransientError):
            await service.process(event)

        persisted = await sql_repository.find_by_id(event.event_id)
        assert persisted is not None
        assert persisted.error_message is not None
        assert mensagem in persisted.error_message


# ── TestSucessoCompleto ────────────────────────────────────────────────────


class TestSucessoCompleto:
    async def test_evento_completo_persistido_no_banco(
        self, sql_repository, idempotency_store
    ) -> None:
        """Evento processado com sucesso deve ser persistido como COMPLETED."""
        event = _make_event()
        service = _make_service(sql_repository, idempotency_store, [_SuccessRule()])

        result = await service.process(event)

        assert result.status == EventStatus.COMPLETED

        persisted = await sql_repository.find_by_id(event.event_id)
        assert persisted is not None
        assert persisted.status == EventStatus.COMPLETED

    async def test_result_output_e_none_por_design(self, sql_repository, idempotency_store) -> None:
        """
        ProcessingResult.output e sempre None — design do servico.

        O servico retorna ProcessingResult.success(event_id) sem propagar
        o output da regra. Isso e intencional: o Result e um DTO minimo
        para o worker; o output completo fica em event.metadata["output"].

        Se precisar do output da regra no caller, acessar via repositorio:
            event = await repo.find_by_id(event_id)
            output = event.metadata.get("output")
        """
        event = _make_event()
        service = _make_service(sql_repository, idempotency_store, [_SuccessRule()])

        result = await service.process(event)

        assert result.status == EventStatus.COMPLETED
        # output do ProcessingResult e None por design do servico
        assert result.output is None
        # o evento em si contem o output via metadata (se a regra persistiu)
        persisted = await sql_repository.find_by_id(event.event_id)
        assert persisted is not None
        assert persisted.status == EventStatus.COMPLETED
