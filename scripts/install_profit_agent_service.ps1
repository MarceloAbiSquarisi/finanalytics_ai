# Instala o profit_agent como servico Windows via NSSM.
# Pre-requisitos:
#   - NSSM no PATH (ja usado para FinAnalyticsBackfill; confirmar com `nssm --version`)
#   - Rodar ESTE SCRIPT COMO ADMINISTRADOR (Start > PowerShell Admin > .\scripts\install_profit_agent_service.ps1)
#
# O que faz:
#   1. Mata qualquer python.exe rodando profit_agent.py (se houver).
#   2. Desinstala o servico se ja existir (reinstalacao limpa).
#   3. Instala o servico FinAnalyticsProfitAgent apontando para o .venv\python.
#   4. Configura auto-restart (AppExit=Restart, throttle=5s).
#   5. Inicia o servico e valida status em :8002.
#
# Depois de instalado:
#   - Boot automatico junto do Windows
#   - Botao "Reiniciar Agent" em /hub -> chama /api/v1/agent/restart ->
#     profit_agent faz os._exit(0) -> NSSM reinicia em 2-5s
#   - Logs: .profit_agent.log e .profit_agent.err.log (rotacionados pelo NSSM)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ServiceName = 'FinAnalyticsProfitAgent'
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$AgentScript = Join-Path $ProjectRoot 'src\finanalytics_ai\workers\profit_agent.py'
$StdoutLog = Join-Path $ProjectRoot '.profit_agent.log'
$StderrLog = Join-Path $ProjectRoot '.profit_agent.err.log'

# ── 0. Verificacoes ────────────────────────────────────────────────────────────

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "Este script precisa ser executado como ADMINISTRADOR. Abra PowerShell como admin e re-execute."
    exit 1
}

if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) {
    Write-Error "NSSM nao encontrado no PATH. Instale via: choco install nssm -y  (ou baixe de nssm.cc/download)"
    exit 1
}

if (-not (Test-Path $PythonExe)) {
    Write-Error "Python do venv nao encontrado em $PythonExe. Verifique o ambiente virtual."
    exit 1
}

if (-not (Test-Path $AgentScript)) {
    Write-Error "profit_agent.py nao encontrado em $AgentScript"
    exit 1
}

Write-Host "`n=== Instalando servico $ServiceName ===" -ForegroundColor Cyan
Write-Host "Project:  $ProjectRoot"
Write-Host "Python:   $PythonExe"
Write-Host "Script:   $AgentScript"
Write-Host "Logs:     $StdoutLog`n"

# ── 1. Matar profit_agent orfao (se rodando manual) ────────────────────────────

$orphan = Get-NetTCPConnection -LocalPort 8002 -State Listen -ErrorAction SilentlyContinue
if ($orphan) {
    Write-Host "Matando processo orfao PID=$($orphan.OwningProcess) em :8002..." -ForegroundColor Yellow
    Stop-Process -Id $orphan.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

# ── 2. Desinstalar servico existente (reinstalacao limpa) ──────────────────────

$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Servico ja existe — removendo para reinstalar limpo..." -ForegroundColor Yellow
    if ($existing.Status -eq 'Running') {
        & nssm stop $ServiceName confirm | Out-Null
        Start-Sleep -Seconds 2
    }
    & nssm remove $ServiceName confirm | Out-Null
    Start-Sleep -Seconds 1
}

# ── 3. Instalar servico ────────────────────────────────────────────────────────

Write-Host "Instalando servico via NSSM..." -ForegroundColor Cyan
& nssm install $ServiceName $PythonExe $AgentScript
& nssm set $ServiceName AppDirectory $ProjectRoot
& nssm set $ServiceName AppStdout $StdoutLog
& nssm set $ServiceName AppStderr $StderrLog
& nssm set $ServiceName AppRotateFiles 1
& nssm set $ServiceName AppRotateOnline 0
& nssm set $ServiceName AppRotateBytes 10485760  # rotaciona aos 10MB

# Auto-restart em crash ou exit (o nosso /restart chama os._exit)
& nssm set $ServiceName AppExit Default Restart
& nssm set $ServiceName AppRestartDelay 2000
& nssm set $ServiceName AppThrottle 5000

# Start automatico no boot
& nssm set $ServiceName Start SERVICE_AUTO_START
& nssm set $ServiceName DisplayName "FinAnalytics Profit Agent (Nelogica DLL)"
& nssm set $ServiceName Description "Agent HTTP na porta 8002 que comunica com Nelogica ProfitDLL. Reinicio automatico via NSSM."

# ── 4. Iniciar e validar ───────────────────────────────────────────────────────

Write-Host "`nIniciando servico..." -ForegroundColor Cyan
& nssm start $ServiceName

# Aguarda ate 30s pelo health do HTTP :8002
$up = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-RestMethod "http://localhost:8002/health" -TimeoutSec 1 -ErrorAction Stop
        if ($r.ok) { $up = $true; break }
    } catch { }
}

Write-Host ""
if ($up) {
    Write-Host "✓ Servico $ServiceName instalado e rodando!" -ForegroundColor Green
    try {
        $s = Invoke-RestMethod "http://localhost:8002/status"
        Write-Host "  market_connected: $($s.market_connected)"
        Write-Host "  routing_connected: $($s.routing_connected)"
        Write-Host "  db_connected: $($s.db_connected)"
        Write-Host "  subscribed_tickers: $($s.subscribed_tickers.Count)"
    } catch { }
    Write-Host "`nComandos uteis:"
    Write-Host "  nssm status $ServiceName     # ver status"
    Write-Host "  nssm stop $ServiceName       # parar"
    Write-Host "  nssm restart $ServiceName    # reiniciar via NSSM"
    Write-Host "  nssm edit $ServiceName       # UI de config"
    Write-Host "  nssm remove $ServiceName confirm   # desinstalar"
    Write-Host "`nOU reinicie via UI em /hub (botao 'Reiniciar Agent' pede senha)."
} else {
    Write-Host "✗ Servico instalado mas nao respondeu em :8002 apos 30s." -ForegroundColor Red
    Write-Host "  Verifique os logs:"
    Write-Host "  - $StdoutLog"
    Write-Host "  - $StderrLog"
    exit 1
}
