from __future__ import annotations
import os
import pytest
from unittest.mock import AsyncMock

# Force test config before any settings import
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-32-chars-minimum!!")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test_fa")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("APP_LOG_LEVEL", "WARNING")

from finanalytics_ai.domain.value_objects.money import Money, Ticker, Quantity, Currency
from finanalytics_ai.domain.entities.portfolio import Portfolio
from finanalytics_ai.domain.entities.event import MarketEvent, EventType


@pytest.fixture
def ticker_petr4() -> Ticker:
    return Ticker("PETR4")


@pytest.fixture
def ticker_vale3() -> Ticker:
    return Ticker("VALE3")


@pytest.fixture
def money_100() -> Money:
    return Money.of("100.00")


@pytest.fixture
def qty_10() -> Quantity:
    return Quantity.of("10")


@pytest.fixture
def portfolio_with_cash() -> Portfolio:
    p = Portfolio(user_id="user-001", name="Carteira Teste")
    p.cash = Money.of("10000.00")
    return p


@pytest.fixture
def sample_market_event() -> MarketEvent:
    return MarketEvent(
        event_id="evt-test-001",
        event_type=EventType.PRICE_UPDATE,
        ticker="PETR4",
        payload={"price": "32.50"},
        source="brapi",
    )


@pytest.fixture
def mock_event_store() -> AsyncMock:
    store = AsyncMock()
    store.exists.return_value = False
    store.find_by_id.return_value = None
    return store


@pytest.fixture
def mock_market_data() -> AsyncMock:
    provider = AsyncMock()
    provider.get_quote.return_value = Money.of("32.50")
    return provider
