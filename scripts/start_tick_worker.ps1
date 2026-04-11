# start_tick_worker.ps1
# Inicia coleta de ticks em tempo real via DLL Nelogica
# Uso: .\start_tick_worker.ps1  (rodar DURANTE o pregao)

$ROOT = "D:\Projetos\finanalytics_ai_fresh"
Set-Location $ROOT

Write-Host "`n=== Tick Worker (DLL Nelogica) ===" -ForegroundColor Cyan

$env:PROFIT_ACTIVATION_KEY = "1834404599450006070"
$env:PROFIT_USERNAME       = "marceloabisquarisi@gmail.com"
$env:PROFIT_PASSWORD       = 'o<{zr*QQ52*g[yzn'
$env:PROFIT_DLL_PATH       = "C:\Nelogica\profitdll.dll"
$env:PROFIT_TICKERS        = "PETR4:B,VALE3:B,WINFUT:F"
$env:REDIS_URL             = "redis://localhost:6379/0"

Write-Host "Tickers: PETR4, VALE3, WINFUT" -ForegroundColor White
Write-Host "Pressione Ctrl+C para parar`n" -ForegroundColor Gray

uv run python profit_tick_worker.py
