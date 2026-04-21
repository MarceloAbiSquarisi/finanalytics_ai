"""
conftest.py para testes de integracao.

Estes testes requerem infraestrutura real (PostgreSQL, Redis).
Sao pulados automaticamente se a infraestrutura nao estiver disponivel.

Para rodar: DATABASE_URL=postgresql+asyncpg://... uv run pytest tests/integration -v
"""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(items: list) -> None:
    """Adiciona skip automatico em todos os testes de integracao se variaveis ausentes."""
    integration_env_vars = ["DATABASE_URL", "REDIS_URL", "TEST_DATABASE_URL"]
    has_infra = any(os.environ.get(v) for v in integration_env_vars)

    if not has_infra:
        skip_marker = pytest.mark.skip(
            reason=(
                "Testes de integracao requerem DATABASE_URL ou TEST_DATABASE_URL. "
                "Configure .env ou rode: TEST_DATABASE_URL=... uv run pytest tests/integration"
            )
        )
        for item in items:
            if "integration" in str(item.fspath):
                item.add_marker(skip_marker)
