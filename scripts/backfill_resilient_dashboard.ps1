# Dashboard live para backfill_resilient.py + supervisor.
# Le E:\finanalytics_data\backfill_resilient.log (escrito pelo container).
# Refresh 10s. Ctrl+C pra sair.

$ErrorActionPreference = 'SilentlyContinue'
$RefreshSec = 10
$LogFile = 'E:\finanalytics_data\backfill_resilient.log'
$StateFile = 'E:\finanalytics_data\backfill_resilient_state.json'

function Parse-Lines {
    if (-not (Test-Path $LogFile)) { return @() }
    $rows = @()
    foreach ($line in Get-Content $LogFile) {
        $tag = ($line -split ' ')[0]
        if ($tag -notin @('START','PROGRESS','SKIP','ERROR','DONE','HEARTBEAT','AGENT_STUCK','SIGNAL','STOPPED_BY_SIGNAL','FATAL','STATE_RESET','STATE_DAY_MISMATCH')) { continue }
        $tk='?'; $dy='?'; $tk_n=0; $st='?'; $els=0; $att=0; $cons=0; $i=0; $n=0; $reason=''
        if ($line -match 'ticker=(\S+)')        { $tk = $Matches[1] }
        if ($line -match 'cur_ticker=(\S+)')    { $tk = $Matches[1] }
        if ($line -match 'last_ticker=(\S+)')   { $tk = $Matches[1] }
        if ($line -match 'day=(\S+)')           { $dy = $Matches[1] }
        if ($line -match 'ticks=(\d+)')         { $tk_n = [int64]$Matches[1] }
        if ($line -match 'status=(\S+)')        { $st = $Matches[1] }
        if ($line -match 'elapsed_s=([\d.]+)')  { $els = [double]$Matches[1] }
        if ($line -match 'attempt=(\d+)')       { $att = [int]$Matches[1] }
        if ($line -match 'consecutive=(\d+)')   { $cons = [int]$Matches[1] }
        if ($line -match ' i=(\d+)')            { $i = [int]$Matches[1] }
        if ($line -match ' n=(\d+)')            { $n = [int]$Matches[1] }
        if ($line -match 'reason=(\S+)')        { $reason = $Matches[1] }

        $rows += [PSCustomObject]@{
            Tag=$tag; Day=$dy; Ticker=$tk; Ticks=$tk_n; Status=$st; ElapsedS=$els
            Attempt=$att; Consecutive=$cons; I=$i; N=$n; Reason=$reason; Line=$line
        }
    }
    return $rows
}

while ($true) {
    Clear-Host
    $now = Get-Date

    # Agent
    try {
        $r = Invoke-RestMethod 'http://localhost:8002/status' -TimeoutSec 3
        $color = if ($r.login_ok -and $r.market_connected) { 'Green' } else { 'Red' }
        $agent = "login=$($r.login_ok) market=$($r.market_connected) ticks=$($r.total_ticks) queue=$($r.db_queue_size)"
    } catch { $agent = "DOWN"; $color = 'Red' }

    Write-Host ("===== BACKFILL RESILIENT - {0} =====" -f $now.ToString('HH:mm:ss')) -ForegroundColor Cyan
    Write-Host ("[AGENT] {0}" -f $agent) -ForegroundColor $color
    Write-Host ""

    $rows = Parse-Lines
    if ($rows.Count -eq 0) {
        Write-Host "  (aguardando primeiro evento — supervisor disparou backfill?)" -ForegroundColor DarkGray
        Start-Sleep -Seconds $RefreshSec
        continue
    }

    # START line (ultima)
    $start = $rows | Where-Object { $_.Tag -eq 'START' } | Select-Object -Last 1
    if ($start) {
        if ($start.Line -match 'pending=(\d+)') { $totPending = [int]$Matches[1] } else { $totPending = 0 }
        if ($start.Line -match 'total=(\d+)')   { $tot = [int]$Matches[1] } else { $tot = 0 }
        if ($start.Line -match 'already_done=(\d+)') { $doneAlready = [int]$Matches[1] } else { $doneAlready = 0 }
        if ($start.Line -match 'day=(\S+)') { $targetDay = $Matches[1] } else { $targetDay = '?' }
        Write-Host ("Day: {0}  Total: {1}  Already done: {2}  Pending this run: {3}" -f $targetDay, $tot, $doneAlready, $totPending) -ForegroundColor White
    } else {
        $tot = 0; $totPending = 0
    }

    # Counts
    $okThisRun   = ($rows | Where-Object { $_.Tag -eq 'PROGRESS' }).Count
    $skipThisRun = ($rows | Where-Object { $_.Tag -eq 'SKIP' }).Count
    $errThisRun  = ($rows | Where-Object { $_.Tag -eq 'ERROR' }).Count
    $stuckEvents = ($rows | Where-Object { $_.Tag -eq 'AGENT_STUCK' }).Count
    $doneEvent   = ($rows | Where-Object { $_.Tag -eq 'DONE' } | Select-Object -Last 1)

    # Pct (cumulativo via state file se existir)
    $cumOk = 0; $cumSkip = 0; $cumErr = 0
    if (Test-Path $StateFile) {
        try {
            $s = (Get-Content $StateFile -Raw | ConvertFrom-Json).summary
            $cumOk = $s.ok; $cumSkip = $s.skip; $cumErr = $s.err
        } catch {}
    }
    $cumDone = $cumOk + $cumSkip
    $pct = if ($tot -gt 0) { [math]::Round(100 * $cumDone / $tot, 2) } else { 0 }

    Write-Host ("Cumulativo (state file): ok={0} skip={1} err={2}  -> {3}/{4} ({5}%)" -f $cumOk, $cumSkip, $cumErr, $cumDone, $tot, $pct) -ForegroundColor Yellow
    Write-Host ("Run atual: PROGRESS={0} SKIP={1} ERROR={2} AGENT_STUCK={3}" -f $okThisRun, $skipThisRun, $errThisRun, $stuckEvents) -ForegroundColor DarkYellow
    Write-Host ""

    # Last 15 events com Ticker | Data | %
    $tableRows = $rows | Where-Object { $_.Tag -in @('PROGRESS','SKIP','ERROR','HEARTBEAT','AGENT_STUCK','DONE') } | Select-Object -Last 15
    if ($tableRows) {
        $tableRows | ForEach-Object {
            $tagColor = switch ($_.Tag) {
                'PROGRESS'    { 'Green' }
                'SKIP'        { 'DarkCyan' }
                'ERROR'       { 'Red' }
                'AGENT_STUCK' { 'Magenta' }
                'HEARTBEAT'   { 'DarkGray' }
                'DONE'        { 'Green' }
                default       { 'White' }
            }
            $statusBlob = if ($_.Tag -eq 'PROGRESS' -and $_.Status -eq 'ok') { 'ok' }
                          elseif ($_.Tag -eq 'SKIP') { "skip:$($_.Reason)" }
                          elseif ($_.Tag -eq 'ERROR') { "err:$($_.Status)" }
                          else { $_.Tag }
            $cur_pct = if ($_.N -gt 0) { [math]::Round(100 * $_.I / $_.N, 1) } else { 0 }
            $text = "{0,-13} {1,-10} {2,-10} {3,8} ticks  i={4}/{5} ({6}%)  attempt={7}  cons={8}  t={9}s" -f `
                $_.Tag, $_.Ticker, $_.Day, ('{0:N0}' -f $_.Ticks), $_.I, $_.N, $cur_pct, $_.Attempt, $_.Consecutive, $_.ElapsedS
            Write-Host $text -ForegroundColor $tagColor
        }
    }

    if ($doneEvent) {
        Write-Host ""
        Write-Host "*** DONE ***" -ForegroundColor Green
        Write-Host $doneEvent.Line -ForegroundColor Green
    }

    Write-Host ""
    Write-Host ("(refresh em {0}s - Ctrl+C pra sair)" -f $RefreshSec) -ForegroundColor DarkGray
    Start-Sleep -Seconds $RefreshSec
}
