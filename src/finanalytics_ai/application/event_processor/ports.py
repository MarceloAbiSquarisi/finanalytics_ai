"""
Ports (interfaces de saida) da camada de aplicacao.

Nomenclatura inspirada em Ports & Adapters (Hexagonal Architecture):
- Ports: o que a aplicacao PRECISA do mundo externo
- Adapters: implementacoes concretas na camada de infraestrutura

Decisao de design: usamos Protocol em vez de ABC para os ports pelos mesmos
motivos do domain/events/rules.py. A aplicacao declara o contrato;
a infraestrutura e livre para implementar sem heranca.

Todos os ports sao async — a aplicacao nao conhece detalhes de IO.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import uuid

from finanalytics_ai.domain.events.models import DomainEvent, EventStatus


@runtime_checkable
class EventRepository(Protocol):
    """
    Persistencia de eventos — port de saida.

    Nao expoe detalhes de ORM ou SQL. O dominio nao sabe que existe SQLAlchemy.
    upsert_event cobre tanto insert quanto update para simplificar o uso:
    o servico nao precisa saber se o evento e novo ou existente.
    """

    async def upsert(self, event: DomainEvent) -> None:
        """Persiste ou atualiza o evento."""
        ...

    async def find_by_id(self, event_id: uuid.UUID) -> DomainEvent | None:
        """Retorna None se nao encontrado — sem excecao."""
        ...

    async def find_by_status(
        self,
        status: EventStatus,
        *,
        limit: int = 100,
    ) -> list[DomainEvent]:
        """Para reprocessamento e monitoramento."""
        ...


@runtime_checkable
class IdempotencyStore(Protocol):
    """
    Store de idempotencia — port de saida.

    Decisao: separado do EventRepository pois tem semantica diferente
    (TTL, operacao atomica check-and-set) e pode usar backend diferente (Redis).

    check_and_set: operacao atomica. Retorna True se a chave JA EXISTIA
    (ou seja, evento ja foi processado). Retorna False e registra a chave
    se era nova.
    """

    async def check_and_set(self, key: str, ttl_seconds: int) -> bool:
        """
        Atomic check-and-set.
        Returns True se a chave ja existia (evento ja processado).
        Returns False e registra a chave se era nova.
        """
        ...

    async def release(self, key: str) -> None:
        """
        Remove a chave — usado quando o processamento falha com erro transitorio
        para permitir retry. NAO chamar em falhas permanentes.
        """
        ...


@runtime_checkable
class ObservabilityPort(Protocol):
    """
    Hooks de observabilidade — port de saida.

    Decisao: abstrair tracing/metrics atras de um port permite:
    1. Testar a logica de negocio sem dependencia de OTEL/Prometheus
    2. Trocar o backend de observabilidade sem alterar o servico
    3. Implementar NullObservability para desenvolvimento local

    Em producao, implementado com OpenTelemetry + Prometheus.
    """

    def record_processing_time(self, event_type: str, duration_ms: float) -> None: ...

    def record_event_status(self, event_type: str, status: str) -> None: ...

    def record_retry(self, event_type: str, retry_count: int) -> None: ...
