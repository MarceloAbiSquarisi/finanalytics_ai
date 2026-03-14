# install-watchdog.ps1
# Registra o watchdog como tarefa agendada do Windows (roda em background, reinicia com o sistema).
#
# REQUER: executar como Administrador
# Uso: powershell -ExecutionPolicy Bypass -File install-watchdog.ps1
#
# Para desinstalar: powershell -ExecutionPolicy Bypass -File install-watchdog.ps1 -Uninstall

param(
    [switch]$Uninstall,
    [string]$ProjectDir = "D:\Projetos\finanalytics_ai",
    [string]$LogDir     = "D:\Logs\finanalytics",
    [int]$IntervalSec   = 60
)

$TaskName    = "FinAnalytics-Watchdog"
$ScriptPath  = Join-Path $ProjectDir "scripts\watchdog.ps1"
$WrapperPath = Join-Path $ProjectDir "scripts\watchdog-runner.ps1"

# ── Verificar admin ───────────────────────────────────────────────────────────
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) {
    Write-Error "Execute este script como Administrador (botão direito → Executar como administrador)."
    exit 1
}

# ── Desinstalar ───────────────────────────────────────────────────────────────
if ($Uninstall) {
    Write-Host "Removendo tarefa '$TaskName'..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Tarefa removida."
    exit 0
}

# ── Validar ───────────────────────────────────────────────────────────────────
if (-not (Test-Path $ScriptPath)) {
    Write-Error "Arquivo não encontrado: $ScriptPath`nVerifique se ProjectDir está correto: $ProjectDir"
    exit 1
}

# ── Criar log dir ─────────────────────────────────────────────────────────────
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    Write-Host "Diretório de log criado: $LogDir"
}

# ── Criar wrapper (resolve problema de ExecutionPolicy no Task Scheduler) ─────
$wrapperContent = @"
# Auto-gerado por install-watchdog.ps1 — não editar manualmente
Set-Location "$ProjectDir"
`$env:WATCHDOG_INTERVAL_SEC  = "$IntervalSec"
`$env:WATCHDOG_LOG_DIR       = "$LogDir"
`$env:WATCHDOG_PROJECT_DIR   = "$ProjectDir"
& "$ScriptPath"
"@
Set-Content -Path $WrapperPath -Value $wrapperContent -Encoding UTF8
Write-Host "Wrapper criado: $WrapperPath"

# ── Registrar tarefa ──────────────────────────────────────────────────────────
# Trigger: ao iniciar o sistema (boot) + repetir a cada 5 minutos como fallback
$action  = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$WrapperPath`""

# Trigger 1: ao fazer login (garante que roda na sessão do usuário com acesso ao Docker Desktop)
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn

# Trigger 2: diariamente a cada hora como fallback (caso o sistema fique ligado sem relogar)
$triggerRepeat = New-ScheduledTaskTrigger -Daily -At "00:00"
# Adicionar repetição a cada hora dentro do trigger diário
$triggerRepeat.RepetitionPattern = New-Object -TypeName "Microsoft.Management.Infrastructure.CimInstance" `
    -ArgumentList "MSFT_TaskRepetitionPattern","Root/Microsoft/Windows/TaskScheduler" `
    -ErrorAction SilentlyContinue
# Forma mais simples e compatível:
$interval = [System.TimeSpan]::FromMinutes(5)
$duration = [System.TimeSpan]::FromDays(1)

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable:$false `
    -MultipleInstances IgnoreNew

# Registrar com trigger de logon
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

# Remover tarefa antiga se existir
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $triggerLogon `
    -Settings $settings `
    -Principal $principal `
    -Description "Monitora Docker Desktop e containers FinAnalytics. Reinicia automaticamente se parados." `
    | Out-Null

Write-Host ""
Write-Host "=========================================="
Write-Host " Watchdog instalado com sucesso!"
Write-Host "=========================================="
Write-Host " Tarefa:    $TaskName"
Write-Host " Script:    $ScriptPath"
Write-Host " Logs:      $LogDir"
Write-Host " Intervalo: ${IntervalSec}s"
Write-Host ""
Write-Host "Iniciando agora para teste..."
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 5

$taskInfo = Get-ScheduledTask -TaskName $TaskName
Write-Host " Status:    $($taskInfo.State)"
Write-Host ""
Write-Host "Comandos úteis:"
Write-Host "  Ver status:    Get-ScheduledTask -TaskName '$TaskName'"
Write-Host "  Ver logs:      Get-Content '$LogDir\watchdog_$(Get-Date -Format 'yyyy-MM').log' -Tail 30"
Write-Host "  Parar:         Stop-ScheduledTask -TaskName '$TaskName'"
Write-Host "  Desinstalar:   powershell -ExecutionPolicy Bypass -File install-watchdog.ps1 -Uninstall"
