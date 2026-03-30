"""
Implementacao do IdempotencyStore usando Redis.

Operacao atomica: SET key NX EX ttl
- NX: only set if Not eXists
- EX: expiry em segundos

Retorna True se a chave JA EXISTIA (evento ja processado),
False se era nova (acabou de ser registrada).

Decisao: Redis em vez de banco relacional para idempotencia.
Motivo:
1. Redis SET NX EX e atomico por design — sem race condition
2. TTL automatico — sem job de limpeza necessario
3. Latencia muito menor que banco para operacoes de check simples
4. O banco relacional ainda armazena o estado completo do evento
   (auditoria, reprocessamento)

Trade-off: Redis e um ponto de falha adicional. Se o Redis cair,
o sistema pode reprocessar eventos (comportamento seguro se as
regras de negocio forem idempotentes). Para sistemas onde o reprocessamento
e catastrofico, seria necessario fallback para banco.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = structlog.get_logger(__name__)


class RedisIdempotencyStore:
    """
    IdempotencyStore backed by Redis.

    redis_client: instancia de redis.asyncio.Redis ja configurada
    (reutiliza o cliente existente do projeto).
    """

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client

    async def check_and_set(self, key: str, ttl_seconds: int) -> bool:
        """
        Retorna True se a chave JA EXISTIA (evento ja processado).
        Retorna False e seta a chave se era nova.
        """
        # SET key "1" NX EX ttl_seconds
        # Retorna None se a chave ja existia (NX falhou)
        # Retorna True se foi setada com sucesso
        result = await self._redis.set(key, "1", nx=True, ex=ttl_seconds)
        already_existed = result is None
        logger.debug(
            "idempotency.check",
            key=key,
            already_existed=already_existed,
        )
        return already_existed

    async def release(self, key: str) -> None:
        """Remove a chave para permitir retry."""
        await self._redis.delete(key)
        logger.debug("idempotency.released", key=key)


class InMemoryIdempotencyStore:
    """
    Implementacao in-memory para testes e desenvolvimento local.
    NAO usar em producao (sem TTL real, sem distribuicao).
    """

    def __init__(self) -> None:
        self._store: dict[str, bool] = {}

    async def check_and_set(self, key: str, ttl_seconds: int) -> bool:
        if key in self._store:
            return True
        self._store[key] = True
        return False

    async def release(self, key: str) -> None:
        self._store.pop(key, None)

