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