# Orquestrador: roda backfill_today_subscribed -> em seguida backfill_winwdo extendido.
# Saida vai pros 2 logs que o backfill_today_dashboard.ps1 esta lendo.
#
# Ambos scripts rodam DENTRO do container finanalytics_api (que ja tem
# host.docker.internal -> :8002 do agent + DSN configurado).

$ErrorActionPreference = 'Continue'
$logToday  = '/data/backfill_today_subscribed.log'
$logWinWdo = '/data/backfill_winwdo_extended.log'

# Limpa logs antigos pra dashboard nao misturar runs.
docker exec finanalytics_api sh -c "rm -f $logToday $logWinWdo; touch $logToday $logWinWdo"

Write-Host "[1/2] Disparando backfill_today_subscribed.py..." -ForegroundColor Cyan
docker exec -d finanalytics_api sh -c "cd /app && python /app/scripts/backfill_today_subscribed.py > $logToday 2>&1"

# Aguarda task 1 terminar (DONE no log)
Write-Host "[1/2] Aguardando DONE em $logToday..." -ForegroundColor DarkGray
while ($true) {
    Start-Sleep -Seconds 10
    $done = docker exec finanalytics_api sh -c "grep -c '^DONE ' $logToday 2>/dev/null || echo 0"
    if ([int]$done -ge 1) { break }
}
Write-Host "[1/2] Concluido." -ForegroundColor Green

Write-Host "[2/2] Disparando backfill_2y_futures.py (2020-01-02 -> 2026-05-05)..." -ForegroundColor Cyan
docker exec -d finanalytics_api sh -c "cd /app && python /app/scripts/backfill_2y_futures.py --tickers WINFUT WDOFUT --start 2020-01-02 --end 2026-05-05 > $logWinWdo 2>&1"

Write-Host "[2/2] Em curso. Acompanhe pelo dashboard." -ForegroundColor Green
Write-Host ""
Write-Host "Dashboard: pwsh .\scripts\backfill_today_dashboard.ps1" -ForegroundColor Yellow
