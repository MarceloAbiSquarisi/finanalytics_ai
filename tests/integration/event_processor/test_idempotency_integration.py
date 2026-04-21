"""
Testes de integracao do RedisIdempotencyStore com Redis real.
"""

from __future__ import annotations

import asyncio

import pytest

from finanalytics_ai.infrastructure.event_processor.idempotency import RedisIdempotencyStore

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
class TestRedisIdempotencyStoreIntegration:
    async def test_first_call_returns_false(self, redis_client) -> None:
        store = RedisIdempotencyStore(redis_client)
        key = "test:first_call_false"
        await redis_client.delete(key)

        result = await store.check_and_set(key, ttl_seconds=60)
        assert result is False  # chave era nova

    async def test_second_call_returns_true(self, redis_client) -> None:
        store = RedisIdempotencyStore(redis_client)
        key = "test:second_call_true"
        await redis_client.delete(key)

        await store.check_and_set(key, ttl_seconds=60)
        result = await store.check_and_set(key, ttl_seconds=60)
        assert result is True  # chave ja existia

    async def test_release_allows_reprocess(self, redis_client) -> None:
        store = RedisIdempotencyStore(redis_client)
        key = "test:release_reprocess"
        await redis_client.delete(key)

        await store.check_and_set(key, ttl_seconds=60)
        await store.release(key)
        result = await store.check_and_set(key, ttl_seconds=60)
        assert result is False  # chave foi liberada

    async def test_concurrent_check_and_set_atomic(self, redis_client) -> None:
        """Apenas um de N workers concorrentes deve conseguir processar."""
        store = RedisIdempotencyStore(redis_client)
        key = "test:concurrent_atomic"
        await redis_client.delete(key)

        results = await asyncio.gather(
            *[store.check_and_set(key, ttl_seconds=60) for _ in range(10)]
        )
        # Exatamente 1 False (primeiro) e 9 True (ja existia)
        assert results.count(False) == 1
        assert results.count(True) == 9

    async def test_ttl_is_set(self, redis_client) -> None:
        store = RedisIdempotencyStore(redis_client)
        key = "test:ttl_check"
        await redis_client.delete(key)

        await store.check_and_set(key, ttl_seconds=120)
        ttl = await redis_client.ttl(key)
        assert 0 < ttl <= 120
