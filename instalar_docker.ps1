$Projeto = "D:\Projetos\finanalytics_ai"

# ── Dockerfile ────────────────────────────────────────────────────────────────
@'
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
'@ | Set-Content "$Projeto\Dockerfile" -Encoding UTF8

Write-Host "  [ok] Dockerfile" -ForegroundColor Green

# ── docker-entrypoint.sh ─────────────────────────────────────────────────────
# Nota: salvo com LF (Unix) obrigatório para rodar dentro do container Linux
$entrypoint = @'
#!/bin/sh
set -e

echo "[entrypoint] Aguardando banco ficar disponivel..."

MAX_RETRIES=15
RETRY=0
until python -c "
import asyncio, asyncpg, os, sys
async def check():
    try:
        url = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', '')
        conn = await asyncpg.connect('postgresql://' + url)
        await conn.close()
    except Exception:
        sys.exit(1)
asyncio.run(check())
" 2>/dev/null; do
    RETRY=$((RETRY + 1))
    if [ "$RETRY" -ge "$MAX_RETRIES" ]; then
        echo "[entrypoint] ERRO: banco nao respondeu. Abortando."
        exit 1
    fi
    echo "[entrypoint] Tentativa ${RETRY}/${MAX_RETRIES}. Aguardando 2s..."
    sleep 2
done

echo "[entrypoint] Banco disponivel. Rodando migrations..."
python -m alembic upgrade head

echo "[entrypoint] Iniciando servidor..."
exec uvicorn finanalytics_ai.interfaces.api.run:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --loop uvloop \
    --http httptools
'@

# Salva com line endings Unix (LF) — obrigatorio para scripts shell no Linux
$entrypoint = $entrypoint -replace "`r`n", "`n"
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText("$Projeto\docker-entrypoint.sh", $entrypoint, $utf8NoBom)

Write-Host "  [ok] docker-entrypoint.sh (LF)" -ForegroundColor Green

Write-Host ""
Write-Host "=== Pronto! Agora rode: ===" -ForegroundColor Cyan
Write-Host "  docker compose build api"
Write-Host "  docker compose up -d api"
Write-Host "  docker compose logs -f api"
