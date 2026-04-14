"""Protocols for analytics engine."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from finanalytics_ai.domain.analytics.models import CandleData, IndicatorResult


class IndicatorEngineProtocol(Protocol):
    """Interface for indicator computation engines."""

    def compute(self, candles: list[CandleData]) -> list[IndicatorResult]:
        """Compute indicators for a list of candles."""
        ...
