#!/bin/sh
# ──────────────────────────────────────────────────────────────────────────────
# docker-entrypoint-worker.sh — Entrypoint do worker de eventos
#
# Responsabilidades:
#   1. Aguardar PostgreSQL estar pronto (sem alembic — migrations são da API)
#   2. Iniciar o worker de processamento de eventos (main.py)
#
# Design decision: o worker NÃO roda migrations.
# Garantia de ordem é feita pelo depends_on do docker-compose:
#   worker depends_on api (service_started) → api já rodou alembic upgrade head
# ──────────────────────────────────────────────────────────────────────────────
set -e

echo "[worker] Iniciando worker de eventos FinAnalytics AI..."

# ── Aguardar banco ─────────────────────────────────────────────────────────────
MAX_RETRIES=${DB_WAIT_RETRIES:-20}
RETRY=0

until python -c "
import asyncio, asyncpg, os, sys

async def check():
    try:
        url = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', '')
        conn = await asyncpg.connect('postgresql://' + url)
        await conn.close()
    except Exception as e:
        print(f'[worker] DB não disponível: {e}', flush=True)
        sys.exit(1)

asyncio.run(check())
" 2>/dev/null; do
    RETRY=$((RETRY + 1))
    if [ "$RETRY" -ge "$MAX_RETRIES" ]; then
        echo "[worker] ERRO: banco não respondeu após ${MAX_RETRIES} tentativas. Abortando."
        exit 1
    fi
    echo "[worker] Aguardando banco... tentativa ${RETRY}/${MAX_RETRIES}"
    sleep 2
done

echo "[worker] Banco disponível. Iniciando processador de eventos..."

# ── Iniciar worker ─────────────────────────────────────────────────────────────
# exec garante que sinais (SIGTERM/SIGINT) cheguem diretamente ao Python
# O main.py implementa shutdown graceful via asyncio CancelledError
exec python -m finanalytics_ai.main
