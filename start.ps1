# start.ps1 — Sobe toda a stack finanalytics_ai
# Uso: .\start.ps1
# Uso parcial: .\start.ps1 -NoPlatform (sem worker/rv/ingestion/etc)

param([switch]$NoPlatform)

Set-Location $PSScriptRoot

$base  = "docker-compose.yml"
$obs   = "docker-compose.observability.yml"
$plat  = "docker-compose.platform.yml"

Write-Host "Subindo infra base..." -ForegroundColor Cyan
if ($NoPlatform) {
    docker compose -f $base -f $obs up -d
} else {
    docker compose -f $base -f $obs -f $plat up -d
}

Write-Host "`nAguardando health checks..." -ForegroundColor Cyan
Start-Sleep 30

Write-Host "`nStatus dos containers:" -ForegroundColor Cyan
docker ps --format "table {{.Names}}`t{{.Status}}" | Sort-Object
