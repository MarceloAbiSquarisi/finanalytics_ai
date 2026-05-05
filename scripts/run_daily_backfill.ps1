# Wrapper invocado pelo Task Scheduler.
# Copia script atualizado pro container e executa, redirecionando output
# pra logs/daily_backfill/<date>.log

$ErrorActionPreference = 'Continue'
$logDir = 'D:\Projetos\finanalytics_ai_fresh\logs\daily_backfill'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logFile = Join-Path $logDir ("daily_backfill_$(Get-Date -Format 'yyyyMMdd_HHmmss').log")

# Copia script atualizado pro container (idempotente)
docker cp D:\Projetos\finanalytics_ai_fresh\scripts\backfill_today_subscribed.py finanalytics_api:/tmp/bft.py 2>&1

# Roda dentro do container (stdout vai pro arquivo no /data, copia depois)
docker exec `
    -e PROFIT_AGENT_URL=http://host.docker.internal:8002 `
    -e PROFIT_TIMESCALE_DSN=postgresql://finanalytics:timescale_secret@timescale:5432/market_data `
    finanalytics_api sh -c "python /tmp/bft.py > /data/_daily_bft.log 2>&1"

# Copia log pra disco local pra retencao
if (Test-Path E:\finanalytics_data\_daily_bft.log) {
    Copy-Item E:\finanalytics_data\_daily_bft.log $logFile -Force
}
"COMPLETED at $(Get-Date)" | Out-File $logFile -Append
