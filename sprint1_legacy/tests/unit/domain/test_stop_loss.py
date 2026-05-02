"""Testes unitários para regras de Stop Loss."""

import pytest
from decimal import Decimal
from finanalytics_ai.domain.rules.stop_loss import StopLossRule, TrailingStopRule


class TestStopLossRule:
    @pytest.mark.asyncio
    async def test_no_violation_above_stop(self) -> None:
        rule = StopLossRule(stop_percentage=Decimal("5.0"))
        result = await rule.evaluate(
            {"ticker": "PETR4", "entry_price": "30.00", "current_price": "29.00"}
        )
        assert result.is_valid  # 29 > 28.5 (stop)

    @pytest.mark.asyncio
    async def test_violation_at_stop(self) -> None:
        rule = StopLossRule(stop_percentage=Decimal("5.0"))
        result = await rule.evaluate(
            {"ticker": "PETR4", "entry_price": "30.00", "current_price": "28.50"}
        )
        assert result.is_violation
        assert "stop_loss" == result.rule_name

    @pytest.mark.asyncio
    async def test_violation_below_stop(self) -> None:
        rule = StopLossRule(stop_percentage=Decimal("10.0"))
        result = await rule.evaluate(
            {"ticker": "VALE3", "entry_price": "100.00", "current_price": "85.00"}
        )
        assert result.is_violation
        assert "loss_pct" in (result.context or {})


class TestTrailingStopRule:
    @pytest.mark.asyncio
    async def test_no_violation(self) -> None:
        rule = TrailingStopRule(trail_percentage=Decimal("5.0"))
        result = await rule.evaluate(
            {"ticker": "VALE3", "max_price": "100.00", "current_price": "96.00"}
        )
        assert result.is_valid

    @pytest.mark.asyncio
    async def test_violation(self) -> None:
        rule = TrailingStopRule(trail_percentage=Decimal("5.0"))
        result = await rule.evaluate(
            {"ticker": "VALE3", "max_price": "100.00", "current_price": "94.00"}
        )
        assert result.is_violation
