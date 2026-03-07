"""
tests/unit/infrastructure/test_cache.py
────────────────────────────────────────
Testes unitários para InMemoryCache, InMemoryRateLimiter e utilitários.

Todos os testes usam os backends in-memory — sem dependência de Redis.
Os backends Redis são testados implicitamente via fallback (quando a
conexão falha, deve comportar-se como permissivo/miss).

Cobertura:
  InMemoryCache:
    - get/set/delete básicos
    - TTL expirado retorna None
    - TTL não expirado retorna valor
    - Valor grande (>5MB) é rejeitado silenciosamente
    - Eviction quando MAX_ENTRIES atingido
    - stats() reflete estado real

  make_cache_key:
    - Deterministico para mesmos params
    - Diferente para params diferentes
    - Diferente para prefixes diferentes
    - Order-independent (sort_keys)

  InMemoryRateLimiter:
    - Requests dentro do limite são permitidos
    - Request que excede retorna allowed=False
    - Janela deslizante: requests antigos expiram
    - Headers corretos no resultado
    - remaining decrementa corretamente
    - Múltiplas chaves independentes

  create_cache_backend / create_rate_limiter:
    - None → InMemory
    - url set → Redis (sem conectar neste teste)
"""
from __future__ import annotations

import asyncio
import time

import pytest

from finanalytics_ai.infrastructure.cache.backend import (
    InMemoryCache,
    make_cache_key,
    create_cache_backend,
    MAX_VALUE_BYTES,
)
from finanalytics_ai.infrastructure.cache.rate_limiter import (
    InMemoryRateLimiter,
    RateLimitResult,
    create_rate_limiter,
)


# ── InMemoryCache ─────────────────────────────────────────────────────────────

class TestInMemoryCache:

    @pytest.fixture
    def cache(self) -> InMemoryCache:
        return InMemoryCache()

    @pytest.mark.asyncio
    async def test_set_and_get(self, cache: InMemoryCache) -> None:
        await cache.set("k1", "hello", ttl=60)
        result = await cache.get("k1")
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, cache: InMemoryCache) -> None:
        result = await cache.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete(self, cache: InMemoryCache) -> None:
        await cache.set("k2", "value", ttl=60)
        await cache.delete("k2")
        assert await cache.get("k2") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_noop(self, cache: InMemoryCache) -> None:
        await cache.delete("ghost")  # Não deve levantar exceção

    @pytest.mark.asyncio
    async def test_ttl_expired_returns_none(self, cache: InMemoryCache) -> None:
        # Insere diretamente com timestamp no passado
        cache._store["expired_key"] = ("val", time.monotonic() - 1)
        result = await cache.get("expired_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_ttl_not_expired(self, cache: InMemoryCache) -> None:
        await cache.set("live", "alive", ttl=3600)
        result = await cache.get("live")
        assert result == "alive"

    @pytest.mark.asyncio
    async def test_exists_true(self, cache: InMemoryCache) -> None:
        await cache.set("ex", "v", ttl=60)
        assert await cache.exists("ex") is True

    @pytest.mark.asyncio
    async def test_exists_false(self, cache: InMemoryCache) -> None:
        assert await cache.exists("missing") is False

    @pytest.mark.asyncio
    async def test_exists_expired(self, cache: InMemoryCache) -> None:
        cache._store["e"] = ("v", time.monotonic() - 1)
        assert await cache.exists("e") is False

    @pytest.mark.asyncio
    async def test_overwrite_value(self, cache: InMemoryCache) -> None:
        await cache.set("k", "first", ttl=60)
        await cache.set("k", "second", ttl=60)
        assert await cache.get("k") == "second"

    @pytest.mark.asyncio
    async def test_large_value_rejected(self, cache: InMemoryCache) -> None:
        big = "x" * (MAX_VALUE_BYTES + 1)
        await cache.set("big", big, ttl=60)
        # Não deve ter sido armazenado
        assert await cache.get("big") is None

    @pytest.mark.asyncio
    async def test_eviction_on_max_entries(self) -> None:
        cache = InMemoryCache(max_entries=3)
        await cache.set("a", "1", ttl=3600)
        await cache.set("b", "2", ttl=3600)
        await cache.set("c", "3", ttl=3600)
        # Ao inserir o 4º, deve evictar um
        await cache.set("d", "4", ttl=3600)
        assert cache.stats()["entries"] <= 3

    @pytest.mark.asyncio
    async def test_stats_excludes_expired(self, cache: InMemoryCache) -> None:
        await cache.set("live1", "v", ttl=3600)
        cache._store["dead"] = ("v", time.monotonic() - 1)
        stats = cache.stats()
        assert stats["entries"] == 1  # só live1

    @pytest.mark.asyncio
    async def test_close_clears_store(self, cache: InMemoryCache) -> None:
        await cache.set("k", "v", ttl=60)
        await cache.close()
        assert len(cache._store) == 0


# ── make_cache_key ────────────────────────────────────────────────────────────

class TestMakeCacheKey:

    def test_deterministic(self) -> None:
        k1 = make_cache_key("screener", {"dy_min": 5.0, "roe_min": 12.0})
        k2 = make_cache_key("screener", {"dy_min": 5.0, "roe_min": 12.0})
        assert k1 == k2

    def test_order_independent(self) -> None:
        k1 = make_cache_key("screener", {"a": 1, "b": 2})
        k2 = make_cache_key("screener", {"b": 2, "a": 1})
        assert k1 == k2

    def test_different_params(self) -> None:
        k1 = make_cache_key("screener", {"dy_min": 5.0})
        k2 = make_cache_key("screener", {"dy_min": 6.0})
        assert k1 != k2

    def test_different_prefix(self) -> None:
        k1 = make_cache_key("screener", {"dy_min": 5.0})
        k2 = make_cache_key("anomaly", {"dy_min": 5.0})
        assert k1 != k2

    def test_key_starts_with_prefix(self) -> None:
        k = make_cache_key("mymodule", {})
        assert k.startswith("fa:mymodule:")

    def test_key_has_fixed_length_hash(self) -> None:
        # Prefixo "fa:x:" + 16 chars de hash
        k = make_cache_key("x", {"foo": "bar"})
        parts = k.split(":")
        assert len(parts[2]) == 16

    def test_empty_params(self) -> None:
        k1 = make_cache_key("route", {})
        k2 = make_cache_key("route", {})
        assert k1 == k2

    def test_nested_values_serializable(self) -> None:
        # Não deve levantar exceção com tipos básicos
        k = make_cache_key("test", {"tickers": ["PETR4", "VALE3"], "range": "1y"})
        assert k.startswith("fa:test:")


# ── InMemoryRateLimiter ───────────────────────────────────────────────────────

class TestInMemoryRateLimiter:

    @pytest.fixture
    def limiter(self) -> InMemoryRateLimiter:
        return InMemoryRateLimiter()

    @pytest.mark.asyncio
    async def test_first_request_allowed(self, limiter: InMemoryRateLimiter) -> None:
        result = await limiter.check("user:1:scan", limit=5, window=60)
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_within_limit_allowed(self, limiter: InMemoryRateLimiter) -> None:
        for _ in range(4):
            r = await limiter.check("user:2:scan", limit=5, window=60)
            assert r.allowed is True

    @pytest.mark.asyncio
    async def test_exceeds_limit_blocked(self, limiter: InMemoryRateLimiter) -> None:
        for _ in range(5):
            await limiter.check("user:3:opt", limit=5, window=60)
        r = await limiter.check("user:3:opt", limit=5, window=60)
        assert r.allowed is False

    @pytest.mark.asyncio
    async def test_blocked_has_retry_after(self, limiter: InMemoryRateLimiter) -> None:
        for _ in range(3):
            await limiter.check("user:4:wf", limit=3, window=60)
        r = await limiter.check("user:4:wf", limit=3, window=60)
        assert r.allowed is False
        assert r.retry_after > 0

    @pytest.mark.asyncio
    async def test_remaining_decrements(self, limiter: InMemoryRateLimiter) -> None:
        r1 = await limiter.check("user:5", limit=5, window=60)
        assert r1.remaining == 4
        r2 = await limiter.check("user:5", limit=5, window=60)
        assert r2.remaining == 3

    @pytest.mark.asyncio
    async def test_remaining_zero_when_blocked(self, limiter: InMemoryRateLimiter) -> None:
        for _ in range(3):
            await limiter.check("user:6", limit=3, window=60)
        r = await limiter.check("user:6", limit=3, window=60)
        assert r.remaining == 0

    @pytest.mark.asyncio
    async def test_limit_in_result(self, limiter: InMemoryRateLimiter) -> None:
        r = await limiter.check("user:7", limit=10, window=30)
        assert r.limit == 10

    @pytest.mark.asyncio
    async def test_reset_at_in_future(self, limiter: InMemoryRateLimiter) -> None:
        r = await limiter.check("user:8", limit=5, window=60)
        assert r.reset_at > int(time.time())

    @pytest.mark.asyncio
    async def test_different_keys_independent(self, limiter: InMemoryRateLimiter) -> None:
        for _ in range(3):
            await limiter.check("user:A", limit=3, window=60)
        blocked = await limiter.check("user:A", limit=3, window=60)
        free    = await limiter.check("user:B", limit=3, window=60)
        assert blocked.allowed is False
        assert free.allowed is True

    @pytest.mark.asyncio
    async def test_sliding_window_evicts_old(self, limiter: InMemoryRateLimiter) -> None:
        # Simula requests antigos inserindo timestamps passados diretamente
        from collections import deque
        key = "user:9:route"
        old_ts = time.time() - 120  # 2 minutos atrás
        limiter._windows[key] = deque([old_ts, old_ts, old_ts])
        # Janela de 60s → os 3 antigos devem ser evictados
        r = await limiter.check(key, limit=3, window=60)
        assert r.allowed is True

    @pytest.mark.asyncio
    async def test_close_clears_windows(self, limiter: InMemoryRateLimiter) -> None:
        await limiter.check("user:10", limit=5, window=60)
        await limiter.close()
        assert len(limiter._windows) == 0


# ── RateLimitResult.headers ───────────────────────────────────────────────────

class TestRateLimitResultHeaders:

    def test_allowed_headers(self) -> None:
        r = RateLimitResult(allowed=True, limit=10, remaining=9, reset_at=9999)
        h = r.headers()
        assert h["RateLimit-Limit"] == "10"
        assert h["RateLimit-Remaining"] == "9"
        assert h["RateLimit-Reset"] == "9999"
        assert "Retry-After" not in h

    def test_blocked_headers_include_retry_after(self) -> None:
        r = RateLimitResult(allowed=False, limit=5, remaining=0, reset_at=9999, retry_after=60)
        h = r.headers()
        assert h["Retry-After"] == "60"

    def test_remaining_floored_at_zero(self) -> None:
        r = RateLimitResult(allowed=False, limit=5, remaining=-1, reset_at=9999)
        h = r.headers()
        assert h["RateLimit-Remaining"] == "0"


# ── Factory functions ─────────────────────────────────────────────────────────

class TestFactories:

    def test_create_cache_backend_no_url_returns_inmemory(self) -> None:
        backend = create_cache_backend(None)
        assert isinstance(backend, InMemoryCache)

    def test_create_cache_backend_with_url_returns_redis(self) -> None:
        from finanalytics_ai.infrastructure.cache.backend import RedisCache
        backend = create_cache_backend("redis://localhost:6379/0")
        assert isinstance(backend, RedisCache)

    def test_create_rate_limiter_no_url_returns_inmemory(self) -> None:
        limiter = create_rate_limiter(None)
        assert isinstance(limiter, InMemoryRateLimiter)

    def test_create_rate_limiter_with_url_returns_redis(self) -> None:
        from finanalytics_ai.infrastructure.cache.rate_limiter import RedisRateLimiter
        limiter = create_rate_limiter("redis://localhost:6379/0")
        assert isinstance(limiter, RedisRateLimiter)
