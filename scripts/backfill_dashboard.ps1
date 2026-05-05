# Dashboard live do backfill 2y futures - tabular.
# Uso: Start-Process powershell .\scripts\backfill_dashboard.ps1
# Refresh: 10s. Ctrl+C pra sair.

$ErrorActionPreference = 'SilentlyContinue'
$RefreshSec = 10

$root = Split-Path -Parent $PSScriptRoot
$pidFile      = Join-Path $root 'logs\backfill\current.pid'
$logPathFile  = Join-Path $root 'logs\backfill\current.log_path'
$progressFile = Join-Path $root 'logs\backfill\progress_2y.json'
$agentLog     = Join-Path $root 'logs\profit_agent.stdout.log'
$totalDays    = 519
$totalTickers = 2
$totalCalls   = $totalDays * $totalTickers

while ($true) {
    Clear-Host
    $now = Get-Date

    Write-Host ("===== BACKFILL 2Y FUTURES - {0} =====" -f $now.ToString('HH:mm:ss')) -ForegroundColor Cyan
    Write-Host ""

    # 1. Script + agent status (cabecalho)
    $scriptPid = Get-Content $pidFile
    $procInfo = "MORTO"
    if ($scriptPid) {
        $proc = Get-Process -Id $scriptPid -ErrorAction SilentlyContinue
        if ($proc) {
            $elapsed = [math]::Round(((Get-Date) - $proc.StartTime).TotalMinutes, 1)
            $procInfo = "PID $scriptPid ha ${elapsed}min"
        }
    }
    try {
        $r = Invoke-RestMethod 'http://localhost:8002/status' -TimeoutSec 3
        $agentInfo = "ticks=$($r.total_ticks) queue=$($r.db_queue_size)"
    } catch { $agentInfo = "DOWN" }
    Write-Host ("[SCRIPT] {0}    [AGENT] {1}" -f $procInfo, $agentInfo)
    Write-Host ""

    # 2. Tabela: dia atual + ultimos eventos PROGRESS
    Write-Host "TABELA DE COLETA:" -ForegroundColor White

    # Header
    $rows = @()

    # Ler eventos PROGRESS do backfill log (ja concluidos)
    $logPath = Get-Content $logPathFile
    if ($logPath -and (Test-Path $logPath)) {
        $progressLines = Get-Content $logPath | Where-Object { $_ -like 'PROGRESS *' }
        foreach ($line in $progressLines) {
            # Formato: PROGRESS ticker=X day=Y ticks=N inserted=M status=ok elapsed_s=T
            $tk='?'; $dy='?'; $tk_n=0; $st='?'; $els=0
            if ($line -match 'ticker=(\w+)') { $tk = $Matches[1] }
            if ($line -match 'day=(\S+)')    { $dy = $Matches[1] }
            if ($line -match 'ticks=(\d+)')  { $tk_n = [int64]$Matches[1] }
            if ($line -match 'status=(\w+)') { $st = $Matches[1] }
            if ($line -match 'elapsed_s=([\d.]+)') { $els = [double]$Matches[1] }
            $rows += [PSCustomObject]@{
                Day=$dy; Ticker=$tk; Ticks=$tk_n; Status=$st; ElapsedS=$els; Pct=100
            }
        }
    }

    # Dia atual em curso (derivar do agent log: GetHistoryTrades + batch atual)
    $lastReq    = Get-Content $agentLog | Select-String "GetHistoryTrades ticker" | Select-Object -Last 1
    $lastBatch  = Get-Content $agentLog | Select-String "collect_history batch "   | Select-Object -Last 1
    $lastStable = Get-Content $agentLog | Select-String "stabilized final"        | Select-Object -Last 1

    $curTk = '?'; $curDay = '?'
    if ($lastReq -and $lastReq.Line -match 'ticker=(\w+) (\d{2})/(\d{2})/(\d{4})') {
        $curTk = $Matches[1]
        $curDay = "$($Matches[4])-$($Matches[3])-$($Matches[2])"
    }

    # Saber se esse dia ja foi marcado done OU se ainda esta em curso
    $isDone = $rows | Where-Object { $_.Day -eq $curDay -and $_.Ticker -eq $curTk } | Select-Object -First 1
    if (-not $isDone -and $curTk -ne '?') {
        $totalTk = '-'; $cur = 0; $tot = 0; $pct = 0
        if ($lastStable -and $lastStable.Line -match 'final=(\d+)') {
            $totalTk = [int64]$Matches[1]
        }
        if ($lastBatch -and $lastBatch.Line -match 'batch (\d+)/(\d+)') {
            $cur = [int64]$Matches[1]; $tot = [int64]$Matches[2]
            if ($tot -gt 0) { $pct = [math]::Round(100 * $cur / $tot, 1) }
        }
        $rows += [PSCustomObject]@{
            Day=$curDay; Ticker=$curTk; Ticks=$cur; Status="EM_CURSO"; ElapsedS='-'; Pct=$pct
        }
    }

    # Ordena DESC por dia
    $sorted = $rows | Sort-Object { [datetime]$_.Day } -Descending

    if ($sorted.Count -eq 0) {
        Write-Host "  (aguardando primeiro evento PROGRESS...)" -ForegroundColor DarkGray
    } else {
        $sorted | Select-Object -First 25 | Format-Table @{L='Dia';E={$_.Day}; W=12},
            @{L='Ativo';E={$_.Ticker}; W=8},
            @{L='Ticks';E={'{0:N0}' -f $_.Ticks}; W=12; A='right'},
            @{L='%';E={"$($_.Pct)%"}; W=7; A='right'},
            @{L='Status';E={$_.Status}; W=10},
            @{L='Tempo(s)';E={$_.ElapsedS}; W=10; A='right'}
    }

    # 3. Stats agregados
    Write-Host ""
    $okCount = ($rows | Where-Object { $_.Status -eq 'ok' }).Count
    $sumTicks = ($rows | Where-Object { $_.Status -eq 'ok' } | Measure-Object Ticks -Sum).Sum
    $pctTotal = if ($totalCalls) { [math]::Round(100 * $okCount / $totalCalls, 2) } else { 0 }
    Write-Host ("RESUMO: {0}/{1} dias-ticker concluidos ({2}%)  Ticks coletados: {3:N0}" -f `
        $okCount, $totalCalls, $pctTotal, $sumTicks) -ForegroundColor Yellow

    Write-Host ""
    Write-Host ("(refresh em {0}s - Ctrl+C pra sair)" -f $RefreshSec) -ForegroundColor DarkGray

    Start-Sleep -Seconds $RefreshSec
}
