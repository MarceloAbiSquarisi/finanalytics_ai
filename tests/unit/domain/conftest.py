"""
tests/unit/domain/conftest.py
Fixtures compartilhadas para testes da camada domain.
"""
from __future__ import annotations
import pytest
from finanalytics_ai.domain.entities.portfolio import Portfolio
from finanalytics_ai.domain.value_objects.money import Money


@pytest.fixture
def portfolio_with_cash() -> Portfolio:
    """Portfolio com saldo inicial de R$ 10.000 para testes."""
    return Portfolio(
        user_id="test-owner",
        name="Portfólio Teste",
        cash=Money.of("10000.00"),
    )
