#!/bin/sh
# docker-entrypoint-worker-v2.sh
# Entry point do Event Worker V2.
#
# Sem wait-for-it: o worker tolera banco indisponivel no startup.
# SqlEventRepository lanca DatabaseError capturado no loop principal.
set -e

echo "[worker-v2] Iniciando Event Worker V2..."
echo "[worker-v2] ENVIRONMENT=${ENVIRONMENT:-production}"
echo "[worker-v2] LOG_FORMAT=${LOG_FORMAT:-json}"

exec python -m finanalytics_ai.workers.event_worker_v2