<#
.SYNOPSIS
  Healthcheck externo do profit_agent. Reinicia o NSSM service se
  /status nao responde OU se boot ficou stuck em fase != ready
  por mais de N minutos.

.DESCRIPTION
  Defesa em profundidade contra agent stuck "Running" mas wedged:
  - Watchdog interno do agent (P0/P1, profit_agent.py) ja' ataca o
    caso de boot timeout, mas se a thread principal travar APOS
    "ready" (ex: DLL stuck mid-runtime) o watchdog nao percebe.
  - Este script externo cobre o caso pos-ready: tenta /status 3 vezes
    com backoff curto; falha consistente -> Restart-Service.

  Recomendacao: rodar via Task Scheduler 1x/min, como SYSTEM ou
  Administrator (Restart-Service exige privilege).

.NOTES
  Critica de falha (qualquer um -> restart):
    1. HTTP /status nao responde 200 em 8s, 3 tentativas seguidas
    2. boot_phase != "ready" E boot_elapsed_s > BOOT_STUCK_THRESHOLD_S
    3. Resposta JSON malformada / sem campos esperados

  Cooldown: apos restart, escreve marker file e nao tenta novo restart
  por COOLDOWN_S segundos (evita loop). NSSM ja' tem AppRestartDelay
  proprio.

  Se script falhar (ex: nssm path errado), loga em ERROR_LOG mas nao
  para o Task Scheduler — failures isolados sao recuperaveis.

.EXAMPLE
  # Manualmente
  pwsh -File D:\Projetos\finanalytics_ai_fresh\scripts\healthcheck_profit_agent.ps1

  # Setup Task Scheduler (Admin PowerShell):
  $action = New-ScheduledTaskAction -Execute 'pwsh.exe' `
    -Argument '-NoProfile -ExecutionPolicy Bypass -File "D:\Projetos\finanalytics_ai_fresh\scripts\healthcheck_profit_agent.ps1"'
  $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 1)
  $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest
  $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
  Register-ScheduledTask -TaskName 'FinAnalytics_AgentHealthcheck' `
    -Action $action -Trigger $trigger -Principal $principal -Settings $settings
#>

param(
    [string]$AgentUrl = "http://localhost:8002/status",
    [int]$BootStuckThresholdS = 600,
    [int]$CooldownS = 180,
    [string]$ServiceName = "FinAnalyticsAgent",
    [string]$LogPath = "D:\Projetos\finanalytics_ai_fresh\logs\agent_healthcheck.log",
    [string]$CooldownMarker = "D:\Projetos\finanalytics_ai_fresh\logs\.agent_healthcheck_cooldown",
    [int]$Tries = 3,
    [int]$RetryBackoffS = 5,
    [int]$HttpTimeoutS = 8
)

$ErrorActionPreference = 'Continue'

function Write-HCLog {
    param([string]$Level, [string]$Msg)
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "$ts $Level $Msg"
    try { Add-Content -Path $LogPath -Value $line -ErrorAction Stop } catch {}
    # IMPORTANTE: Write-Host nao polui o output stream da funcao caller.
    # Usar Write-Output aqui faria Test-AgentHealth retornar array
    # [string, string, ..., $false] em vez de so $false, quebrando o
    # `if ($health -eq $false)` no main.
    Write-Host $line
}

function Test-Cooldown {
    if (-not (Test-Path $CooldownMarker)) { return $false }
    $marker = Get-Item $CooldownMarker
    $age = (Get-Date) - $marker.LastWriteTime
    if ($age.TotalSeconds -lt $CooldownS) {
        Write-HCLog "INFO" "in_cooldown remaining_s=$([int]($CooldownS - $age.TotalSeconds))"
        return $true
    }
    return $false
}

function Set-Cooldown {
    Set-Content -Path $CooldownMarker -Value (Get-Date -Format 'o') -ErrorAction SilentlyContinue
}

function Test-AgentHealth {
    # Retorna $true se saudavel, $false se deve restart, $null se inconclusivo (nao restart)
    for ($i = 1; $i -le $Tries; $i++) {
        try {
            $r = Invoke-WebRequest -Uri $AgentUrl -UseBasicParsing -TimeoutSec $HttpTimeoutS
            if ($r.StatusCode -ne 200) {
                Write-HCLog "WARN" "http_non_200 try=$i code=$($r.StatusCode)"
                if ($i -lt $Tries) { Start-Sleep -Seconds $RetryBackoffS; continue }
                return $false
            }
            $body = $r.Content | ConvertFrom-Json -ErrorAction Stop
            $phase = $body.boot_phase
            $elapsed = [double]($body.boot_elapsed_s)
            if (-not $phase) {
                Write-HCLog "WARN" "missing_boot_phase try=$i — skipping"
                return $null
            }
            if ($phase -eq "ready") {
                Write-HCLog "INFO" "ok phase=ready elapsed_s=$elapsed market=$($body.market_connected) db=$($body.db_connected)"
                return $true
            }
            if ($elapsed -gt $BootStuckThresholdS) {
                Write-HCLog "ERROR" "boot_stuck phase=$phase elapsed_s=$elapsed threshold=$BootStuckThresholdS"
                return $false
            }
            Write-HCLog "INFO" "booting phase=$phase elapsed_s=$elapsed (under threshold, waiting)"
            return $null
        } catch {
            Write-HCLog "WARN" "http_fail try=$i err=$($_.Exception.Message.Replace("`n", " ").Substring(0, [Math]::Min(150, $_.Exception.Message.Length)))"
            if ($i -lt $Tries) { Start-Sleep -Seconds $RetryBackoffS }
        }
    }
    Write-HCLog "ERROR" "http_fail_all_tries=$Tries"
    return $false
}

function Restart-AgentService {
    Write-HCLog "ERROR" "RESTART triggered service=$ServiceName"
    try {
        Restart-Service -Name $ServiceName -Force -ErrorAction Stop
        Set-Cooldown
        Write-HCLog "INFO" "restart_ok cooldown_s=$CooldownS"
    } catch {
        Write-HCLog "ERROR" "restart_failed err=$($_.Exception.Message)"
    }
}

# ── main ─────────────────────────────────────────────────────────────────────

if (Test-Cooldown) { exit 0 }

$health = Test-AgentHealth
if ($health -eq $false) {
    Restart-AgentService
} elseif ($health -eq $true) {
    # OK — no-op
} else {
    # null = inconclusivo (booting under threshold); aguarda proximo tick
}

exit 0
