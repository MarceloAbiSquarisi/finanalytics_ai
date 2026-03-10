"""
finanalytics_ai.infrastructure.cache.backend
─────────────────────────────────────────────
Abstração de cache com dois backends:

  RedisCache:
    Backend de produção. Usa redis-py asyncio com serialização JSON.
    TTL por chave. Operações get/set/delete/exists.

  InMemoryCache:
    Fallback quando Redis não está disponível.
    Dict simples com expiração por timestamp.
    Thread-safe via asyncio (single-threaded event loop).
    Capacidade máxima (LRU simplificado): remove entradas expiradas
    ao atingir MAX_ENTRIES.

Design decisions:

  CacheBackend Protocol:
    Permite trocar backend em testes sem necessidade de mock de Redis.
    InMemory é o backend em dev local sem Redis configurado.

  Degradação graceful:
    Se Redis sair do ar, cache.get() retorna None (cache miss) e
    cache.set() loga warning mas não levanta exceção.
    A aplicação continua funcionando, só sem cache.

  Chave cache:
    Gerada via hashlib.sha256 dos parâmetros serializados.
    Inclui prefixo do módulo para evitar colisão entre rotas
    com mesmos parâmetros (ex: backtest e correlation com mesmos tickers).

  Serialização:
    JSON puro — sem pickle (segurança) nem msgpack (dependência extra).
    Valores > 5MB são recusados silenciosamente (proteção contra OOM).

  TTLs recomendados por rota:
    screener/run       — 300s  (dados fundamentais mudam pouco intraday)
    anomaly/scan       — 120s  (detecção recente ainda relevante por 2min)
    backtest/optimize  — 600s  (grid search CPU-heavy, resultados estáveis)
    backtest/multi     — 300s  (mesmo que scan)
    backtest/walkforward — 600s
    correlation        — 180s  (correlação muda lentamente)
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from typing import Any, Protocol, runtime_checkable

import structlog

logger = structlog.get_logger(__name__)

MAX_VALUE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_MEMORY_ENTRIES = 512


# ── Protocol ──────────────────────────────────────────────────────────────────


@runtime_checkable
class CacheBackend(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def exists(self, key: str) -> bool: ...
    async def close(self) -> None: ...


# ── Key builder ───────────────────────────────────────────────────────────────


def make_cache_key(prefix: str, params: dict[str, Any]) -> str:
    """
    Gera chave de cache determinística a partir do prefixo e params.

    Ordena as chaves do dict para garantir que a mesma requisição
    com parâmetros em ordem diferente produza a mesma key.
    """
    serialized = json.dumps(params, sort_keys=True, default=str)
    digest = hashlib.sha256(serialized.encode()).hexdigest()[:16]
    return f"fa:{prefix}:{digest}"


# ── InMemory backend (fallback) ───────────────────────────────────────────────


class InMemoryCache:
    """
    Cache em memória com TTL. Fallback quando Redis não disponível.
    Não persiste entre restarts — aceitável para cache de resultados.
    """

    def __init__(self, max_entries: int = MAX_MEMORY_ENTRIES) -> None:
        self._store: dict[str, tuple[str, float]] = {}  # key -> (value, expires_at)
        self._max = max_entries

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, (_, exp) in self._store.items() if exp <= now]
        for k in expired:
            del self._store[k]

    async def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        return value

    async def set(self, key: str, value: str, ttl: int) -> None:
        if len(value.encode()) > MAX_VALUE_BYTES:
            logger.warning("cache.value_too_large", key=key, size=len(value))
            return
        if len(self._store) >= self._max:
            self._evict_expired()
            # Se ainda cheio, remove a primeira entrada (FIFO simplificado)
            if len(self._store) >= self._max:
                oldest = next(iter(self._store))
                del self._store[oldest]
        self._store[key] = (value, time.monotonic() + ttl)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def exists(self, key: str) -> bool:
        return await self.get(key) is not None

    async def close(self) -> None:
        self._store.clear()

    def stats(self) -> dict[str, int]:
        self._evict_expired()
        return {"entries": len(self._store), "max": self._max}


# ── Redis backend ─────────────────────────────────────────────────────────────


class RedisCache:
    """
    Cache Redis async usando redis-py.

    Inicialização lazy — não conecta no __init__, conecta no primeiro uso.
    Isso permite criar a instância no startup sem bloquear.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: Any = None  # redis.asyncio.Redis

    async def _get_client(self) -> Any:
        if self._client is None:
            try:
                import redis.asyncio as aioredis

                self._client = aioredis.from_url(
                    self._url,
                    decode_responses=True,
                    socket_timeout=2.0,
                    socket_connect_timeout=2.0,
                    retry_on_timeout=False,
                )
            except ImportError as exc:
                raise RuntimeError("redis não instalado. Execute: pip install redis") from exc
        return self._client

    async def get(self, key: str) -> str | None:
        try:
            client = await self._get_client()
            return await client.get(key)
        except Exception as exc:
            logger.warning("redis.get_failed", key=key, error=str(exc))
            return None

    async def set(self, key: str, value: str, ttl: int) -> None:
        if len(value.encode()) > MAX_VALUE_BYTES:
            logger.warning("cache.value_too_large", key=key)
            return
        try:
            client = await self._get_client()
            await client.setex(key, ttl, value)
        except Exception as exc:
            logger.warning("redis.set_failed", key=key, error=str(exc))

    async def delete(self, key: str) -> None:
        try:
            client = await self._get_client()
            await client.delete(key)
        except Exception as exc:
            logger.warning("redis.delete_failed", key=key, error=str(exc))

    async def exists(self, key: str) -> bool:
        try:
            client = await self._get_client()
            return bool(await client.exists(key))
        except Exception:
            return False

    async def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()
            self._client = None


# ── Factory ───────────────────────────────────────────────────────────────────


def create_cache_backend(redis_url: str | None) -> CacheBackend:
    """
    Cria o backend de cache apropriado baseado na configuração.

    redis_url=None → InMemoryCache (dev / sem Redis)
    redis_url set  → RedisCache (produção)
    """
    if redis_url:
        logger.info("cache.backend.redis", url=str(redis_url)[:30] + "...")
        return RedisCache(str(redis_url))
    else:
        logger.info("cache.backend.memory", reason="REDIS_URL not set")
        return InMemoryCache()
