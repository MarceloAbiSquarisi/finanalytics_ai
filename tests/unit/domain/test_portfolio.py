"""Testes unitários para a entidade Portfolio."""
import pytest
from decimal import Decimal
from finanalytics_ai.domain.entities.portfolio import Portfolio, Position
from finanalytics_ai.domain.value_objects.money import Money, Ticker, Quantity
from finanalytics_ai.exceptions import InsufficientFundsError, PortfolioNotFoundError


class TestPortfolio:
    def test_initial_state(self, portfolio_with_cash: Portfolio) -> None:
        assert portfolio_with_cash.cash == Money.of("10000.00")
        assert portfolio_with_cash.position_count() == 0

    def test_add_position(self, portfolio_with_cash: Portfolio) -> None:
        portfolio_with_cash.add_position(
            Ticker("PETR4"), Quantity.of("100"), Money.of("30.00")
        )
        assert "PETR4" in portfolio_with_cash.positions
        assert portfolio_with_cash.cash == Money.of("7000.00")

    def test_add_position_insufficient_funds(self, portfolio_with_cash: Portfolio) -> None:
        with pytest.raises(InsufficientFundsError):
            portfolio_with_cash.add_position(
                Ticker("VALE3"), Quantity.of("1000"), Money.of("100.00")
            )

    def test_remove_position(self, portfolio_with_cash: Portfolio) -> None:
        portfolio_with_cash.add_position(Ticker("PETR4"), Quantity.of("10"), Money.of("30.00"))
        proceeds = portfolio_with_cash.remove_position(
            Ticker("PETR4"), Quantity.of("10"), Money.of("35.00")
        )
        assert proceeds == Money.of("350.00")
        assert "PETR4" not in portfolio_with_cash.positions

    def test_remove_nonexistent_raises(self, portfolio_with_cash: Portfolio) -> None:
        with pytest.raises(PortfolioNotFoundError):
            portfolio_with_cash.remove_position(
                Ticker("UNKN"), Quantity.of("1"), Money.of("10")
            )

    def test_average_price_calculation(self, portfolio_with_cash: Portfolio) -> None:
        # Compra 10 @ R$30 + 10 @ R$40 = PM R$35
        portfolio_with_cash.add_position(Ticker("PETR4"), Quantity.of("10"), Money.of("30.00"))
        portfolio_with_cash.add_position(Ticker("PETR4"), Quantity.of("10"), Money.of("40.00"))
        pos = portfolio_with_cash.positions["PETR4"]
        assert pos.average_price == Money.of("35.00")
        assert pos.quantity.value == Decimal("20.00000000")

    def test_profit_loss(self) -> None:
        pos = Position(
            ticker=Ticker("PETR4"),
            quantity=Quantity.of("100"),
            average_price=Money.of("30.00"),
        )
        pl = pos.profit_loss(Money.of("35.00"))
        assert pl == Money.of("500.00")

    def test_total_invested(self, portfolio_with_cash: Portfolio) -> None:
        portfolio_with_cash.add_position(Ticker("PETR4"), Quantity.of("10"), Money.of("30"))
        portfolio_with_cash.add_position(Ticker("VALE3"), Quantity.of("5"), Money.of("80"))
        total = portfolio_with_cash.total_invested()
        assert total == Money.of("700.00")
