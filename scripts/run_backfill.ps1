# run_backfill.ps1 — Executa backfill completo em terminal separado
# Uso: abrir PowerShell -> .\scripts\run_backfill.ps1
#
# Ativa todos os tickers, depois roda backfill_history.py com resume automatico.
# O script pula dias ja coletados — seguro re-executar quantas vezes quiser.

$ErrorActionPreference = "Stop"
Set-Location "D:\Projetos\finanalytics_ai_fresh"

$DSN   = "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
$END   = (Get-Date).ToString("yyyy-MM-dd")
$START = "2026-01-02"
$DELAY = 2

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  BACKFILL HISTORICO" -ForegroundColor Cyan
Write-Host "  Periodo: $START -> $END" -ForegroundColor Cyan
Write-Host "  Delay  : ${DELAY}s" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# 1. Ativa tickers inativos (ITUB4, PETR4, VALE3 estao desativados)
Write-Host "[1/3] Ativando todos os tickers..." -ForegroundColor Yellow
docker exec finanalytics_timescale psql -U finanalytics -d market_data -c `
    "UPDATE profit_history_tickers SET active = TRUE WHERE active = FALSE;"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[AVISO] Falha ao ativar tickers — continuando com os ativos" -ForegroundColor DarkYellow
}

# 2. Mostra estado atual
Write-Host "`n[2/3] Estado atual:" -ForegroundColor Yellow
docker exec finanalytics_timescale psql -U finanalytics -d market_data -c `
    "SELECT t.ticker, t.exchange, t.active,
            COALESCE(h.dias, 0) AS dias_coletados,
            h.fim AS ultimo_dia
     FROM profit_history_tickers t
     LEFT JOIN (
         SELECT ticker,
                COUNT(DISTINCT trade_date::date) AS dias,
                MAX(trade_date::date) AS fim
         FROM market_history_trades
         WHERE trade_date >= '2025-12-01'
         GROUP BY ticker
     ) h ON h.ticker = t.ticker
     ORDER BY t.ticker;"

# 3. Executa backfill
Write-Host "`n[3/3] Iniciando backfill..." -ForegroundColor Green
Write-Host "       Ctrl+C para interromper (seguro re-executar depois)`n" -ForegroundColor DarkGray

.venv\Scripts\python.exe scripts\backfill_history.py --start $START --end $END --delay $DELAY

Write-Host "`n[DONE] Backfill finalizado." -ForegroundColor Green
Write-Host "       Execute .\scripts\monitor_backfill.ps1 para ver o resultado.`n" -ForegroundColor DarkGray
