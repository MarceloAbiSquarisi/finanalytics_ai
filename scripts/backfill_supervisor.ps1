# backfill_supervisor.ps1 — supervisor com auto-restart do agent.
#
# Loop:
#   1. Roda backfill_resilient.py dentro do container API
#   2. Se exit=0: DONE, break
#   3. Se exit=2 (AGENT_STUCK): restart NSSM agent, espera login_ok, re-roda
#   4. Se exit=1 ou outro: log + abort
# Max iterations: 12 (cada cycle pode resgatar ~5-10 tickers stuck)
#
# Requisitos:
#   - Stop-Service/Start-Service ja' tem ACL pro user atual (Decisao 24)
#   - NSSM auto-restart NAO basta (kill via Stop-Service e' deterministico)

param(
    [int]$MaxIterations = 12,
    [string]$Day = $null,
    [switch]$Reset
)

$ErrorActionPreference = 'Stop'

$LogFile = 'E:\finanalytics_data\backfill_resilient.log'
$ContainerLog = '/data/backfill_resilient.log'

# Limpa log se --reset
if ($Reset) {
    Remove-Item $LogFile -ErrorAction SilentlyContinue
    Remove-Item 'E:\finanalytics_data\backfill_resilient_state.json' -ErrorAction SilentlyContinue
    docker exec finanalytics_api sh -c "rm -f /data/backfill_resilient_state.json $ContainerLog; touch $ContainerLog" 2>&1 | Out-Null
    Write-Host "[supervisor] State + log resetados" -ForegroundColor Yellow
}

function Wait-AgentReady {
    param([int]$TimeoutSec = 240)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-RestMethod 'http://localhost:8002/status' -TimeoutSec 4
            if ($r.login_ok -and $r.market_connected) {
                Write-Host "[supervisor] Agent UP login_ok=true market=true ticks=$($r.total_ticks)" -ForegroundColor Green
                return $true
            }
        } catch {}
        Start-Sleep -Seconds 5
    }
    return $false
}

function Restart-Agent {
    Write-Host "[supervisor] Stop+Start FinAnalyticsAgent..." -ForegroundColor Yellow
    Stop-Service FinAnalyticsAgent -Force -ErrorAction Continue
    Start-Sleep -Seconds 3
    Start-Service FinAnalyticsAgent -ErrorAction Continue
    Start-Sleep -Seconds 5
    return Wait-AgentReady -TimeoutSec 240
}

function Run-Backfill {
    # Roda no HOST (Python venv local) — script novo nao esta no container image.
    # Usa defaults do script: AGENT_URL=localhost:8002, DSN=localhost:5433.
    # Output -> E:\finanalytics_data\backfill_resilient.log (= /data/ no container, lido pelo dashboard)
    $py = 'D:\Projetos\finanalytics_ai_fresh\.venv\Scripts\python.exe'
    $script = 'D:\Projetos\finanalytics_ai_fresh\scripts\backfill_resilient.py'
    $extra = @()
    if ($Day) { $extra += '--day'; $extra += $Day }
    $env:BACKFILL_STATE_FILE = 'E:\finanalytics_data\backfill_resilient_state.json'
    & $py $script @extra *>> $LogFile
    return $LASTEXITCODE
}

# ==== MAIN LOOP ====

if (-not (Wait-AgentReady -TimeoutSec 240)) {
    Write-Host "[supervisor] Agent nao responde apos 240s — abortando" -ForegroundColor Red
    exit 1
}

for ($iter = 1; $iter -le $MaxIterations; $iter++) {
    Write-Host ""
    Write-Host "=== ITERATION $iter / $MaxIterations ===" -ForegroundColor Cyan
    Write-Host ("[supervisor] $(Get-Date -Format 'HH:mm:ss') Disparando backfill_resilient...")

    $code = Run-Backfill

    Write-Host "[supervisor] $(Get-Date -Format 'HH:mm:ss') Backfill exit_code=$code"

    if ($code -eq 0) {
        Write-Host "[supervisor] DONE (exit 0)" -ForegroundColor Green
        break
    }
    if ($code -eq 2) {
        Write-Host "[supervisor] EXIT_AGENT_STUCK (exit 2) — restart agent + resume" -ForegroundColor Yellow
        if (-not (Restart-Agent)) {
            Write-Host "[supervisor] Restart falhou — abortando" -ForegroundColor Red
            exit 1
        }
        continue
    }
    if ($code -eq 1) {
        Write-Host "[supervisor] EXIT_FATAL (exit 1) — abortando" -ForegroundColor Red
        exit 1
    }
    Write-Host "[supervisor] Exit code inesperado=$code — abortando" -ForegroundColor Red
    exit 1
}

if ($iter -gt $MaxIterations) {
    Write-Host "[supervisor] MaxIterations=$MaxIterations atingido sem DONE" -ForegroundColor Yellow
    exit 1
}
