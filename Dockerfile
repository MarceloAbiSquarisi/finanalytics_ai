# syntax=docker/dockerfile:1
# ──────────────────────────────────────────────────────────────────────────────
# FinAnalytics AI — Dockerfile multi-stage
#
# Stages:
#   base     → Python slim + variáveis de ambiente
#   builder  → instala dependências com uv (cache de layer separada)
#   final    → imagem mínima para produção (non-root, sem build tools)
# ──────────────────────────────────────────────────────────────────────────────

# ── Base ──────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# ── Builder ───────────────────────────────────────────────────────────────────
FROM base AS builder

RUN pip install uv==0.4.29

# Copia manifestos primeiro → layer de cache isolada das deps
COPY pyproject.toml ./
RUN mkdir -p src/finanalytics_ai && touch src/finanalytics_ai/__init__.py && touch README.md

# Instala dependências de produção
RUN uv pip install --system \
    "fastapi>=0.111" \
    "uvicorn[standard]>=0.30" \
    "uvloop>=0.19" \
    "httptools>=0.6" \
    "sqlalchemy[asyncio]>=2.0" \
    "asyncpg>=0.29" \
    "pydantic>=2.7" \
    "pydantic-settings>=2.2" \
    "structlog>=24.1" \
    "httpx>=0.27" \
    "tenacity>=8.3" \
    "aiokafka>=0.11" \
    "python-dotenv>=1.0" \
    "prometheus-client>=0.20" \
    "opentelemetry-api>=1.24" \
    "opentelemetry-sdk>=1.24" \
    "numpy>=1.26" \
    "pandas>=2.2"

# Instala o pacote local sem reinstalar deps
COPY src/ ./src/
RUN uv pip install --system -e . --no-deps

# ── Final (produção) ──────────────────────────────────────────────────────────
FROM base AS final

COPY --from=builder /usr/local/lib/python3.12/site-packages \
                    /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn
COPY --from=builder /app/src ./src

RUN adduser --disabled-password --gecos '' --uid 1001 appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
    || exit 1

CMD ["uvicorn", "finanalytics_ai.interfaces.api.run:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--loop", "uvloop", "--http", "httptools"]
