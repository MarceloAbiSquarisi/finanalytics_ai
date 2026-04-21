"""
finanalytics_ai.infrastructure.cache.rate_limiter
───────────────────────────────────────────────────
Rate limiter com algoritmo Sliding Window usando Redis Sorted Sets.

Algoritmo Sliding Window:
  Para cada (key, window_seconds):
    1. ZADD key <now_ms> <now_ms>       — registra request atual
    2. ZREMRANGEBYSCORE key 0 <now_ms - window_ms>  — remove fora da janela
    3. ZCARD key                         — conta requests na janela
    4. EXPIRE key <window_seconds>       — TTL do set

  Se ZCARD > limit → 429 Too Many Requests

Por que Sliding Window vs Fixed Window?
  Fixed Window tem o problema do "double burst": um cliente pode
  fazer 2x o limite na virada de janela (fim da janela anterior +
  início da próxima). Sliding Window resolve isso com custo de
  memória ligeiramente maior (O(requests) vs O(1)).
  Para os volumes desta aplicação (10-50 req/min por rota),
  o custo é desprezível.

Por que não Token Bucket?
  Token Bucket é mais suave para bursts legítimos mas requer
  estado atômico complexo (lua script). Sliding Window é mais
  simples e correto para APIs developer-facing onde queremos
  hard limits, não suavização.

Fallback sem Redis:
  InMemoryRateLimiter usa deque com timestamps.
  Correto para single-process (1 réplica API).
  Para múltiplas réplicas, Redis é obrigatório.

Headers de resposta (RFC 6585 / IETF draft-polli):
  RateLimit-Limit:     N
  RateLimit-Remaining: M
  RateLimit-Reset:     <unix timestamp>
  Retry-After:         <segundos>  (apenas no 429)
"""

from __future__ import annotations

from collections import deque
import time
from typing import Protocol, runtime_checkable

import structlog

logger = structlog.get_logger(__name__)


# ── Result ────────────────────────────────────────────────────────────────────


class RateLimitResult:
    __slots__ = ("allowed", "limit", "remaining", "reset_at", "retry_after")

    def __init__(
        self,
        allowed: bool,
        limit: int,
        remaining: int,
        reset_at: int,
        retry_after: int = 0,
    ) -> None:
        self.allowed = allowed
        self.limit = limit
        self.remaining = remaining
        self.reset_at = reset_at
        self.retry_after = retry_after

    def headers(self) -> dict[str, str]:
        h = {
            "RateLimit-Limit": str(self.limit),
            "RateLimit-Remaining": str(max(0, self.remaining)),
            "RateLimit-Reset": str(self.reset_at),
        }
        if not self.allowed:
            h["Retry-After"] = str(self.retry_after)
        return h


# ── Protocol ──────────────────────────────────────────────────────────────────


@runtime_checkable
class RateLimiterBackend(Protocol):
    async def check(self, key: str, limit: int, window: int) -> RateLimitResult: ...
    async def close(self) -> None: ...


# ── Redis Sliding Window ───────────────────────────────────────────────────────


class RedisRateLimiter:
    """
    Sliding window rate limiter usando Redis Sorted Sets.
    Atomic via pipeline — não usa Lua script para simplicidade.

    Nota: o pipeline não é 100% atômico (race condition entre
    ZREMRANGEBYSCORE e ZCARD), mas para rate limiting "best effort"
    em aplicação single-tenant é aceitável. Para multi-tenant
    crítico, usar script Lua ou Redlock.
    """

    def __init__(self, redis_url: str) -> None:
        self._url = redis_url
        self._client: object | None = None

    async def _get_client(self) -> object:
        if self._client is None:
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(
                self._url,
                decode_responses=True,
                socket_timeout=1.0,
                socket_connect_timeout=1.0,
            )
        return self._client

    async def check(self, key: str, limit: int, window: int) -> RateLimitResult:
        try:
            client = await self._get_client()
            now_ms = int(time.time() * 1000)
            window_ms = window * 1000
            cutoff = now_ms - window_ms
            rl_key = f"rl:{key}"

            pipe = client.pipeline()  # type: ignore[attr-defined]
            pipe.zadd(rl_key, {str(now_ms): now_ms})
            pipe.zremrangebyscore(rl_key, 0, cutoff)
            pipe.zcard(rl_key)
            pipe.expire(rl_key, window)
            results = await pipe.execute()

            count = int(results[2])
            remaining = max(0, limit - count)
            reset_at = int(time.time()) + window
            allowed = count <= limit

            if not allowed:
                logger.warning(
                    "rate_limit.exceeded",
                    key=key,
                    count=count,
                    limit=limit,
                    window=window,
                )

            return RateLimitResult(
                allowed=allowed,
                limit=limit,
                remaining=remaining,
                reset_at=reset_at,
                retry_after=window if not allowed else 0,
            )

        except Exception as exc:
            # Redis fora do ar → permitir request (fail open)
            logger.warning("rate_limiter.redis_error", error=str(exc))
            return RateLimitResult(
                allowed=True,
                limit=limit,
                remaining=limit,
                reset_at=int(time.time()) + window,
            )

    async def close(self) -> None:
        if self._client is not None:
            import contextlib

            with contextlib.suppress(Exception):
                await self._client.aclose()  # type: ignore[attr-defined]


# ── InMemory fallback ─────────────────────────────────────────────────────────


class InMemoryRateLimiter:
    """
    Sliding window em memória. Correto para processo único.
    Cada chave mantém uma deque de timestamps (em segundos float).
    """

    def __init__(self) -> None:
        self._windows: dict[str, deque[float]] = {}

    async def check(self, key: str, limit: int, window: int) -> RateLimitResult:
        now = time.time()
        cutoff = now - window

        if key not in self._windows:
            self._windows[key] = deque()

        dq = self._windows[key]
        # Remove entradas fora da janela
        while dq and dq[0] <= cutoff:
            dq.popleft()

        count = len(dq)
        allowed = count < limit
        if allowed:
            dq.append(now)
            count += 1

        remaining = max(0, limit - count)
        reset_at = int(now) + window

        return RateLimitResult(
            allowed=allowed,
            limit=limit,
            remaining=remaining,
            reset_at=reset_at,
            retry_after=window if not allowed else 0,
        )

    async def close(self) -> None:
        self._windows.clear()


# ── Factory ───────────────────────────────────────────────────────────────────


def create_rate_limiter(redis_url: str | None) -> RateLimiterBackend:
    if redis_url:
        logger.info("rate_limiter.backend.redis")
        return RedisRateLimiter(str(redis_url))
    logger.info("rate_limiter.backend.memory", reason="REDIS_URL not set")
    return InMemoryRateLimiter()
