"""
tests/unit/domain/conftest.py

Fixtures compartilhadas para testes da camada domain.
Resolve: portfolio_with_cash ausente em test_portfolio.py
"""
from __future__ import annotations

from decimal import Decimal
import pytest

from finanalytics_ai.domain.entities.portfolio import Portfolio
from finanalytics_ai.domain.value_objects.money import Money


@pytest.fixture
def portfolio_with_cash() -> Portfolio:
    """Portfolio com saldo inicial de R$ 10.000 para testes."""
    return Portfolio.create(
        owner_id="test-owner",
        initial_cash=Money.of("10000.00"),
    )
