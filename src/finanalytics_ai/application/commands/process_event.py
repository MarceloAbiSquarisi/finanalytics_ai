"""
Commands da camada de aplicação.

Design decision: Command como dataclass imutável (frozen=True).
Comandos representam a intenção do usuário/sistema — são validados
antes de chegarem ao handler. Separar Command do Handler segue
o padrão CQRS e facilita serialização/auditoria.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal


@dataclass(frozen=True)
class ProcessMarketEventCommand:
    """Comando para processar um evento de mercado."""

    event_id: str
    event_type: str
    ticker: str
    payload: dict[str, Any]
    source: str = "unknown"
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class BuyAssetCommand:
    """Comando para registrar uma compra."""

    portfolio_id: str
    ticker: str
    quantity: Decimal
    price: Decimal
    broker: str = "unknown"
    idempotency_key: str = ""


@dataclass(frozen=True)
class SellAssetCommand:
    """Comando para registrar uma venda."""

    portfolio_id: str
    ticker: str
    quantity: Decimal
    price: Decimal
    broker: str = "unknown"
    idempotency_key: str = ""


@dataclass(frozen=True)
class SetStopLossCommand:
    """Configura stop loss para uma posição."""

    portfolio_id: str
    ticker: str
    stop_percentage: Decimal
    trailing: bool = False
