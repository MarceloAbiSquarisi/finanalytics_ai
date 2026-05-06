# Dashboard live para backfill subscribed (hoje) + WINFUT/WDOFUT extendido.
# Uso: pwsh .\scripts\backfill_today_dashboard.ps1
# Ctrl+C pra sair. Refresh 5s.
#
# Le 2 logs (gravados em E:\finanalytics_data\ pelo container API):
#   - backfill_today_subscribed.log  (PROGRESS / SKIP / ERROR / DONE)
#   - backfill_winwdo_extended.log   (PROGRESS / SKIP / ERROR / TICKER_DONE / DONE)

$ErrorActionPreference = 'SilentlyContinue'
$RefreshSec = 10

$logToday  = 'E:\finanalytics_data\backfill_today_subscribed.log'
$logWinWdo = 'E:\finanalytics_data\backfill_winwdo_extended.log'

# Totals esperados (atualizados quando START aparece)
$totalToday = 0
$totalWin   = 0

function Parse-LogRows([string]$path, [string]$source) {
    if (-not (Test-Path $path)) { return @() }
    $rows = @()
    foreach ($line in Get-Content $path -ErrorAction SilentlyContinue) {
        $tag = ($line -split ' ')[0]
        if ($tag -notin @('PROGRESS','SKIP','ERROR','START','DONE','TICKER_DONE')) { continue }

        $tk='?'; $dy='?'; $tk_n=0; $st='?'; $els=0; $reason=''
        if ($line -match 'ticker=(\S+)')        { $tk = $Matches[1] }
        if ($line -match 'day=(\S+)')           { $dy = $Matches[1] }
        if ($line -match 'ticks=(\d+)')         { $tk_n = [int64]$Matches[1] }
        if ($line -match 'status=(\S+)')        { $st = $Matches[1] }
        if ($line -match 'elapsed_s=([\d.]+)')  { $els = [double]$Matches[1] }
        if ($line -match 'reason=(\S+)')        { $reason = $Matches[1] }

        $statusOut = switch ($tag) {
            'PROGRESS'    { if ($st -eq 'ok') { 'ok' } else { $st } }
            'SKIP'        { "SKIP:$reason" }
            'ERROR'       { if ($st -eq '?') { 'err' } else { $st } }
            'TICKER_DONE' { 'TICKER_DONE' }
            default       { $tag }
        }

        $rows += [PSCustomObject]@{
            Source    = $source
            Tag       = $tag
            Day       = $dy
            Ticker    = $tk
            Ticks     = $tk_n
            Status    = $statusOut
            ElapsedS  = $els
            Line      = $line
        }
    }
    return $rows
}

function Get-StartTotal([string]$path) {
    if (-not (Test-Path $path)) { return 0 }
    $startLine = Get-Content $path -ErrorAction SilentlyContinue | Where-Object { $_ -like 'START *' } | Select-Object -Last 1
    if ($startLine -and $startLine -match 'tickers_count=(\d+)') { return [int]$Matches[1] }
    if ($startLine -and $startLine -match 'total_calls=(\d+)')   { return [int]$Matches[1] }
    return 0
}

while ($true) {
    Clear-Host
    $now = Get-Date

    # Agent status
    try {
        $r = Invoke-RestMethod 'http://localhost:8002/status' -TimeoutSec 3
        $loginColor = if ($r.login_ok) { 'Green' } else { 'Red' }
        $agentInfo  = "login_ok=$($r.login_ok) market=$($r.market_connected) routing=$($r.routing_connected) ticks=$($r.total_ticks) queue=$($r.db_queue_size)"
    } catch { $agentInfo = "DOWN"; $loginColor = 'Red' }

    Write-Host ("===== BACKFILL DASHBOARD - {0} =====" -f $now.ToString('HH:mm:ss')) -ForegroundColor Cyan
    Write-Host ("[AGENT] {0}" -f $agentInfo) -ForegroundColor $loginColor
    Write-Host ""

    $rowsToday  = Parse-LogRows $logToday  'TODAY'
    $rowsWinWdo = Parse-LogRows $logWinWdo 'WIN/WDO'

    $totalToday = Get-StartTotal $logToday
    $totalWin   = Get-StartTotal $logWinWdo

    # ---- BLOCK 1: Subscribed hoje
    Write-Host ("--- [1] BACKFILL HOJE (subscribed) - total esperado: {0} tickers ---" -f $totalToday) -ForegroundColor White
    $todayDone = $rowsToday | Where-Object { $_.Tag -in @('PROGRESS','SKIP','ERROR') }
    $todayOk   = ($todayDone | Where-Object { $_.Status -eq 'ok' }).Count
    $todaySkip = ($todayDone | Where-Object { $_.Status -like 'SKIP:*' }).Count
    $todayErr  = ($todayDone | Where-Object { $_.Status -ne 'ok' -and $_.Status -notlike 'SKIP:*' }).Count
    $todayProcessed = $todayDone.Count
    $todayPct  = if ($totalToday -gt 0) { [math]::Round(100 * $todayProcessed / $totalToday, 1) } else { 0 }
    $todayDone   = ($rowsToday | Where-Object { $_.Tag -eq 'DONE' } | Select-Object -Last 1)

    Write-Host ("  Processados: {0}/{1} ({2}%)  ok={3} skip={4} err={5}" -f $todayProcessed, $totalToday, $todayPct, $todayOk, $todaySkip, $todayErr) -ForegroundColor Yellow
    if ($todayDone) { Write-Host "  [DONE]" -ForegroundColor Green }

    if ($rowsToday.Count -gt 0) {
        $rowsToday | Where-Object { $_.Tag -in @('PROGRESS','SKIP','ERROR') } | Select-Object -Last 12 |
            Format-Table @{L='Data';E={$_.Day}; W=12},
                         @{L='Ticker';E={$_.Ticker}; W=10},
                         @{L='Ticks';E={'{0:N0}' -f $_.Ticks}; W=12; A='right'},
                         @{L='Status';E={$_.Status}; W=20},
                         @{L='Tempo(s)';E={$_.ElapsedS}; W=10; A='right'}
    } else {
        Write-Host "  (aguardando...)" -ForegroundColor DarkGray
    }

    # ---- BLOCK 2: WINFUT/WDOFUT extendido
    Write-Host ""
    Write-Host ("--- [2] BACKFILL WINFUT/WDOFUT (2020-01-02 -> 2026-05-05) - total esperado: {0} dia-ticker calls ---" -f $totalWin) -ForegroundColor White
    $winDone = $rowsWinWdo | Where-Object { $_.Tag -in @('PROGRESS','SKIP','ERROR') }
    $winOk   = ($winDone | Where-Object { $_.Status -eq 'ok' }).Count
    $winSkip = ($winDone | Where-Object { $_.Status -like 'SKIP:*' }).Count
    $winErr  = ($winDone | Where-Object { $_.Status -ne 'ok' -and $_.Status -notlike 'SKIP:*' -and $_.Status -ne '?' }).Count
    $winProcessed = $winDone.Count
    $winPct  = if ($totalWin -gt 0) { [math]::Round(100 * $winProcessed / $totalWin, 2) } else { 0 }

    Write-Host ("  Processados: {0}/{1} ({2}%)  ok={3} skip={4} err={5}" -f $winProcessed, $totalWin, $winPct, $winOk, $winSkip, $winErr) -ForegroundColor Yellow

    # Estatistica por ticker (PROGRESS so)
    $perTk = $winDone | Where-Object { $_.Status -eq 'ok' } | Group-Object Ticker | Sort-Object Name
    foreach ($g in $perTk) {
        $sumTicks = ($g.Group | Measure-Object Ticks -Sum).Sum
        Write-Host ("    {0}: {1} dias coletados, {2:N0} ticks total" -f $g.Name, $g.Count, $sumTicks) -ForegroundColor DarkYellow
    }

    if ($rowsWinWdo.Count -gt 0) {
        $rowsWinWdo | Where-Object { $_.Tag -in @('PROGRESS','SKIP','ERROR') } | Select-Object -Last 12 |
            Format-Table @{L='Data';E={$_.Day}; W=12},
                         @{L='Ticker';E={$_.Ticker}; W=10},
                         @{L='Ticks';E={'{0:N0}' -f $_.Ticks}; W=12; A='right'},
                         @{L='Status';E={$_.Status}; W=20},
                         @{L='Tempo(s)';E={$_.ElapsedS}; W=10; A='right'}
    } else {
        Write-Host "  (aguardando inicio - roda apos task #1 acabar)" -ForegroundColor DarkGray
    }

    Write-Host ""
    Write-Host ("(refresh em {0}s - Ctrl+C pra sair)" -f $RefreshSec) -ForegroundColor DarkGray

    Start-Sleep -Seconds $RefreshSec
}
