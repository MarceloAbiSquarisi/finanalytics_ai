"""
Entidade MarketEvent — evento de mercado processado de forma assíncrona.

Design decision: Events são imutáveis após criação (frozen=True exceto
processed_at que é setado uma vez). O event_id é a chave de idempotência —
dois eventos com o mesmo ID nunca serão processados duas vezes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
import uuid


class EventType(StrEnum):
    PRICE_UPDATE = "price_update"
    TRADE_EXECUTED = "trade_executed"
    DIVIDEND_ANNOUNCED = "dividend_announced"
    CORPORATE_ACTION = "corporate_action"
    NEWS_PUBLISHED = "news_published"
    ALERT_TRIGGERED = "alert_triggered"
    PORTFOLIO_REBALANCE = "portfolio_rebalance"
    STOP_LOSS_TRIGGERED = "stop_loss_triggered"
    OHLC_BAR_CLOSED = "ohlc_bar_closed"


class EventStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    SKIPPED = "skipped"  # duplicata detectada


@dataclass
class MarketEvent:
    """
    Evento de mercado. Imutável após criação — nunca altere campos.

    event_id: chave de idempotência. Gere com uuid4 no produtor,
              preserve ao fazer retry para evitar duplicatas.
    """

    event_type: EventType
    ticker: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    source: str = "unknown"
    status: EventStatus = EventStatus.PENDING
    retry_count: int = 0
    error_message: str = ""
    processed_at: datetime | None = None
    correlation_id: str | None = None

    def mark_processing(self) -> MarketEvent:
        """Retorna nova instância com status PROCESSING."""
        return MarketEvent(
            event_id=self.event_id,
            event_type=self.event_type,
            ticker=self.ticker,
            payload=self.payload,
            occurred_at=self.occurred_at,
            source=self.source,
            status=EventStatus.PROCESSING,
            retry_count=self.retry_count,
            correlation_id=self.correlation_id,
        )

    def mark_processed(self) -> MarketEvent:
        return MarketEvent(
            event_id=self.event_id,
            event_type=self.event_type,
            ticker=self.ticker,
            payload=self.payload,
            occurred_at=self.occurred_at,
            source=self.source,
            status=EventStatus.PROCESSED,
            retry_count=self.retry_count,
            processed_at=datetime.now(UTC),
            correlation_id=self.correlation_id,
        )

    def mark_failed(self, error: str) -> MarketEvent:
        return MarketEvent(
            event_id=self.event_id,
            event_type=self.event_type,
            ticker=self.ticker,
            payload=self.payload,
            occurred_at=self.occurred_at,
            source=self.source,
            status=EventStatus.FAILED,
            retry_count=self.retry_count + 1,
            error_message=error,
            correlation_id=self.correlation_id,
        )


@dataclass(frozen=True)
class OHLCBar:
    """
    Barra OHLC (Open/High/Low/Close) para armazenamento e backtesting.

    Imutável por design — dados históricos nunca são alterados.
    """

    ticker: str
    timestamp: datetime
    timeframe: str  # "1m", "5m", "15m", "1h", "1d", "1w"
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source: str = "unknown"

    def typical_price(self) -> Decimal:
        """(H+L+C)/3 — usado em VWAP e outros indicadores."""
        return (self.high + self.low + self.close) / Decimal("3")

    def is_bullish(self) -> bool:
        return self.close >= self.open

    def body_size(self) -> Decimal:
        return abs(self.close - self.open)
