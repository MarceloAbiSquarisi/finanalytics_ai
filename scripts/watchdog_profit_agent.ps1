# watchdog_profit_agent.ps1
# Monitora o profit_agent (Nelogica) e reinicia automaticamente se parado.
#
# Uso direto : powershell -ExecutionPolicy Bypass -File watchdog_profit_agent.ps1 -RunOnce
# Instalado  : via install_profit_watchdog.ps1

param([switch]$RunOnce)

# -- Configuracao -------------------------------------------------------------
if ($env:PROFIT_WATCHDOG_INTERVAL_SEC) { $IntervalSec = [int]$env:PROFIT_WATCHDOG_INTERVAL_SEC } else { $IntervalSec = 60 }
if ($env:PROFIT_WATCHDOG_LOG_DIR)      { $LogDir      = $env:PROFIT_WATCHDOG_LOG_DIR }           else { $LogDir      = "D:\Logs\finanalytics" }
if ($env:PROFIT_WATCHDOG_PROJECT_DIR)  { $ProjectDir  = $env:PROFIT_WATCHDOG_PROJECT_DIR }       else { $ProjectDir  = "D:\Projetos\finanalytics_ai_fresh" }
if ($env:PROFIT_WATCHDOG_PORT)         { $AgentPort   = [int]$env:PROFIT_WATCHDOG_PORT }         else { $AgentPort   = 8001 }
if ($env:PROFIT_WATCHDOG_MAX_RESTARTS) { $MaxRestarts = [int]$env:PROFIT_WATCHDOG_MAX_RESTARTS } else { $MaxRestarts = 5 }

$BatPath   = "$ProjectDir\scripts\start_profit_agent.bat"
$AgentUrl  = "http://localhost:$AgentPort/health"

# -- Logger --------------------------------------------------------------------
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$LogFile = Join-Path $LogDir ("profit_watchdog_" + (Get-Date -Format "yyyy-MM") + ".log")

function Write-Log($Level, $Message) {
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts [$Level] $Message"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}
function Log-Info($m)  { Write-Log "INFO " $m }
function Log-Warn($m)  { Write-Log "WARN " $m }
function Log-Error($m) { Write-Log "ERROR" $m }

# -- Health check --------------------------------------------------------------
function Test-AgentHealth {
    try {
        $r = Invoke-WebRequest -Uri $AgentUrl -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

# -- Mata processos zumbis do agent --------------------------------------------
function Stop-ZombieAgent {
    $procs = Get-WmiObject Win32_Process -Filter "Name='python.exe' OR Name='uv.exe'" -ErrorAction SilentlyContinue
    foreach ($p in $procs) {
        $cmd = $p.CommandLine
        if ($cmd -and ($cmd -like "*profit_agent*" -or $cmd -like "*start_profit_agent*")) {
            Log-Warn "Encerrando processo zumbi PID=$($p.ProcessId): $($cmd.Substring(0, [Math]::Min(80, $cmd.Length)))..."
            try {
                Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
                Log-Info "Processo PID=$($p.ProcessId) encerrado."
            } catch {
                Log-Error "Nao foi possivel encerrar PID=$($p.ProcessId): $_"
            }
        }
    }
}

# -- Inicia o agent ------------------------------------------------------------
function Start-Agent {
    if (-not (Test-Path $BatPath)) {
        Log-Error "Arquivo nao encontrado: $BatPath"
        return $false
    }
    Log-Warn "Iniciando profit_agent via: $BatPath"
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c `"$BatPath`"" -WorkingDirectory $ProjectDir -WindowStyle Minimized
    Log-Info "Aguardando agent inicializar (30s)..."
    Start-Sleep -Seconds 30

    # Verifica ate 4 vezes (a DLL pode demorar para conectar)
    $attempts = 0
    while (-not (Test-AgentHealth) -and $attempts -lt 4) {
        Log-Info "Aguardando resposta do agent... tentativa $($attempts + 1)/4"
        Start-Sleep -Seconds 15
        $attempts++
    }
    if (Test-AgentHealth) {
        Log-Info "Profit Agent respondendo em $AgentUrl"
        return $true
    } else {
        Log-Warn "Agent nao respondeu ao health check - pode estar conectando a DLL (normal nos primeiros 60s)"
        return $false
    }
}

# -- Verificacao principal -----------------------------------------------------
$restartCount = 0

function Invoke-Check {
    Log-Info "=== Verificando Profit Agent (porta $AgentPort) ==="
    if (Test-AgentHealth) {
        Log-Info "Profit Agent OK - respondendo em $AgentUrl"
        return
    }

    Log-Warn "Profit Agent nao responde em $AgentUrl"

    if ($restartCount -ge $MaxRestarts) {
        Log-Error "Limite de $MaxRestarts reinicializacoes atingido. Aguardando intervalo."
        return
    }

    Stop-ZombieAgent
    Start-Sleep -Seconds 3
    $ok = Start-Agent
    $script:restartCount++

    if ($ok) {
        Log-Info "Profit Agent recuperado (reinicio $($script:restartCount)/$MaxRestarts)"
        $script:restartCount = 0
    } else {
        Log-Warn "Reinicio $($script:restartCount)/$MaxRestarts - agent pode estar inicializando"
    }
    Log-Info "=== Verificacao concluida ==="
}

# -- Entrypoint ----------------------------------------------------------------
Log-Info "Profit Agent Watchdog iniciado. Porta=$AgentPort Intervalo=${IntervalSec}s MaxRestarts=$MaxRestarts"

if ($RunOnce) {
    Invoke-Check
    exit 0
}

# Reset do contador a cada hora para evitar bloqueio permanente
$lastReset = Get-Date

while ($true) {
    if (((Get-Date) - $lastReset).TotalHours -ge 1) {
        $restartCount = 0
        $lastReset = Get-Date
        Log-Info "Contador de reinicializacoes resetado."
    }
    try { Invoke-Check } catch { Log-Error "Erro inesperado: $_" }
    Log-Info "Proxima verificacao em ${IntervalSec}s..."
    Start-Sleep -Seconds $IntervalSec
}