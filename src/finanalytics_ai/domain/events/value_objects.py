"""
Value Objects do dominio de eventos.

Decisao: usar dataclasses frozen=True para imutabilidade garantida em runtime.
Constantes de tipo sao ClassVar para nao participarem do __init__ do dataclass.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class EventType:
    """
    Tipo do evento como value object.

    Constantes de classe (PRICE_UPDATE, etc.) sao ClassVar — o dataclass
    as ignora no __init__. Populadas logo apos a definicao da classe.
    """

    value: str

    # ClassVar exclui o campo do __init__ gerado pelo dataclass
    PRICE_UPDATE: ClassVar[EventType]
    PORTFOLIO_REBALANCE: ClassVar[EventType]
    TRADE_EXECUTED: ClassVar[EventType]
    ALERT_TRIGGERED: ClassVar[EventType]

    def __post_init__(self) -> None:
        if not self.value or not self.value.strip():
            raise ValueError("EventType nao pode ser vazio")
        if len(self.value) > 128:
            raise ValueError("EventType nao pode exceder 128 caracteres")

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"EventType({self.value!r})"


# Populacao das constantes de classe — fora do corpo do dataclass
EventType.PRICE_UPDATE = EventType("price.update")
EventType.PORTFOLIO_REBALANCE = EventType("portfolio.rebalance")
EventType.TRADE_EXECUTED = EventType("trade.executed")
EventType.ALERT_TRIGGERED = EventType("alert.triggered")


@dataclass(frozen=True)
class CorrelationId:
    """Rastreabilidade de causa-efeito entre eventos."""

    value: str

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"CorrelationId({self.value!r})"
