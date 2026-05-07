<#
.SYNOPSIS
  Registra (ou remove) a Scheduled Task que executa
  healthcheck_profit_agent.ps1 a cada 1 minuto como SYSTEM.

.DESCRIPTION
  Auto-eleva via UAC se nao estiver rodando como Admin.
  Idempotente: se a task ja existe, remove e recria.

.PARAMETER Remove
  Se passado, apenas remove a task e sai.

.PARAMETER User
  -User para rodar como o usuario atual interativo em vez de SYSTEM
  (so funciona enquanto voce esta logado; perde defesa em reboot sem
  login). Default e' SYSTEM (recomendado pra dev box 24/7).

.EXAMPLE
  # 1-click setup (vai pedir UAC):
  pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\setup_healthcheck_task.ps1

  # Remover:
  pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\setup_healthcheck_task.ps1 -Remove

  # Registrar como user atual (em vez de SYSTEM):
  pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\setup_healthcheck_task.ps1 -User

.NOTES
  Task: FinAnalytics_AgentHealthcheck
  Script alvo: scripts\healthcheck_profit_agent.ps1
  Intervalo: 1 minuto (RepetitionInterval)
  Limite por run: 2 minutos (ExecutionTimeLimit)
#>

[CmdletBinding()]
param(
    [switch]$Remove,
    [switch]$User
)

$ErrorActionPreference = 'Stop'

$TaskName = 'FinAnalytics_AgentHealthcheck'
$ScriptPath = Join-Path $PSScriptRoot 'healthcheck_profit_agent.ps1'

# ── Auto-elevate se nao estiver rodando como Admin ───────────────────────────
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) {
    Write-Host "Nao esta rodando como Administrator — solicitando elevacao via UAC..." -ForegroundColor Yellow
    $argList = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', "`"$PSCommandPath`""
    )
    if ($Remove) { $argList += '-Remove' }
    if ($User)   { $argList += '-User' }
    try {
        Start-Process pwsh -Verb RunAs -ArgumentList $argList -Wait
    } catch {
        Write-Host "Falha ao elevar: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "Abra um PowerShell como Administrator e rode novamente." -ForegroundColor Red
        exit 1
    }
    exit 0
}

# ── Sanity checks ────────────────────────────────────────────────────────────
if (-not (Test-Path $ScriptPath)) {
    Write-Host "ERRO: script nao encontrado em $ScriptPath" -ForegroundColor Red
    exit 1
}

# ── Remove task antiga (idempotente, e tb usado em -Remove) ──────────────────
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removendo task existente '$TaskName'..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

if ($Remove) {
    Write-Host "Task '$TaskName' removida (ou nao existia)." -ForegroundColor Green
    exit 0
}

# ── Registra task ────────────────────────────────────────────────────────────
$action = New-ScheduledTaskAction -Execute 'pwsh.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1)

if ($User) {
    Write-Host "Registrando como user atual (RunLevel Highest)..." -ForegroundColor Cyan
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest -LogonType Interactive
} else {
    Write-Host "Registrando como SYSTEM (recomendado p/ 24/7)..." -ForegroundColor Cyan
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest -LogonType ServiceAccount
}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName `
    -Description 'Healthcheck profit_agent (curl :8002/status; restart se stuck/down). 1x/min.' `
    -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null

# ── Verifica ─────────────────────────────────────────────────────────────────
$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host ""
Write-Host "✓ Task registrada com sucesso" -ForegroundColor Green
Write-Host ("  Name:       " + $task.TaskName)
Write-Host ("  State:      " + $task.State)
Write-Host ("  Principal:  " + $task.Principal.UserId + " (RunLevel " + $task.Principal.RunLevel + ")")
Write-Host ("  Next run:   " + $info.NextRunTime)
Write-Host ("  Action:     pwsh -File " + $ScriptPath)
Write-Host ""
Write-Host "Logs: D:\Projetos\finanalytics_ai_fresh\logs\agent_healthcheck.log" -ForegroundColor Cyan
Write-Host "Para remover: rode este script com -Remove" -ForegroundColor Cyan
Write-Host ""
Write-Host "Pressione qualquer tecla para fechar..." -ForegroundColor DarkGray
$null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
