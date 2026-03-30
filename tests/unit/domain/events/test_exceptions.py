"""Testes para a hierarquia de excecoes."""
from __future__ import annotations

import uuid

from finanalytics_ai.domain.events.exceptions import (
    DatabaseError,
    ExternalServiceError,
    IdempotencyConflict,
    MaxRetriesExceededError,
    PermanentError,
    RuleViolationError,
    TransientError,
)


def test_transient_is_event_processing_error() -> None:
    from finanalytics_ai.domain.events.exceptions import EventProcessingError
    exc = DatabaseError("timeout")
    assert isinstance(exc, EventProcessingError)
    assert isinstance(exc, TransientError)


def test_rule_violation_carries_rule_name() -> None:
    exc = RuleViolationError("violacao", rule_name="price_check")
    assert exc.rule_name == "price_check"
    assert isinstance(exc, PermanentError)


def test_idempotency_conflict_message() -> None:
    eid = uuid.uuid4()
    exc = IdempotencyConflict(eid)
    assert str(eid) in str(exc)


def test_external_service_error_retriable() -> None:
    assert ExternalServiceError("503", status_code=503).is_retriable
    assert ExternalServiceError("429", status_code=429).is_retriable
    assert not ExternalServiceError("400", status_code=400).is_retriable
    assert not ExternalServiceError("404", status_code=404).is_retriable
    assert ExternalServiceError("unknown").is_retriable  # sem status_code = retriavel


def test_max_retries_carries_count() -> None:
    exc = MaxRetriesExceededError(uuid.uuid4(), 5)
    assert exc.max_retries == 5
