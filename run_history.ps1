# run_history.ps1 — um ticker por vez

$env:PROFIT_DLL_PATH       = "C:\Nelogica\profitdll.dll"
$env:PROFIT_ACTIVATION_KEY = "1834404599450006070"
$env:PROFIT_USERNAME       = "marceloabisquarisi@gmail.com"
$env:PROFIT_PASSWORD       = 'o<{zr*QQ52*g[yzn'
$env:TIMESCALE_DSN         = "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"

# Um ticker por vez — troque para WDOFUT:F depois
$env:PROFIT_TICKERS = "WINFUT:F"

$env:HISTORY_DATE_START    = "01/04/2026 09:00:00"
$env:HISTORY_DATE_END      = "11/04/2026 18:00:00"
$env:HISTORY_RESOLUTIONS   = "1,5,15,60,D"
$env:HISTORY_TIMEOUT       = "300"

uv run python -m finanalytics_ai.workers.profit_history_worker
