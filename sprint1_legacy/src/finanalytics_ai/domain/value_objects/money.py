"""Value Objects: Money, Ticker, Quantity, Percentage."""

from __future__ import annotations
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import ClassVar
from finanalytics_ai.exceptions import InvalidQuantityError, InvalidTickerError


class Currency(StrEnum):
    BRL = "BRL"
    USD = "USD"
    EUR = "EUR"


@dataclass(frozen=True)
class Money:
    """Valor monetário com moeda. Usa Decimal para precisão financeira."""

    amount: Decimal
    currency: Currency = Currency.BRL
    _PLACES: ClassVar[Decimal] = Decimal("0.01")

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", self.amount.quantize(self._PLACES, ROUND_HALF_UP))

    @classmethod
    def of(cls, value: str | float | int | Decimal, currency: Currency = Currency.BRL) -> "Money":
        try:
            return cls(amount=Decimal(str(value)), currency=currency)
        except InvalidOperation as exc:
            raise ValueError(f"Valor inválido: {value!r}") from exc

    def __add__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: Decimal | int | float) -> "Money":
        return Money(self.amount * Decimal(str(factor)), self.currency)

    def __lt__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount <= other.amount

    def is_positive(self) -> bool:
        return self.amount > Decimal("0")

    def is_zero(self) -> bool:
        return self.amount == Decimal("0")

    def _check(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise ValueError(f"Moedas diferentes: {self.currency} vs {other.currency}")

    def __repr__(self) -> str:
        return f"Money({self.amount} {self.currency})"


VALID_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


@dataclass(frozen=True)
class Ticker:
    """Código de negociação normalizado (PETR4, VALE3, MXRF11)."""

    symbol: str

    def __post_init__(self) -> None:
        n = self.symbol.upper().strip()
        if not n:
            raise InvalidTickerError(message="Ticker vazio")
        if not all(c in VALID_CHARS for c in n):
            raise InvalidTickerError(
                message=f"Ticker inválido: {self.symbol!r}", context={"symbol": self.symbol}
            )
        if len(n) > 10:
            raise InvalidTickerError(
                message=f"Ticker longo: {self.symbol!r}", context={"symbol": self.symbol}
            )
        object.__setattr__(self, "symbol", n)

    def __str__(self) -> str:
        return self.symbol


@dataclass(frozen=True)
class Quantity:
    """Quantidade de ativos (suporta frações para fundos/cripto)."""

    value: Decimal
    _PLACES: ClassVar[Decimal] = Decimal("0.00000001")

    def __post_init__(self) -> None:
        if self.value <= Decimal("0"):
            raise InvalidQuantityError(message=f"Quantidade positiva requerida: {self.value}")
        object.__setattr__(self, "value", self.value.quantize(self._PLACES, ROUND_HALF_UP))

    @classmethod
    def of(cls, value: str | int | float | Decimal) -> "Quantity":
        try:
            return cls(Decimal(str(value)))
        except InvalidOperation as exc:
            raise InvalidQuantityError(message=f"Inválido: {value!r}") from exc

    def __add__(self, other: "Quantity") -> "Quantity":
        return Quantity(self.value + other.value)

    def __sub__(self, other: "Quantity") -> "Quantity":
        return Quantity(self.value - other.value)

    def __repr__(self) -> str:
        return f"Quantity({self.value})"


@dataclass(frozen=True)
class Percentage:
    """Percentual [-100, 100]. Use from_fraction(0.05) -> 5.0."""

    value: Decimal

    def __post_init__(self) -> None:
        if not (Decimal("-100") <= self.value <= Decimal("100")):
            raise ValueError(f"Fora do range: {self.value}")

    @classmethod
    def from_fraction(cls, f: float | Decimal) -> "Percentage":
        return cls(Decimal(str(f)) * Decimal("100"))

    def as_fraction(self) -> Decimal:
        return self.value / Decimal("100")

    def __repr__(self) -> str:
        return f"Percentage({self.value}%)"
