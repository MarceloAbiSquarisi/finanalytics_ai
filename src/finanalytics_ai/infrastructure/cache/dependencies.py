"""
finanalytics_ai.infrastructure.cache.dependencies
───────────────────────────────────────────────────
Dependências FastAPI e decorators para cache e rate limiting.

Uso nas rotas:

  # Rate limiting simples:
  @router.post("/run")
  async def run(request: Request, _rl=Depends(rate_limit(limit=20, window=60))):
      ...

  # Cache automático em route handler:
  @router.get("/run")
  @cached_route(ttl=300, prefix="screener")
  async def run_get(request: Request, tickers: str = Query(...)):
      ...

Design:

  rate_limit() retorna uma FastAPI Dependency que:
    1. Extrai o IP do cliente (X-Forwarded-For ou host)
    2. Constrói a chave: "ip:<ip>:<rota>"
    3. Chama o rate limiter
    4. Adiciona headers RateLimit-* na resposta
    5. Levanta HTTPException 429 se excedido

  get_cache() / get_rate_limiter():
    Acessam os backends via app.state injetado no startup do app.py.
    Fallback para InMemory se state não tiver os backends.

  cached_route():
    Decorator que:
      1. Serializa os query params + body em JSON
      2. Gera cache key via make_cache_key()
      3. Tenta buscar do cache (hit → retorna JSONResponse diretamente)
      4. Executa o handler original
      5. Armazena resultado no cache
    Importante: só cacheia respostas 200 OK.
    O decorator preserva assinatura para FastAPI via functools.wraps.
"""
from __future__ import annotations

import functools
import json
from typing import Any, Callable

import structlog
from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from finanalytics_ai.infrastructure.cache.backend import (
    CacheBackend,
    InMemoryCache,
    make_cache_key,
)
from finanalytics_ai.infrastructure.cache.rate_limiter import (
    InMemoryRateLimiter,
    RateLimiterBackend,
)
from finanalytics_ai.metrics import (
    http_requests_total,  # reutiliza métricas já existentes
)

logger = structlog.get_logger(__name__)

# ── Singletons de fallback (usados se app.state não tiver os backends) ─────────
_fallback_cache: InMemoryCache | None = None
_fallback_limiter: InMemoryRateLimiter | None = None


def _get_fallback_cache() -> InMemoryCache:
    global _fallback_cache
    if _fallback_cache is None:
        _fallback_cache = InMemoryCache()
    return _fallback_cache


def _get_fallback_limiter() -> InMemoryRateLimiter:
    global _fallback_limiter
    if _fallback_limiter is None:
        _fallback_limiter = InMemoryRateLimiter()
    return _fallback_limiter


# ── Accessors de state ────────────────────────────────────────────────────────

def get_cache(request: Request) -> CacheBackend:
    return getattr(request.app.state, "cache_backend", None) or _get_fallback_cache()


def get_rate_limiter(request: Request) -> RateLimiterBackend:
    return getattr(request.app.state, "rate_limiter", None) or _get_fallback_limiter()


def _client_ip(request: Request) -> str:
    """Extrai IP real considerando proxies reversos."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ── Rate limit dependency ─────────────────────────────────────────────────────

def rate_limit(limit: int = 60, window: int = 60) -> Callable:
    """
    FastAPI Dependency Factory para rate limiting.

    Parâmetros:
      limit:  número máximo de requests na janela
      window: tamanho da janela em segundos

    Uso:
      @router.post("/scan")
      async def scan(request: Request, _=Depends(rate_limit(limit=10, window=60))):
    """
    async def _dependency(request: Request, response: Response) -> None:
        limiter = get_rate_limiter(request)
        ip      = _client_ip(request)
        route   = request.url.path.replace("/api/v1/", "").replace("/", "_")
        key     = f"ip:{ip}:{route}"

        result = await limiter.check(key, limit=limit, window=window)

        # Sempre adiciona headers informativos
        for header, value in result.headers().items():
            response.headers[header] = value

        if not result.allowed:
            raise HTTPException(
                status_code=429,
                detail={
                    "error":       "RATE_LIMIT_EXCEEDED",
                    "message":     f"Limite de {limit} requests por {window}s excedido.",
                    "retry_after": result.retry_after,
                },
                headers=result.headers(),
            )

    return _dependency


# ── Cache decorator ───────────────────────────────────────────────────────────

def cached_route(
    ttl: int = 300,
    prefix: str = "route",
    include_body: bool = False,
) -> Callable:
    """
    Decorator de cache para route handlers FastAPI.

    Gera cache key a partir dos query params + path params.
    Se include_body=True, também inclui o request body (para POST).

    O decorator NÃO altera a assinatura do handler — FastAPI
    continua enxergando os parâmetros para documentação Swagger.

    Cache miss → executa handler → armazena se status 200.
    Cache hit  → retorna JSONResponse do cache, bypassa handler.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Localiza o Request nos kwargs
            request: Request | None = kwargs.get("request")
            if request is None:
                for arg in args:
                    if isinstance(arg, Request):
                        request = arg
                        break

            if request is None:
                # Sem request, não podemos cachear — executa direto
                return await func(*args, **kwargs)

            cache = get_cache(request)

            # Monta params para a chave
            params: dict[str, Any] = dict(request.query_params)
            params.update(request.path_params)

            if include_body:
                try:
                    body = await request.body()
                    if body:
                        params["__body__"] = body.decode("utf-8", errors="replace")
                except Exception:
                    pass

            cache_key = make_cache_key(prefix, params)

            # Tenta hit de cache
            cached_value = await cache.get(cache_key)
            if cached_value is not None:
                logger.debug("cache.hit", key=cache_key, prefix=prefix)
                try:
                    data = json.loads(cached_value)
                    return JSONResponse(
                        content=data,
                        headers={"X-Cache": "HIT", "X-Cache-TTL": str(ttl)},
                    )
                except json.JSONDecodeError:
                    pass  # cache corrompido — re-executa

            logger.debug("cache.miss", key=cache_key, prefix=prefix)

            # Executa handler original
            result = await func(*args, **kwargs)

            # Armazena no cache (só se retornou dict/list serializável)
            if isinstance(result, (dict, list)):
                try:
                    serialized = json.dumps(result)
                    await cache.set(cache_key, serialized, ttl=ttl)
                except (TypeError, ValueError) as exc:
                    logger.warning("cache.serialize_failed", error=str(exc))

            return result

        return wrapper
    return decorator
