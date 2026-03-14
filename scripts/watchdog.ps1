# FinAnalytics Watchdog — compativel com PowerShell 5+
# Monitora Docker Desktop e containers. Reinicia automaticamente se necessario.
#
# Uso direto:    powershell -ExecutionPolicy Bypass -File watchdog.ps1 -RunOnce
# Como servico:  powershell -ExecutionPolicy Bypass -File install-watchdog.ps1

param([switch]$RunOnce)

# ── Configuracao (PS5: sem operador ??) ───────────────────────────────────────
if ($env:WATCHDOG_INTERVAL_SEC) { $IntervalSec = [int]$env:WATCHDOG_INTERVAL_SEC } else { $IntervalSec = 60 }
if ($env:WATCHDOG_LOG_DIR)      { $LogDir      = $env:WATCHDOG_LOG_DIR }           else { $LogDir      = "D:\Logs\finanalytics" }
if ($env:WATCHDOG_PROJECT_DIR)  { $ProjectDir  = $env:WATCHDOG_PROJECT_DIR }       else { $ProjectDir  = "D:\Projetos\finanalytics_ai" }
if ($env:WATCHDOG_COMPOSE_FILE) { $ComposeFile = $env:WATCHDOG_COMPOSE_FILE }      else { $ComposeFile = "docker-compose.yml" }

$RequiredContainers = @(
    "finanalytics_postgres",
    "finanalytics_redis",
    "finanalytics_api",
    "finanalytics_worker",
    "finanalytics_scheduler"
)

# ── Logger ────────────────────────────────────────────────────────────────────
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$LogFile = Join-Path $LogDir ("watchdog_" + (Get-Date -Format "yyyy-MM") + ".log")

function Write-Log($Level, $Message) {
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts [$Level] $Message"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}
function Log-Info($m)  { Write-Log "INFO " $m }
function Log-Warn($m)  { Write-Log "WARN " $m }
function Log-Error($m) { Write-Log "ERROR" $m }

# ── Docker Desktop ────────────────────────────────────────────────────────────
function Test-DockerRunning {
    try {
        & docker info 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Start-DockerDesktop {
    Log-Warn "Docker Desktop nao esta rodando. Iniciando..."
    $paths = @(
        "C:\Program Files\Docker\Docker\Docker Desktop.exe",
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
        "$env:LOCALAPPDATA\Docker\Docker Desktop.exe"
    )
    $dockerExe = $null
    foreach ($p in $paths) {
        if (Test-Path $p) { $dockerExe = $p; break }
    }
    if (-not $dockerExe) {
        Log-Error "Docker Desktop nao encontrado. Verifique a instalacao."
        return $false
    }
    Start-Process $dockerExe
    Log-Info "Aguardando Docker Desktop iniciar (60s)..."
    Start-Sleep -Seconds 60
    $attempts = 0
    while (-not (Test-DockerRunning) -and $attempts -lt 6) {
        Start-Sleep -Seconds 10
        $attempts++
    }
    if (Test-DockerRunning) {
        Log-Info "Docker Desktop pronto."
        return $true
    }
    Log-Error "Docker Desktop nao respondeu apos tentativas."
    return $false
}

# ── Containers ────────────────────────────────────────────────────────────────
function Get-ContainerStatus($Name) {
    try {
        $out = & docker inspect --format "{{.State.Status}}" $Name 2>&1
        if ($LASTEXITCODE -ne 0) { return "missing" }
        return $out.Trim()
    } catch {
        return "error"
    }
}

function Repair-Containers {
    Log-Info "Verificando containers..."
    $needsUp = $false
    foreach ($container in $RequiredContainers) {
        $status = Get-ContainerStatus $container
        if ($status -ne "running") {
            Log-Warn "Container $container esta '$status' - marcando para restart."
            $needsUp = $true
        } else {
            Log-Info "Container $container OK."
        }
    }
    if ($needsUp) {
        Log-Warn "Executando docker-compose up..."
        Push-Location $ProjectDir
        try {
            & docker-compose -f $ComposeFile up -d 2>&1 | ForEach-Object { Log-Info "  $_" }
            if ($LASTEXITCODE -eq 0) {
                Log-Info "Containers restaurados."
            } else {
                Log-Error "docker-compose up retornou erro $LASTEXITCODE."
            }
        } finally {
            Pop-Location
        }
    }
}

# ── Health check da API ───────────────────────────────────────────────────────
function Test-ApiHealth {
    try {
        $r = Invoke-WebRequest "http://localhost:8000/health" -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Repair-ApiIfUnhealthy {
    if (-not (Test-ApiHealth)) {
        Log-Warn "API /health falhou. Reiniciando finanalytics_api..."
        & docker restart finanalytics_api 2>&1 | Out-Null
        Start-Sleep -Seconds 15
        if (Test-ApiHealth) {
            Log-Info "API recuperada."
        } else {
            Log-Error "API ainda nao responde apos restart."
        }
    }
}

# ── Verificacao principal ─────────────────────────────────────────────────────
function Invoke-Check {
    Log-Info "=== Verificacao iniciada ==="
    if (-not (Test-DockerRunning)) {
        $ok = Start-DockerDesktop
        if (-not $ok) {
            Log-Error "Nao foi possivel iniciar Docker Desktop. Abortando."
            return
        }
    }
    Repair-Containers
    Start-Sleep -Seconds 5
    Repair-ApiIfUnhealthy
    Log-Info "=== Verificacao concluida ==="
}

# ── Entrypoint ────────────────────────────────────────────────────────────────
Log-Info "FinAnalytics Watchdog iniciado. Intervalo=${IntervalSec}s Log=$LogFile"

if ($RunOnce) {
    Invoke-Check
    exit 0
}

while ($true) {
    try { Invoke-Check } catch { Log-Error "Erro inesperado: $_" }
    Log-Info "Proxima verificacao em ${IntervalSec}s..."
    Start-Sleep -Seconds $IntervalSec
}
