# monitor_backfill_top50.ps1 - Monitor do Cenario B (Top 50, 2025+2026)
# Uso: abrir OUTRO PowerShell -> .\scripts\monitor_backfill_top50.ps1
#
# Atualiza a cada 20s. Ctrl+C para sair.

$ErrorActionPreference = "SilentlyContinue"
Set-Location "D:\Projetos\finanalytics_ai_fresh"

$INTERVAL = 20

# Dias uteis esperados 2025-01-02 -> hoje (inclui 2026 parcial)
# 2025: ~245 dias  |  2026 ate 17/abr: ~72 dias  |  Total: ~317
# Ajuste se o periodo mudar.
$TOTAL_DIAS_ESPERADOS = 317

# SQL: cobertura por ticker da Top 50 (com chunk pruning em >= 2024-12-01)
$sqlCoverage = @'
WITH top50 AS (
    SELECT unnest(ARRAY[
      'VALE3','PETR4','ITUB4','BBAS3','BBDC4','B3SA3','JBSS3','EMBR3',
      'ABEV3','WEGE3','PRIO3','PETR3','SBSP3','ELET3','RENT3','SUZB3',
      'ITSA4','EQTL3','MBRF3','LREN3','MGLU3','RAIL3','VBBR3','BRFS3',
      'EMBJ3','RADL3','BBSE3','HAPV3','GGBR4','CRFB3','RDOR3','BRAV3',
      'CSAN3','MRFG3','VIVT3','CPLE6','ASAI3','ENEV3','CYRE3','NTCO3',
      'TOTS3','CCRO3','TIMS3','CMIG4','UGPA3','MOTV3','BBDC3','MULT3',
      'PSSA3','SMFT3'
    ]) AS ticker
)
SELECT t.ticker,
       COALESCE(h.dias, 0) AS dias,
       COALESCE(h.ticks, 0) AS ticks,
       h.inicio,
       h.fim,
       ROUND(COALESCE(h.dias,0)::numeric / 317 * 100, 1) AS pct
FROM top50 t
LEFT JOIN (
   SELECT ticker,
          COUNT(DISTINCT trade_date::date) AS dias,
          COUNT(*) AS ticks,
          MIN(trade_date::date) AS inicio,
          MAX(trade_date::date) AS fim
   FROM market_history_trades
   WHERE trade_date >= '2024-12-01'
   GROUP BY ticker
) h ON h.ticker = t.ticker
ORDER BY COALESCE(h.dias, 0) DESC, t.ticker;
'@

$sqlTotal = "SELECT COUNT(*) FROM market_history_trades WHERE trade_date >= '2024-12-01';"

$sqlResumo = @'
WITH top50 AS (
    SELECT unnest(ARRAY[
      'VALE3','PETR4','ITUB4','BBAS3','BBDC4','B3SA3','JBSS3','EMBR3',
      'ABEV3','WEGE3','PRIO3','PETR3','SBSP3','ELET3','RENT3','SUZB3',
      'ITSA4','EQTL3','MBRF3','LREN3','MGLU3','RAIL3','VBBR3','BRFS3',
      'EMBJ3','RADL3','BBSE3','HAPV3','GGBR4','CRFB3','RDOR3','BRAV3',
      'CSAN3','MRFG3','VIVT3','CPLE6','ASAI3','ENEV3','CYRE3','NTCO3',
      'TOTS3','CCRO3','TIMS3','CMIG4','UGPA3','MOTV3','BBDC3','MULT3',
      'PSSA3','SMFT3'
    ]) AS ticker
)
SELECT
  COUNT(*) FILTER (WHERE dias > 0)              AS tickers_com_dados,
  COUNT(*) FILTER (WHERE dias = 0)              AS tickers_zero,
  COUNT(*) FILTER (WHERE dias >= 317)           AS tickers_completos,
  SUM(dias)                                     AS total_dias_coletados,
  SUM(ticks)                                    AS total_ticks_top50
FROM (
  SELECT t.ticker,
         COALESCE(h.dias, 0)  AS dias,
         COALESCE(h.ticks, 0) AS ticks
  FROM top50 t
  LEFT JOIN (
    SELECT ticker,
           COUNT(DISTINCT trade_date::date) AS dias,
           COUNT(*) AS ticks
    FROM market_history_trades
    WHERE trade_date >= '2024-12-01'
    GROUP BY ticker
  ) h ON h.ticker = t.ticker
) _;
'@

function Invoke-Psql([string]$sql) {
    $sql | docker exec -i finanalytics_timescale psql -U finanalytics -d market_data -A 2>$null
}
function Invoke-PsqlTable([string]$sql) {
    $sql | docker exec -i finanalytics_timescale psql -U finanalytics -d market_data 2>$null
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  MONITOR BACKFILL 2025+2026 - TOP 50" -ForegroundColor Cyan
Write-Host "  Atualiza a cada ${INTERVAL}s | Ctrl+C p/ sair" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$lastTotal = $null
while ($true) {
    Clear-Host
    $now = Get-Date -Format "HH:mm:ss"

    $totalNow = Invoke-Psql $sqlTotal
    $totalNow = ($totalNow -split "`n")[1]  # linha de dado (headers + -A)
    $delta    = if ($lastTotal) { [int64]$totalNow - [int64]$lastTotal } else { 0 }
    $lastTotal = $totalNow

    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  BACKFILL TOP 50 [$now]" -ForegroundColor Cyan
    Write-Host "  Total ticks (>= 2024-12-01): $totalNow  (+$delta desde ultima leitura)" -ForegroundColor Cyan
    Write-Host "========================================`n" -ForegroundColor Cyan

    Write-Host "--- Resumo ---" -ForegroundColor Yellow
    Invoke-PsqlTable $sqlResumo

    Write-Host "`n--- Progresso por Ticker (ordenado por dias DESC) ---" -ForegroundColor Yellow
    Invoke-PsqlTable $sqlCoverage

    Write-Host "`nProxima atualizacao em ${INTERVAL}s..." -ForegroundColor DarkGray
    Start-Sleep -Seconds $INTERVAL
}
