# run_backfill_resume.ps1 - Backfill incremental por ticker (continua do ultimo dia coletado)
# Uso: abrir PowerShell -> .\scripts\run_backfill_resume.ps1
#
# Diferenca vs run_backfill.ps1:
#   - run_backfill.ps1         : start FIXO 2026-01-02 p/ todos os tickers
#   - run_backfill_resume.ps1  : start = ultimo_dia_coletado + 1, por ticker

$ErrorActionPreference = "Stop"
Set-Location "D:\Projetos\finanalytics_ai_fresh"

$END            = (Get-Date).ToString("yyyy-MM-dd")
$FALLBACK_START = "2026-01-02"
$DELAY          = 2

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  BACKFILL INCREMENTAL (resume por ticker)" -ForegroundColor Cyan
Write-Host "  End            : $END" -ForegroundColor Cyan
Write-Host "  Fallback start : $FALLBACK_START" -ForegroundColor Cyan
Write-Host "  Delay          : ${DELAY}s" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

Write-Host "[1/3] Ativando todos os tickers..." -ForegroundColor Yellow
docker exec finanalytics_timescale psql -U finanalytics -d market_data -c `
    "UPDATE profit_history_tickers SET active = TRUE WHERE active = FALSE;"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[AVISO] Falha ao ativar tickers; continuando" -ForegroundColor DarkYellow
}

Write-Host "`n[2/3] Estado atual (ultimo dia coletado por ticker):" -ForegroundColor Yellow
docker exec finanalytics_timescale psql -U finanalytics -d market_data -c `
    "SELECT t.ticker, t.exchange, t.active,
            COALESCE(h.dias, 0) AS dias_coletados,
            h.fim AS ultimo_dia,
            CASE WHEN h.fim IS NULL THEN '$FALLBACK_START'
                 ELSE (h.fim + 1)::text END AS proximo_dia
     FROM profit_history_tickers t
     LEFT JOIN (
         SELECT ticker,
                COUNT(DISTINCT trade_date::date) AS dias,
                MAX(trade_date::date) AS fim
         FROM market_history_trades
         WHERE trade_date >= '2025-12-01'
         GROUP BY ticker
     ) h ON h.ticker = t.ticker
     WHERE t.active = TRUE
     ORDER BY t.ticker;"

Write-Host "`n[3/3] Iniciando backfill incremental..." -ForegroundColor Green
Write-Host "       Ctrl+C para interromper (seguro re-executar)`n" -ForegroundColor DarkGray

.venv\Scripts\python.exe scripts\backfill_resume.py `
    --end $END `
    --fallback-start $FALLBACK_START `
    --delay $DELAY

Write-Host "`n[DONE] Backfill incremental finalizado." -ForegroundColor Green
Write-Host "       Use .\scripts\monitor_backfill.ps1 p/ ver o resultado.`n" -ForegroundColor DarkGray
