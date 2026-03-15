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

echo "[entrypoint] Banco disponivel. Verificando estado das migrations..."

# ── Garante que alembic_version existe e está sincronizada ────────────────────
#
# Problema: quando o banco já tem todas as tabelas mas alembic_version não
# existe (banco criado antes do Alembic ou volume reutilizado de outro projeto),
# o Alembic tenta rodar a migration baseline do zero e falha com DuplicateTable.
#
# Solução: inspeciona o banco e decide automaticamente o que fazer:
#   1. Se alembic_version não existe + tabelas existem → stamp no head
#   2. Se alembic_version existe com versões duplicadas  → limpa, mantém head
#   3. Se alembic_version existe e está ok              → deixa upgrade rodar
#   4. Banco vazio                                       → deixa upgrade criar tudo
# ─────────────────────────────────────────────────────────────────────────────
python - << 'PYEOF'
import asyncio
import asyncpg
import os

HEAD_REVISION = "0002_portfolio_multi"
SENTINEL_TABLE = "portfolios"

async def ensure_alembic_version():
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        alembic_exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'alembic_version'
            )
        """)

        if alembic_exists:
            rows = await conn.fetch("SELECT version_num FROM alembic_version")
            versions = [r["version_num"] for r in rows]
            print(f"[entrypoint] alembic_version: {versions}")
            if len(versions) > 1:
                print("[entrypoint] Multiplas versoes — limpando e definindo head...")
                await conn.execute("DELETE FROM alembic_version")
                await conn.execute(
                    "INSERT INTO alembic_version (version_num) VALUES ($1)", HEAD_REVISION
                )
                print(f"[entrypoint] Stamp -> {HEAD_REVISION}")
            return

        sentinel_exists = await conn.fetchval(f"""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = '{SENTINEL_TABLE}'
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS alembic_version (
                version_num VARCHAR(32) NOT NULL,
                CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
            )
        """)

        if sentinel_exists:
            await conn.execute(
                "INSERT INTO alembic_version (version_num) VALUES ($1) ON CONFLICT DO NOTHING",
                HEAD_REVISION
            )
            print(f"[entrypoint] Banco pre-existente detectado. Stamp -> {HEAD_REVISION}")
        else:
            print("[entrypoint] Banco vazio. Upgrade vai criar tudo.")
    finally:
        await conn.close()

asyncio.run(ensure_alembic_version())
PYEOF

echo "[entrypoint] Rodando migrations..."
python -m alembic upgrade head

echo "[entrypoint] Iniciando servidor..."
exec uvicorn finanalytics_ai.interfaces.api.run:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --loop uvloop \
    --http httptools