"""startup/cache.py — Cache Redis e Rate Limiter."""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


def init_cache(app, settings) -> None:
    from finanalytics_ai.infrastructure.cache.backend import create_cache_backend
    from finanalytics_ai.infrastructure.cache.rate_limiter import create_rate_limiter

    redis_url = str(settings.redis_url) if settings.redis_url else None
    app.state.cache_backend = create_cache_backend(redis_url)
    app.state.rate_limiter = create_rate_limiter(redis_url)
    log.info("cache.ready", backend=type(app.state.cache_backend).__name__)
    log.info("rate_limiter.ready", backend=type(app.state.rate_limiter).__name__)
