# install_service_taskscheduler.ps1
# Alternativa ao NSSM: usa o Task Scheduler do Windows
# Vantagem: nao precisa instalar nada extra
# Uso: .\install_service_taskscheduler.ps1
# Requer: executar como Administrador

param([string]$Action = "install")  # install | uninstall | status

$ROOT     = "D:\Projetos\finanalytics_ai_fresh"
$TASK     = "FinAnalyticsAI_API"
$LOG_DIR  = "$ROOT\logs"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERRO: Execute como Administrador" -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null

switch ($Action) {

    "install" {
        Write-Host "=== Instalando Task Scheduled: $TASK ===" -ForegroundColor Cyan

        # Wrapper .bat que seta as envs e inicia o servidor
        $batContent = @"
@echo off
cd /d $ROOT
set DATABASE_URL=postgresql+asyncpg://finanalytics:secret@localhost:5432/finanalytics
set ASYNC_DATABASE_URL=postgresql+asyncpg://finanalytics:secret@localhost:5432/finanalytics
set PROFIT_TIMESCALE_DSN=postgresql://finanalytics:timescale_secret@localhost:5433/market_data
"$ROOT\.venv\Scripts\uvicorn.exe" finanalytics_ai.interfaces.api.run:app --host 0.0.0.0 --port 8000 --no-access-log >> "$LOG_DIR\api.log" 2>&1
"@
        $batPath = "$ROOT\run_api_service.bat"
        $batContent | Set-Content $batPath -Encoding ASCII
        Write-Host "  Wrapper criado: $batPath"

        # Remove task anterior
        Unregister-ScheduledTask -TaskName $TASK -Confirm:$false -ErrorAction SilentlyContinue

        # Cria a task
        $taskAction = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$batPath`""
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $settings = New-ScheduledTaskSettingsSet `
            -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
            -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 1) `
            -StartWhenAvailable `
            -RunOnlyIfNetworkAvailable:$false

        $principal = New-ScheduledTaskPrincipal `
            -UserId "SYSTEM" `
            -LogonType ServiceAccount `
            -RunLevel Highest

        Register-ScheduledTask `
            -TaskName $TASK `
            -Action $taskAction `
            -Trigger $trigger `
            -Settings $settings `
            -Principal $principal `
            -Description "FinAnalytics AI - API FastAPI" | Out-Null

        # Inicia agora
        Start-ScheduledTask -TaskName $TASK

        Write-Host "`n=== Task instalada e iniciada! ===" -ForegroundColor Green
        Write-Host "  Dashboard: http://localhost:8000/"
        Write-Host "  Logs:      $LOG_DIR\api.log"
        Write-Host "  Gerenciar: taskschd.msc (busque $TASK)"
    }

    "uninstall" {
        Stop-ScheduledTask  -TaskName $TASK -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TASK -Confirm:$false
        Write-Host "Task removida." -ForegroundColor Green
    }

    "status" {
        Get-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue |
            Select-Object TaskName, State, @{n='LastRun';e={$_.LastRunTime}}, @{n='NextRun';e={$_.NextRunTime}}
    }
}

