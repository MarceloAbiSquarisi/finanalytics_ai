#!/bin/sh
# docker-backup.sh — pg_dump diário para PostgreSQL + TimescaleDB
#
# Executa às 02:00 BRT (05:00 UTC) via cron dentro do container backup.
# Salva em /backups/YYYY-MM-DD/ com retenção de 7 dias.
# Variáveis injetadas pelo docker-compose: PG_*, TS_*

set -e

DATE=$(date +%Y-%m-%d)
HOUR=$(date +%H:%M)
BACKUP_DIR="/backups/${DATE}"
mkdir -p "${BACKUP_DIR}"

log() { echo "[$(date +%Y-%m-%dT%H:%M:%S)] $1"; }

# ── PostgreSQL (OLTP) ─────────────────────────────────────────────────────────
log "Iniciando backup PostgreSQL → ${BACKUP_DIR}/postgres_finanalytics.sql"
PGPASSWORD="${PG_PASSWORD}" pg_dump \
    -h "${PG_HOST}" \
    -U "${PG_USER}" \
    -d "${PG_DB}" \
    --no-password \
    --format=plain \
    --encoding=UTF8 \
    > "${BACKUP_DIR}/postgres_finanalytics.sql"

PG_SIZE=$(du -sh "${BACKUP_DIR}/postgres_finanalytics.sql" | cut -f1)
log "PostgreSQL OK — ${PG_SIZE}"

# ── TimescaleDB (séries temporais) ────────────────────────────────────────────
log "Iniciando backup TimescaleDB → ${BACKUP_DIR}/timescale_market_data.sql"
PGPASSWORD="${TS_PASSWORD}" pg_dump \
    -h "${TS_HOST}" \
    -U "${TS_USER}" \
    -d "${TS_DB}" \
    --no-password \
    --format=plain \
    --encoding=UTF8 \
    > "${BACKUP_DIR}/timescale_market_data.sql"

TS_SIZE=$(du -sh "${BACKUP_DIR}/timescale_market_data.sql" | cut -f1)
log "TimescaleDB OK — ${TS_SIZE}"

# ── Retenção: remove backups com mais de 7 dias ───────────────────────────────
log "Verificando retenção (7 dias)..."
find /backups -maxdepth 1 -type d -name "????-??-??" | sort | head -n -7 | while read OLD_DIR; do
    log "Removendo backup antigo: ${OLD_DIR}"
    rm -rf "${OLD_DIR}"
done

# ── Relatório final ───────────────────────────────────────────────────────────
TOTAL=$(du -sh "${BACKUP_DIR}" | cut -f1)
log "Backup concluído — ${DATE} ${HOUR} — Total: ${TOTAL}"
log "Arquivos:"
ls -lh "${BACKUP_DIR}"
