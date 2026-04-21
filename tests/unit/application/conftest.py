"""
tests/unit/application/conftest.py

Fixtures compartilhadas para testes da camada application.
Resolve: mock_event_store e mock_market_data ausentes em test_ohlc_handler.py
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def mock_event_store() -> AsyncMock:
    """Mock do repositório de eventos usado em test_ohlc_handler."""
    store = AsyncMock()
    store.exists = AsyncMock(return_value=False)
    store.save = AsyncMock(return_value=None)
    store.get = AsyncMock(return_value=None)
    return store


@pytest.fixture
def mock_market_data() -> AsyncMock:
    """Mock do cliente de market data usado em test_ohlc_handler."""
    client = AsyncMock()
    client.get_quote = AsyncMock(return_value=None)
    client.get_bars = AsyncMock(return_value=[])
    return client
