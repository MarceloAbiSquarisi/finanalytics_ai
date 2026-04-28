#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Instala profit_agent como servico Windows via NSSM.

.DESCRIPTION
    Mata processos zumbis na porta 8002, instala servico FinAnalyticsAgent,
    configura AppDirectory + redirect logs, inicia, valida /health.

    Roda elevado: clique direito no PowerShell -> "Executar como administrador"
    ou: PS> Start-Process powershell -Verb RunAs -ArgumentList "-File path\to\script.ps1"

.NOTES
    Uninstall: nssm stop FinAnalyticsAgent ; nssm remove FinAnalyticsAgent confirm
#>

$ErrorActionPreference = 'Stop'
$svc = 'FinAnalyticsAgent'
$proj = 'D:\Projetos\finanalytics_ai_fresh'
$python = "$proj\.venv\Scripts\python.exe"
$script = "$proj\src\finanalytics_ai\workers\profit_agent.py"
$logOut = "$proj\logs\profit_agent.stdout.log"
$logErr = "$proj\logs\profit_agent.stderr.log"
$nssm = (Get-Command nssm -ErrorAction Stop).Source

Write-Host "=== Pre-flight ===" -ForegroundColor Cyan
Write-Host "NSSM: $nssm"
Write-Host "Python: $python"
Write-Host "Script: $script"

# Garante diretorio de logs
$logDir = Split-Path $logOut -Parent
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

# Step 1: Kill leftover processes na porta 8002
Write-Host "`n=== Step 1: Limpando porta 8002 ===" -ForegroundColor Cyan
$conns = Get-NetTCPConnection -LocalPort 8002 -State Listen -ErrorAction SilentlyContinue
if ($conns) {
    foreach ($c in $conns) {
        Write-Host "Killing PID $($c.OwningProcess) listening on 8002..."
        Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
} else {
    Write-Host "Porta 8002 ja livre"
}

# Step 2: Remove servico existente (idempotencia)
Write-Host "`n=== Step 2: Cleanup servico antigo ===" -ForegroundColor Cyan
$existing = Get-Service -Name $svc -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Servico $svc ja existe (status=$($existing.Status)), removendo..."
    & $nssm stop $svc 2>&1 | Out-Null
    Start-Sleep -Seconds 1
    & $nssm remove $svc confirm 2>&1 | Out-Null
    Start-Sleep -Seconds 1
} else {
    Write-Host "Sem servico existente"
}

# Step 3: Install + configure
Write-Host "`n=== Step 3: Install $svc ===" -ForegroundColor Cyan
& $nssm install $svc $python $script
& $nssm set $svc AppDirectory $proj
& $nssm set $svc DisplayName 'FinAnalytics Profit Agent'
& $nssm set $svc Description 'Profit DLL agent (port 8002) — managed by NSSM watchdog'
& $nssm set $svc Start SERVICE_AUTO_START
& $nssm set $svc AppStdout $logOut
& $nssm set $svc AppStderr $logErr
# Rotate logs em 10MB
& $nssm set $svc AppRotateFiles 1
& $nssm set $svc AppRotateBytes 10485760
# Restart automatico em crash (delay 2s, max 3 retries em 60s)
& $nssm set $svc AppExit Default Restart
& $nssm set $svc AppRestartDelay 2000
& $nssm set $svc AppThrottle 5000
# Conta de servico — escolha:
#   (a) LocalSystem (default, sem senha, isolado do user profile)
#   (b) Conta do usuario logado (precisa senha; permite acesso a tudo do user)
# Default: LocalSystem. Se der problema de DLL/Docker, troque manualmente:
#   nssm set FinAnalyticsAgent ObjectName ".\<user>" "<password>"
& $nssm set $svc ObjectName LocalSystem

Write-Host "Configuracao aplicada"

# Step 4: Start
Write-Host "`n=== Step 4: Start service ===" -ForegroundColor Cyan
& $nssm start $svc
Start-Sleep -Seconds 3

$status = (& $nssm status $svc).Trim()
Write-Host "Status: $status"

# Step 5: Health check
Write-Host "`n=== Step 5: Health poll (30s) ===" -ForegroundColor Cyan
$start = Get-Date
$ok = $false
for ($i = 1; $i -le 30; $i++) {
    try {
        $r = Invoke-RestMethod -Uri 'http://localhost:8002/health' -TimeoutSec 1 -ErrorAction Stop
        if ($r.ok) {
            $elapsed = ((Get-Date) - $start).TotalSeconds
            Write-Host "Health OK em $($elapsed.ToString('F1'))s" -ForegroundColor Green
            $ok = $true
            break
        }
    } catch {
        Write-Host "  attempt ${i}: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 1
}

if (-not $ok) {
    Write-Host "ERRO: /health nao respondeu em 30s. Checar logs:" -ForegroundColor Red
    Write-Host "  $logErr"
    if (Test-Path $logErr) { Get-Content $logErr -Tail 30 }
    exit 1
}

# Step 6: Status final
Write-Host "`n=== Status agent ===" -ForegroundColor Cyan
Invoke-RestMethod 'http://localhost:8002/status' | ConvertTo-Json -Compress

Write-Host "`n=== Conclusao ===" -ForegroundColor Green
Write-Host "Servico $svc instalado e rodando."
Write-Host "Test restart: POST /api/v1/agent/restart com X-Sudo-Token"
Write-Host "Watchdog deve recriar processo automaticamente em ~2-5s"
Write-Host ""
Write-Host "Comandos uteis:"
Write-Host "  nssm status $svc"
Write-Host "  nssm restart $svc"
Write-Host "  Get-Service $svc"
Write-Host "  tail -f '$logOut'"
