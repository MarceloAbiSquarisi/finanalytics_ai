# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install uv for fast dependency resolution
RUN pip install uv

# ── Builder stage ─────────────────────────────────────────────────────────────
FROM base AS builder
COPY pyproject.toml ./
COPY src/ ./src/
RUN uv pip install --system -e .

# ── Final stage ───────────────────────────────────────────────────────────────
FROM base AS final
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY src/ ./src/

# Non-root user para segurança
RUN adduser --disabled-password --gecos '' appuser
USER appuser

EXPOSE 9090

CMD ["python", "-m", "finanalytics_ai.main"]
