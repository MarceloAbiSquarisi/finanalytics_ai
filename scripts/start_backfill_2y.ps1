# Inicia (ou reinicia) o backfill 2y WIN/WDO dentro do container API.
# Idempotente: skip se ja' rodando.
#
# Usado:
#   - Manualmente pra disparar Fase A
#   - Pelo run_daily_backfill.ps1 (Fase C, retomar apos daily)

param(
    [switch]$Force  # mata processo existente e relanca
)

$ErrorActionPreference = 'Continue'

# Verifica se ja' tem processo bf2y rodando
docker exec finanalytics_api sh -c "ps -ef | grep 'python /tmp/bf2y' | grep -v grep > /data/_bf2y_check.txt"
Start-Sleep 1
$existing = Get-Content E:\finanalytics_data\_bf2y_check.txt -Raw -ErrorAction SilentlyContinue

if ($existing -and $existing.Trim() -ne "") {
    if ($Force) {
        Write-Host "Backfill 2y rodando — forcando kill..." -ForegroundColor Yellow
        docker exec finanalytics_api sh -c "pkill -f bf2y.py"
        Start-Sleep 3
    } else {
        Write-Host "Backfill 2y JA' rodando (pular start). Use -Force pra reiniciar." -ForegroundColor Yellow
        $existing.Trim() | Out-Host
        exit 0
    }
}

# Copia script atualizado (idempotente)
docker cp D:\Projetos\finanalytics_ai_fresh\scripts\backfill_2y_futures.py finanalytics_api:/tmp/bf2y.py
Write-Host "Script copiado pro container." -ForegroundColor Cyan

# Dispatch detached
docker exec -d `
    -e PROFIT_AGENT_URL=http://host.docker.internal:8002 `
    -e PROFIT_TIMESCALE_DSN=postgresql://finanalytics:timescale_secret@timescale:5432/market_data `
    finanalytics_api sh -c "python /tmp/bf2y.py > /data/backfill_2y.log 2> /data/backfill_2y.err"

Write-Host "Backfill 2y dispatched at $(Get-Date -Format 'HH:mm:ss')." -ForegroundColor Green
Start-Sleep 4

# Confirma processo subiu
docker exec finanalytics_api sh -c "ps -ef | grep 'python /tmp/bf2y' | grep -v grep | head -1 > /data/_bf2y_after.txt"
Start-Sleep 1
$after = Get-Content E:\finanalytics_data\_bf2y_after.txt -Raw -ErrorAction SilentlyContinue
if ($after -and $after.Trim() -ne "") {
    Write-Host "OK: $($after.Trim())" -ForegroundColor Green
} else {
    Write-Host "AVISO: processo nao detectado (pode ainda estar bootando)" -ForegroundColor Yellow
}
