"""
Regra de Stop Loss.

Verifica se o preço atual de um ativo atingiu o gatilho de stop loss
configurado para uma posição. Suporta stop fixo e stop trailing.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from finanalytics_ai.domain.rules.base import RuleResult


@dataclass
class StopLossRule:
    """
    Regra: stop loss fixo.

    Viola quando: preco_atual <= preco_entrada * (1 - stop_pct/100)

    Usage:
        rule = StopLossRule(stop_percentage=Decimal("5.0"))
        result = await rule.evaluate({"entry_price": 30.0, "current_price": 28.0})
    """

    stop_percentage: Decimal  # ex: 5.0 = 5% abaixo do preço de entrada

    async def evaluate(self, context: dict[str, Any]) -> RuleResult:
        entry_price = Decimal(str(context["entry_price"]))
        current_price = Decimal(str(context["current_price"]))
        ticker = str(context.get("ticker", "UNKNOWN"))

        stop_price = entry_price * (Decimal("1") - self.stop_percentage / Decimal("100"))

        if current_price <= stop_price:
            loss_pct = ((entry_price - current_price) / entry_price) * Decimal("100")
            return RuleResult.violation(
                rule_name="stop_loss",
                message=(
                    f"{ticker}: Stop loss atingido. "
                    f"Entrada: {entry_price:.2f}, Atual: {current_price:.2f}, "
                    f"Stop: {stop_price:.2f}, Perda: {loss_pct:.2f}%"
                ),
                context={
                    "ticker": ticker,
                    "entry_price": str(entry_price),
                    "current_price": str(current_price),
                    "stop_price": str(stop_price),
                    "loss_pct": str(loss_pct),
                },
            )
        return RuleResult.ok("stop_loss")


@dataclass
class TrailingStopRule:
    """
    Stop loss trailing: acompanha o preço máximo atingido.

    Gatilho: current_price <= max_price * (1 - trail_pct/100)
    """

    trail_percentage: Decimal

    async def evaluate(self, context: dict[str, Any]) -> RuleResult:
        max_price = Decimal(str(context["max_price"]))
        current_price = Decimal(str(context["current_price"]))
        ticker = str(context.get("ticker", "UNKNOWN"))

        stop_price = max_price * (Decimal("1") - self.trail_percentage / Decimal("100"))

        if current_price <= stop_price:
            drop_pct = ((max_price - current_price) / max_price) * Decimal("100")
            return RuleResult.violation(
                rule_name="trailing_stop",
                message=(
                    f"{ticker}: Trailing stop atingido. "
                    f"Máximo: {max_price:.2f}, Atual: {current_price:.2f}, "
                    f"Queda: {drop_pct:.2f}%"
                ),
                context={
                    "ticker": ticker,
                    "max_price": str(max_price),
                    "current_price": str(current_price),
                    "drop_pct": str(drop_pct),
                },
            )
        return RuleResult.ok("trailing_stop")
