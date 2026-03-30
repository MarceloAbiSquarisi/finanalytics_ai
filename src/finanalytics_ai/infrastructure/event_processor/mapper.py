"""
Mapeamento bidirecional entre DomainEvent e EventRecord (ORM).

Responsabilidade unica: traducao entre camadas.
Sem logica de negocio aqui.

Decisao: funcoes standalone em vez de metodos na entidade ou no ORM model.
Motivo: nem o dominio nem o ORM devem conhecer um ao outro.
O mapper e o unico lugar com essa dependencia cruzada, facilitando
identificar e controlar o acoplamento.
"""
from __future__ import annotations

from finanalytics_ai.domain.events.models import DomainEvent, EventPayload, EventStatus
from finanalytics_ai.domain.events.value_objects import CorrelationId, EventType
from finanalytics_ai.infrastructure.event_processor.orm_models import EventRecord


def domain_to_record(event: DomainEvent) -> EventRecord:
    return EventRecord(
        event_id=event.event_id,
        event_type=str(event.payload.event_type),
        source=event.payload.source,
        correlation_id=(
            str(event.payload.correlation_id)
            if event.payload.correlation_id
            else None
        ),
        status=event.status.value,
        payload_data=event.payload.data,
        error_message=event.error_message,
        retry_count=event.retry_count,
        metadata_=event.metadata,
        created_at=event.created_at,
        processed_at=event.processed_at,
    )


def record_to_domain(record: EventRecord) -> DomainEvent:
    payload = EventPayload(
        event_type=EventType(record.event_type),
        data=record.payload_data,
        source=record.source,
        correlation_id=(
            CorrelationId(record.correlation_id)
            if record.correlation_id
            else None
        ),
    )
    return DomainEvent(
        event_id=record.event_id,
        payload=payload,
        status=EventStatus(record.status),
        created_at=record.created_at,
        processed_at=record.processed_at,
        error_message=record.error_message,
        retry_count=record.retry_count,
        metadata=record.metadata_,
    )
