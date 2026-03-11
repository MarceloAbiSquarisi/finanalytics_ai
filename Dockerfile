# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 `
    PYTHONUNBUFFERED=1 `
    PIP_NO_CACHE_DIR=1 `
    PYTHONPATH=/app/src

WORKDIR /app

FROM base AS builder

RUN pip install uv==0.4.29

COPY pyproject.toml ./
RUN mkdir -p src/finanalytics_ai && touch src/finanalytics_ai/__init__.py && touch README.md

RUN uv pip install --system `
    "fastapi>=0.111" `
    "uvicorn[standard]>=0.30" `
    "uvloop>=0.19" `
    "python-jose[cryptography]>=3.3" `
    "passlib[bcrypt]>=1.7" `
    "bcrypt==4.0.1" `
    "httptools>=0.6" `
    "sqlalchemy[asyncio]>=2.0" `
    "asyncpg>=0.29" `
    "alembic>=1.13" `
    "pydantic>=2.7" `
    "pydantic-settings>=2.2" `
    "structlog>=24.1" `
    "httpx>=0.27" `
    "tenacity>=8.3" `
    "redis>=5.0" `
    "reportlab>=4.0" `
    "python-dotenv>=1.0" `
    "prometheus-client>=0.20" `
    "opentelemetry-api>=1.24" `
    "opentelemetry-sdk>=1.24" `
    "numpy>=1.26" `
    "pandas>=2.2" `
    "scipy>=1.13"

COPY src/ ./src/
RUN uv pip install --system -e . --no-deps

FROM base AS final

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/uvicorn  /usr/local/bin/uvicorn
COPY --from=builder /usr/local/bin/alembic  /usr/local/bin/alembic
COPY --from=builder /app/src ./src

COPY alembic/    ./alembic/
COPY alembic.ini ./alembic.ini
COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x ./docker-entrypoint.sh

RUN adduser --disabled-password --gecos "" --uid 1001 appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8000/health\")" \
    || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]
