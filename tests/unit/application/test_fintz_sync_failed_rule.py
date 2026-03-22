"""Testes unitários — FintzSyncFailedRule."""

from __future__ import annotations

import pytest

from finanalytics_ai.application.rules.fintz_sync_failed_rule import FintzSyncFailedRule
from finanalytics_ai.domain.events.entities import Event, EventType
from finanalytics_ai.exceptions import BusinessRuleError


def _event(payload: dict) -> Event:
    return Event.create(event_type=EventType.FINTZ_SYNC_FAILED, payload=payload, source="test")


def _payload(**kw) -> dict:
    base = {"dataset": "cotacoes", "error_type": "APIError", "error_message": "timeout"}
    base.update(kw)
    return base


class TestFintzSyncFailedRule:
    async def test_critical_dataset_raises_business_rule_error(self) -> None:
        rule = FintzSyncFailedRule(escalate_critical=True)
        event = _event(_payload(dataset="cotacoes"))

        with pytest.raises(BusinessRuleError, match="dataset crítico"):
            await rule.apply(event)

    async def test_non_critical_dataset_returns_metadata(self) -> None:
        rule = FintzSyncFailedRule(escalate_critical=True)
        event = _event(_payload(dataset="algum_dataset_secundario"))

        result = await rule.apply(event)

        assert result["escalated"] is False
        assert result["dataset"] == "algum_dataset_secundario"

    async def test_escalate_false_never_raises(self) -> None:
        """Com escalate_critical=False, nem datasets críticos levantam erro."""
        rule = FintzSyncFailedRule(escalate_critical=False)
        event = _event(_payload(dataset="cotacoes"))

        result = await rule.apply(event)

        assert result["escalated"] is False

    async def test_missing_field_raises_business_rule_error(self) -> None:
        rule = FintzSyncFailedRule()
        event = _event({"dataset": "cotacoes"})  # falta error_type e error_message

        with pytest.raises(BusinessRuleError, match="incompleto"):
            await rule.apply(event)

    async def test_handles_correct_event_type(self) -> None:
        rule = FintzSyncFailedRule()
        assert EventType.FINTZ_SYNC_FAILED in rule.handles
        assert EventType.FINTZ_SYNC_COMPLETED not in rule.handles

    async def test_all_critical_datasets_escalate(self) -> None:
        rule = FintzSyncFailedRule(escalate_critical=True)
        critical = ["cotacoes", "itens_contabeis", "indicadores"]

        for dataset in critical:
            event = _event(_payload(dataset=dataset))
            with pytest.raises(BusinessRuleError):
                await rule.apply(event)
