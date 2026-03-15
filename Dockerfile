# syntax=docker/dockerfile:1
# ──────────────────────────────────────────────────────────────────────────────
# FinAnalytics AI — Dockerfile multi-stage
#
# Stages:
#   base    → imagem base compartilhada (Python 3.12-slim + env vars)
#   builder → instala dependências via uv (cache separado do código)
#   api     → imagem final do servidor FastAPI + Alembic
#   worker  → imagem final do worker de eventos (main.py)
#
# Build:
#   docker build --target api    -t finanalytics-ai:latest .
#   docker build --target worker -t finanalytics-worker:latest .
#
# Design decision: dois targets finais (api / worker) a partir do mesmo
# builder. Isso garante que API e worker usam exatamente as mesmas versões
# de dependências sem duplicar a camada de instalação.
# ──────────────────────────────────────────────────────────────────────────────

# ── base: imagem mínima compartilhada ─────────────────────────────────────────
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# ── builder: instala dependências ─────────────────────────────────────────────
FROM base AS builder

# uv: gerenciador de pacotes rápido (substitui pip install diretamente)
RUN pip install uv==0.4.29

# Copiar apenas manifesto primeiro — camada de deps é cacheada separadamente
# do código. Rebuild de src/ não reinstala deps.
COPY pyproject.toml ./
RUN touch README.md

# Stub mínimo para que `uv pip install -e .` resolva o package local
RUN mkdir -p src/finanalytics_ai \
    && touch src/finanalytics_ai/__init__.py

# Instalar deps de produção diretamente do pyproject.toml (sem lista hardcoded)
# --no-dev: exclui [dev] extras (ruff, mypy, pytest não vão para produção)
RUN uv pip install --system -e . \
    && uv pip install --system \
        "uvicorn[standard]>=0.30.0" \
        "uvloop>=0.19.0" \
        "httptools>=0.6.0" \
        "pyarrow>=16.0.0"

# ── Dependências de Forecast (Prophet + PyTorch + PyTorch-Forecasting) ────────
# Instaladas em camada separada para melhor cache — só rebuildam se esta linha mudar
RUN uv pip install --system \
        "prophet>=1.1.5" \
        "torch>=2.2.0" \
        "pytorch-forecasting>=1.0.0" \
    || echo "[WARN] forecast deps parcialmente instaladas — modelos degradarão graciosamente"

# Copiar código-fonte depois das deps (preserva cache de deps no rebuild)
COPY src/ ./src/

# ── api: servidor FastAPI ──────────────────────────────────────────────────────
FROM base AS api

# Copiar site-packages instalados no builder
COPY --from=builder /usr/local/lib/python3.12/site-packages \
                    /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/uvicorn  /usr/local/bin/uvicorn
COPY --from=builder /usr/local/bin/alembic  /usr/local/bin/alembic
COPY --from=builder /app/src ./src

# Migrations e entrypoint
COPY alembic/              ./alembic/
COPY alembic.ini           ./alembic.ini
COPY docker-entrypoint.sh  ./docker-entrypoint.sh
RUN chmod +x ./docker-entrypoint.sh

# Usuário não-root: UID 1001 para evitar colisão com usuários do host
RUN adduser --disabled-password --gecos "" --uid 1001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Health check: falha rápida (5s) com warm-up generoso (60s para Alembic)
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c \
        "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
    || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]

# ── worker: processador de eventos (main.py) ───────────────────────────────────
FROM base AS worker

COPY --from=builder /usr/local/lib/python3.12/site-packages \
                    /usr/local/lib/python3.12/site-packages
COPY --from=builder /app/src ./src

COPY docker-entrypoint-worker.sh ./docker-entrypoint-worker.sh
RUN chmod +x ./docker-entrypoint-worker.sh

# Worker não precisa de alembic — migrations são responsabilidade da API
RUN adduser --disabled-password --gecos "" --uid 1001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Worker não expõe porta HTTP — apenas métricas Prometheus se habilitadas
# EXPOSE 9091  # descomente se quiser métricas separadas do worker

# Sem health check HTTP — o worker é monitorado via logs e métricas Prometheus.
# Em Kubernetes, use liveness probe com arquivo sentinel (futuro).
HEALTHCHECK NONE

ENTRYPOINT ["./docker-entrypoint-worker.sh"]
