# install_profit_watchdog.ps1
# Instala o watchdog do profit_agent como tarefa agendada do Windows.
#
# REQUER: executar como Administrador
# Instalar  : powershell -ExecutionPolicy Bypass -File install_profit_watchdog.ps1
# Desinstalar: powershell -ExecutionPolicy Bypass -File install_profit_watchdog.ps1 -Uninstall
# Status    : powershell -ExecutionPolicy Bypass -File install_profit_watchdog.ps1 -Status

param(
    [switch]$Uninstall,
    [switch]$Status,
    [string]$ProjectDir  = "D:\Projetos\finanalytics_ai_fresh",
    [string]$LogDir      = "D:\Logs\finanalytics",
    [int]$IntervalSec    = 60,
    [int]$AgentPort      = 8001,
    [int]$MaxRestarts    = 5
)

$TaskName    = "FinAnalytics-ProfitAgent-Watchdog"
$ScriptPath  = "$ProjectDir\scripts\watchdog_profit_agent.ps1"
$WrapperPath = "$ProjectDir\scripts\watchdog_profit_agent_runner.ps1"

# -- Status --------------------------------------------------------------------
if ($Status) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Write-Host "Tarefa    : $TaskName"
        Write-Host "Estado    : $($task.State)"
        $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($info) {
            Write-Host "Ultima ex : $($info.LastRunTime)"
            Write-Host "Resultado : $($info.LastTaskResult)"
            Write-Host "Proxima   : $($info.NextRunTime)"
        }
        Write-Host ""
        Write-Host "Ultimas 20 linhas do log:"
        $logFile = "$LogDir\profit_watchdog_$(Get-Date -Format 'yyyy-MM').log"
        if (Test-Path $logFile) {
            Get-Content $logFile | Select-Object -Last 20
        } else {
            Write-Host "(log ainda nao criado)"
        }
    } else {
        Write-Host "Tarefa '$TaskName' nao encontrada." -ForegroundColor Yellow
    }
    exit 0
}

# -- Verificar admin -----------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) {
    Write-Error "Execute como Administrador (botao direito na janela do PowerShell ou terminal)."
    exit 1
}

# -- Desinstalar ---------------------------------------------------------------
if ($Uninstall) {
    Write-Host "Removendo tarefa '$TaskName'..."
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "[OK] Tarefa removida." -ForegroundColor Green
    exit 0
}

# -- Validar -------------------------------------------------------------------
if (-not (Test-Path $ScriptPath)) {
    Write-Error "Watchdog nao encontrado: $ScriptPath"
    Write-Error "Execute primeiro: finanalytics_create_profit_watchdog.ps1"
    exit 1
}

# -- Criar log dir -------------------------------------------------------------
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# -- Criar wrapper -------------------------------------------------------------
$wrapperLines = @(
    "# Auto-gerado por install_profit_watchdog.ps1 - nao editar manualmente",
    "Set-Location `"$ProjectDir`"",
    "`$env:PROFIT_WATCHDOG_INTERVAL_SEC  = `"$IntervalSec`"",
    "`$env:PROFIT_WATCHDOG_LOG_DIR       = `"$LogDir`"",
    "`$env:PROFIT_WATCHDOG_PROJECT_DIR   = `"$ProjectDir`"",
    "`$env:PROFIT_WATCHDOG_PORT          = `"$AgentPort`"",
    "`$env:PROFIT_WATCHDOG_MAX_RESTARTS  = `"$MaxRestarts`"",
    "& `"$ScriptPath`""
)
$wrapperContent = $wrapperLines -join "`n"
[System.IO.File]::WriteAllText($WrapperPath, $wrapperContent, [System.Text.Encoding]::UTF8)
Write-Host "Wrapper criado: $WrapperPath"

# -- Registrar tarefa ----------------------------------------------------------
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$WrapperPath`""

# Trigger: ao fazer login
$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable:$false `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

# Remove tarefa anterior se existir
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Monitora o profit_agent (Nelogica) na porta $AgentPort. Reinicia automaticamente se parado." `
    | Out-Null

Write-Host ""
Write-Host "==========================================="
Write-Host " Profit Agent Watchdog instalado!"
Write-Host "==========================================="
Write-Host " Tarefa   : $TaskName"
Write-Host " Script   : $ScriptPath"
Write-Host " Logs     : $LogDir\profit_watchdog_YYYY-MM.log"
Write-Host " Porta    : $AgentPort"
Write-Host " Intervalo: ${IntervalSec}s"
Write-Host " Ativacao : ao fazer login"
Write-Host ""
Write-Host "Iniciando agora para teste..."
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3
$taskInfo = Get-ScheduledTask -TaskName $TaskName
Write-Host " Estado   : $($taskInfo.State)"
Write-Host ""
Write-Host "Comandos uteis:"
Write-Host "  Status  : powershell -ExecutionPolicy Bypass -File `"$ProjectDir\scripts\install_profit_watchdog.ps1`" -Status"
Write-Host "  Parar   : Stop-ScheduledTask -TaskName '$TaskName'"
Write-Host "  Iniciar : Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "  Remover : powershell -ExecutionPolicy Bypass -File `"$ProjectDir\scripts\install_profit_watchdog.ps1`" -Uninstall"
Write-Host "  Logs    : Get-Content '$LogDir\profit_watchdog_$(Get-Date -Format 'yyyy-MM').log' -Tail 30 -Wait"
Write-Host "  Teste   : powershell -ExecutionPolicy Bypass -File `"$ScriptPath`" -RunOnce"