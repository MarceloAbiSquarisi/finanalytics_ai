"""Analytics domain exceptions."""

from __future__ import annotations


class AnalyticsError(Exception):
    """Base exception for analytics domain."""


class InsufficientDataError(AnalyticsError):
    """Raised when not enough candles are available for indicator computation."""

    def __init__(self, ticker: str, required: int, available: int) -> None:
        self.ticker = ticker
        self.required = required
        self.available = available
        super().__init__(f"{ticker}: need {required} candles, only {available} available")


class PairNotCointegrated(AnalyticsError):
    """Raised when a pair fails cointegration test."""


class MarketDataUnavailable(AnalyticsError):
    """Raised when no market data source returns data for the ticker."""

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        super().__init__(f"No market data available for {ticker}")
