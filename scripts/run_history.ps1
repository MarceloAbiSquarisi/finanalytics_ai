# run_history.ps1 — Executa profit_history_worker.py com credenciais corretas
# Uso: .\run_history.ps1 [-Tickers "PETR4:B,VALE3:B"] [-Start "09/04/2026 09:00:00"] [-End "10/04/2026 18:00:00"]

param(
    [string]$Tickers = "PETR4:B,VALE3:B,WINFUT:F",
    [string]$Start   = "09/04/2026 09:00:00",
    [string]$End     = "10/04/2026 18:00:00"
)

$env:PROFIT_ACTIVATION_KEY = "1834404599450006070"
$env:PROFIT_USERNAME       = "marceloabisquarisi@gmail.com"
$env:PROFIT_PASSWORD       = 'o<{zr*QQ52*g[yzn'
$env:PROFIT_DLL_PATH       = "C:\Nelogica\profitdll.dll"
$env:TIMESCALE_DSN         = "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
$env:REDIS_URL             = "redis://localhost:6379/0"
$env:PROFIT_TICKERS        = $Tickers
$env:HISTORY_DATE_START    = $Start
$env:HISTORY_DATE_END      = $End
$env:HISTORY_RESOLUTIONS   = "1,5,15,60,D"
$env:HISTORY_TIMEOUT       = "120"

Write-Host "Tickers : $Tickers"
Write-Host "Start   : $Start"
Write-Host "End     : $End"

uv run python profit_history_worker.py
