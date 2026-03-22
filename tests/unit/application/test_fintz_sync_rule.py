"""
Testes unitários — FintzSyncCompletedRule.

Testa a regra de negócio de forma completamente isolada:
sem banco, sem HTTP, sem filesystem.
"""

from __future__ import annotations

import pytest

from finanalytics_ai.application.rules.fintz_sync_rule import FintzSyncCompletedRule
from finanalytics_ai.domain.events.entities import Event, EventType
from finanalytics_ai.exceptions import BusinessRuleError


def _make_event(payload: dict) -> Event:
    return Event.create(
        event_type=EventType.FINTZ_SYNC_COMPLETED,
        payload=payload,
        source="test",
    )


def _valid_payload(**overrides) -> dict:
    base = {
        "dataset": "cotacoes",
        "rows_synced": 1000,
        "errors": 10,
        "duration_s": 5.0,
    }
    base.update(overrides)
    return base


class TestFintzSyncCompletedRule:
    async def test_apply_returns_metadata_on_success(self) -> None:
        rule = FintzSyncCompletedRule(error_rate_threshold=0.10)
        event = _make_event(_valid_payload(rows_synced=1000, errors=5))

        result = await rule.apply(event)

        assert result["dataset"] == "cotacoes"
        assert result["error_rate"] == pytest.approx(5 / 1005)
        assert result["rows_per_second"] == pytest.approx(1000 / 5.0)

    async def test_high_error_rate_raises_business_rule_error(self) -> None:
        rule = FintzSyncCompletedRule(error_rate_threshold=0.10)
        # 50% de erro — acima do threshold
        event = _make_event(_valid_payload(rows_synced=100, errors=100))

        with pytest.raises(BusinessRuleError, match="excede o limite"):
            await rule.apply(event)

    async def test_error_rate_exactly_at_threshold_passes(self) -> None:
        """Threshold é exclusivo (>), não >= ."""
        rule = FintzSyncCompletedRule(error_rate_threshold=0.10)
        # exatamente 10% de erro → deve passar
        event = _make_event(_valid_payload(rows_synced=900, errors=100))

        result = await rule.apply(event)
        assert result["error_rate"] == pytest.approx(100 / 1000)

    async def test_missing_payload_field_raises(self) -> None:
        rule = FintzSyncCompletedRule()
        event = _make_event({"dataset": "cotacoes"})  # faltam campos

        with pytest.raises(BusinessRuleError, match="faltando campos"):
            await rule.apply(event)

    async def test_zero_total_rows_no_division_error(self) -> None:
        """rows_synced=0, errors=0 não deve lançar ZeroDivisionError."""
        rule = FintzSyncCompletedRule(error_rate_threshold=0.10)
        event = _make_event(_valid_payload(rows_synced=0, errors=0))

        result = await rule.apply(event)
        assert result["error_rate"] == 0.0

    async def test_custom_threshold_respected(self) -> None:
        rule = FintzSyncCompletedRule(error_rate_threshold=0.50)
        # 40% de erro — abaixo do threshold de 50%
        event = _make_event(_valid_payload(rows_synced=600, errors=400))

        result = await rule.apply(event)
        assert result["error_rate"] == pytest.approx(400 / 1000)

    def test_handles_correct_event_type(self) -> None:
        rule = FintzSyncCompletedRule()
        assert EventType.FINTZ_SYNC_COMPLETED in rule.handles
        assert EventType.FINTZ_SYNC_FAILED not in rule.handles
