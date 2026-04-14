# monitor_backfill.ps1 — Acompanha progresso do backfill em tempo real
# Uso: abrir OUTRO PowerShell -> .\scripts\monitor_backfill.ps1
#
# Atualiza a cada 15 segundos. Ctrl+C para sair.

$ErrorActionPreference = "SilentlyContinue"
Set-Location "D:\Projetos\finanalytics_ai_fresh"

# Dias uteis no periodo completo (jan-abr 2026, excl feriados)
# Usado para calcular % de completude
$TOTAL_TRADING_DAYS_STOCKS  = 70   # acoes: ~70 pregoes jan-abr
$TOTAL_TRADING_DAYS_FUTURES = 70   # futuros: idem
$INTERVAL = 15

function Get-BackfillStatus {
    $query = @"
SELECT
    t.ticker,
    t.exchange,
    CASE WHEN t.active THEN 'ON' ELSE 'OFF' END AS ativo,
    COALESCE(h.dias, 0) AS dias,
    h.inicio,
    h.fim,
    COALESCE(h.total_ticks, 0) AS ticks,
    ROUND(COALESCE(h.dias, 0)::numeric / 70 * 100, 1) AS pct
FROM profit_history_tickers t
LEFT JOIN (
    SELECT
        ticker,
        COUNT(DISTINCT trade_date::date) AS dias,
        MIN(trade_date::date) AS inicio,
        MAX(trade_date::date) AS fim,
        COUNT(*) AS total_ticks
    FROM market_history_trades
    GROUP BY ticker
) h ON h.ticker = t.ticker
ORDER BY t.ticker;
"@
    docker exec finanalytics_timescale psql -U finanalytics -d market_data -c $query 2>$null
}

function Get-TotalTicks {
    $result = docker exec finanalytics_timescale psql -U finanalytics -d market_data -t -A -c `
        "SELECT COUNT(*) FROM market_history_trades;" 2>$null
    return $result.Trim()
}

function Get-RecentActivity {
    $query = @"
SELECT ticker,
       trade_date::date AS dia,
       COUNT(*) AS ticks
FROM market_history_trades
WHERE created_at > NOW() - INTERVAL '2 minutes'
GROUP BY ticker, trade_date::date
ORDER BY MAX(created_at) DESC
LIMIT 5;
"@
    docker exec finanalytics_timescale psql -U finanalytics -d market_data -c $query 2>$null
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  MONITOR DE BACKFILL" -ForegroundColor Cyan
Write-Host "  Atualiza a cada ${INTERVAL}s | Ctrl+C para sair" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

while ($true) {
    Clear-Host
    $now = Get-Date -Format "HH:mm:ss"
    $totalTicks = Get-TotalTicks

    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  BACKFILL MONITOR  [$now]" -ForegroundColor Cyan
    Write-Host "  Total ticks no banco: $totalTicks" -ForegroundColor Cyan
    Write-Host "========================================`n" -ForegroundColor Cyan

    Write-Host "--- Progresso por Ticker ---" -ForegroundColor Yellow
    Get-BackfillStatus

    Write-Host "`n--- Atividade Recente (ultimos 2 min) ---" -ForegroundColor Yellow
    Get-RecentActivity

    Write-Host "`nProxima atualizacao em ${INTERVAL}s..." -ForegroundColor DarkGray
    Start-Sleep -Seconds $INTERVAL
}
