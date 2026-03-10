"""Testes unitários para Value Objects do domínio."""

from decimal import Decimal

import pytest

from finanalytics_ai.domain.value_objects.money import Currency, Money, Percentage, Quantity, Ticker
from finanalytics_ai.exceptions import InvalidQuantityError, InvalidTickerError


class TestMoney:
    def test_creates_with_decimal(self) -> None:
        m = Money.of("100.50")
        assert m.amount == Decimal("100.50")
        assert m.currency == Currency.BRL

    def test_rounds_to_two_places(self) -> None:
        m = Money.of("100.555")
        assert m.amount == Decimal("100.56")

    def test_addition_same_currency(self) -> None:
        result = Money.of("50.00") + Money.of("30.00")
        assert result.amount == Decimal("80.00")

    def test_subtraction(self) -> None:
        result = Money.of("100.00") - Money.of("30.00")
        assert result.amount == Decimal("70.00")

    def test_multiplication(self) -> None:
        result = Money.of("10.00") * 5
        assert result.amount == Decimal("50.00")

    def test_addition_different_currencies_raises(self) -> None:
        with pytest.raises(ValueError, match="Moedas diferentes"):
            Money.of("100", Currency.BRL) + Money.of("100", Currency.USD)

    def test_is_positive(self) -> None:
        assert Money.of("0.01").is_positive() is True
        assert Money.of("0.00").is_positive() is False

    def test_comparison(self) -> None:
        assert Money.of("10") < Money.of("20")
        assert Money.of("10") <= Money.of("10")

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError):
            Money.of("not-a-number")


class TestTicker:
    def test_normalizes_to_uppercase(self) -> None:
        t = Ticker("petr4")
        assert t.symbol == "PETR4"

    def test_strips_whitespace(self) -> None:
        t = Ticker("  VALE3  ")
        assert t.symbol == "VALE3"

    def test_empty_raises(self) -> None:
        with pytest.raises(InvalidTickerError):
            Ticker("")

    def test_special_chars_raises(self) -> None:
        with pytest.raises(InvalidTickerError):
            Ticker("PETR-4")

    def test_too_long_raises(self) -> None:
        with pytest.raises(InvalidTickerError):
            Ticker("TOOLONGTICKER")

    def test_equality(self) -> None:
        assert Ticker("PETR4") == Ticker("petr4")

    def test_str_representation(self) -> None:
        assert str(Ticker("PETR4")) == "PETR4"


class TestQuantity:
    def test_creates_positive(self) -> None:
        q = Quantity.of("100")
        assert q.value == Decimal("100.00000000")

    def test_zero_raises(self) -> None:
        with pytest.raises(InvalidQuantityError):
            Quantity.of("0")

    def test_negative_raises(self) -> None:
        with pytest.raises(InvalidQuantityError):
            Quantity.of("-5")

    def test_addition(self) -> None:
        result = Quantity.of("10") + Quantity.of("5")
        assert result.value == Decimal("15.00000000")

    def test_fractional(self) -> None:
        q = Quantity.of("0.5")
        assert q.value == Decimal("0.50000000")


class TestPercentage:
    def test_from_fraction(self) -> None:
        p = Percentage.from_fraction(0.05)
        assert p.value == Decimal("5.0")

    def test_as_fraction(self) -> None:
        p = Percentage.from_fraction(0.10)
        assert p.as_fraction() == Decimal("0.10")

    def test_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError):
            Percentage(Decimal("101"))
