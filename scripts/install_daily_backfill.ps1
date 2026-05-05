# Instala Scheduled Task: backfill diario de tickers subscritos.
#
# Roda 21:00 BRT diariamente (3h apos fechamento do mercado as 18h).
# Executa scripts/backfill_today_subscribed.py DENTRO do container API
# (psycopg2 estavel + rede docker pra timescale, agent via host.docker.internal).
#
# Uso:
#   .\scripts\install_daily_backfill.ps1
#   .\scripts\install_daily_backfill.ps1 -RemoveOnly
#   .\scripts\install_daily_backfill.ps1 -RunNow  # disparar uma vez agora pra testar

param(
    [switch]$RemoveOnly,
    [switch]$RunNow
)

$ErrorActionPreference = 'Stop'

$taskName = 'FinAnalytics-DailyBackfillSubscribed'
$logsDir = 'D:\Projetos\finanalytics_ai_fresh\logs\daily_backfill'
$wrapper = 'D:\Projetos\finanalytics_ai_fresh\scripts\run_daily_backfill.ps1'

# Cria wrapper PS que copia script atualizado + roda no container API
$wrapperContent = @'
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
'@
$wrapperContent | Out-File -FilePath $wrapper -Encoding utf8 -Force
Write-Host "Wrapper escrito: $wrapper" -ForegroundColor Green

# Remove existente (idempotente)
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Task antiga removida" -ForegroundColor Yellow
}

if ($RemoveOnly) {
    Write-Host "Removido. Saindo." -ForegroundColor Cyan
    exit 0
}

# Action: invoca o wrapper PS
$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$wrapper`""

# Trigger diario 21:00
$trigger = New-ScheduledTaskTrigger -Daily -At 9:00pm

# Settings: rodar mesmo se atrasou, NAO em bateria, max 4h
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)

# Registra (sem -Principal = roda como user atual; SDDL ja permite docker)
Register-ScheduledTask `
    -TaskName $taskName `
    -Description 'Backfill diario do ultimo pregao para todos os tickers subscritos. Roda apos fechamento (21h).' `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings | Out-Null

Get-ScheduledTask -TaskName $taskName |
    Select TaskName, State, @{N='NextRun';E={(Get-ScheduledTaskInfo $_).NextRunTime}} |
    Format-List

if ($RunNow) {
    Write-Host "`nDisparando run inicial pra testar..." -ForegroundColor Cyan
    Start-ScheduledTask -TaskName $taskName
    Start-Sleep 3
    Get-ScheduledTaskInfo -TaskName $taskName | Select LastRunTime, LastTaskResult | Format-List
    Write-Host "Acompanhe o log em $logsDir" -ForegroundColor Yellow
}
