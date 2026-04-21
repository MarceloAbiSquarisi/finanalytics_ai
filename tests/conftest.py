"""
conftest.py — fixtures compartilhadas.

Hierarquia de fixtures:
    unit_settings   — Settings sem banco; usada em testes unitários
    _silence_logging — autouse; suprime output de log nos testes

Princípio: fixtures de infra (engine, session) vivem nos conftest.py de cada
subpacote para não vazar para testes unitários que não precisam de banco.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from finanalytics_ai.config import Settings


@pytest.fixture(autouse=True)
def _silence_logging() -> None:  # type: ignore[return]
    """Silencia todos os loggers durante os testes."""
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


@pytest.fixture
def unit_settings(**overrides: Any) -> Settings:
    """Settings mínimo para testes unitários — sem banco, sem rede.

    Uso:
        def test_algo(unit_settings):
            processor = build_event_processor(..., unit_settings)

    Para sobrescrever valores:
        @pytest.fixture
        def custom_settings(unit_settings):
            return unit_settings.model_copy(update={"event_max_retries": 1})
    """
    return Settings.model_validate(
        {
            "database_url": "postgresql+asyncpg://test:test@localhost/test",
            "app_secret_key": "test-secret-key-16chars",
            "event_max_retries": 5,
            "event_retry_base_delay": 0.001,
            "event_processor_concurrency": 10,
            "metrics_enabled": False,
            "log_level": "ERROR",
            "log_format": "text",
            **overrides,
        }
    )
