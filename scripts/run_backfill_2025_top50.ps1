# run_backfill_2025_top50.ps1 - Cenario B: Top 50 tickers por liquidez, 2025 completo
# Uso: abrir PowerShell -> .\scripts\run_backfill_2025_top50.ps1
#
# Pre-requisito: profit_agent rodando em :8002 com market_connected=true.

$ErrorActionPreference = "Stop"
Set-Location "D:\Projetos\finanalytics_ai_fresh"

$START = "2025-01-02"
$END   = (Get-Date).ToString("yyyy-MM-dd")   # inclui 2026 ate hoje
$DELAY = 2

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  BACKFILL 2025+2026 - TOP 50 (Cenario B)" -ForegroundColor Cyan
Write-Host "  Periodo : $START -> $END" -ForegroundColor Cyan
Write-Host "  Delay   : ${DELAY}s" -ForegroundColor Cyan
Write-Host "  ETA     : ~5.5 dias continuos (~15700 calls, 30s cada)" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# SQL em here-string single-quote (literal - sem interpretar var)
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
       COALESCE(h.dias, 0) AS dias_2025,
       h.fim AS ultimo
FROM top50 t
LEFT JOIN (
   SELECT ticker,
          COUNT(DISTINCT trade_date::date) AS dias,
          MAX(trade_date::date) AS fim
   FROM market_history_trades
   WHERE trade_date >= '2024-12-01'
   GROUP BY ticker
) h ON h.ticker = t.ticker
ORDER BY t.ticker;
'@

Write-Host "[1/2] Cobertura atual no banco para os 50 tickers (so 2025):" -ForegroundColor Yellow
# Pipe via stdin preserva newlines (o -c "..." com CMD do Windows quebra)
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$sqlCoverage | docker exec -i finanalytics_timescale psql -U finanalytics -d market_data
if ($LASTEXITCODE -ne 0) {
    Write-Host "[AVISO] Query de cobertura falhou (exit=$LASTEXITCODE); seguindo." -ForegroundColor DarkYellow
}
$ErrorActionPreference = $prevEAP

Write-Host "`n[2/2] Iniciando backfill (Ctrl+C interrompe; re-executar e seguro)`n" -ForegroundColor Green

.venv\Scripts\python.exe scripts\backfill_2025_top50.py --start $START --end $END --delay $DELAY

Write-Host "`n[DONE] Backfill 2025 Top 50 finalizado." -ForegroundColor Green
Write-Host "       Acompanhe com .\scripts\monitor_backfill.ps1`n" -ForegroundColor DarkGray
