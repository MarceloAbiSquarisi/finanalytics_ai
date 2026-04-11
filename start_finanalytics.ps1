# start_finanalytics.ps1 - v2
# Sobe todo o ambiente finanalytics_ai na ordem correta
# Uso: .\start_finanalytics.ps1 [-ComTicks]
param([switch]$ComTicks)

$ROOT = "D:\Projetos\finanalytics_ai_fresh"
Set-Location $ROOT

Write-Host ""
Write-Host "╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     finanalytics_ai  —  Startup      ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════╝" -ForegroundColor Cyan

# ── 1. Verifica Docker ────────────────────────────────────────────────────────
Write-Host "`n[1/4] Verificando Docker..." -ForegroundColor Yellow
try {
    $null = docker ps 2>&1
    if ($LASTEXITCODE -ne 0) { throw }
    Write-Host "  Docker: OK" -ForegroundColor Green
} catch {
    Write-Host "  ERRO: Docker Desktop nao esta rodando. Inicie-o e tente novamente." -ForegroundColor Red
    exit 1
}

# ── 2. Para container da API Docker (libera porta 8000) ───────────────────────
Write-Host "`n[2/4] Liberando porta 8000..." -ForegroundColor Yellow
docker stop finanalytics_api 2>$null | Out-Null
Start-Sleep -Seconds 1
Write-Host "  Porta 8000 liberada." -ForegroundColor Green

# ── 3. Sobe API principal ─────────────────────────────────────────────────────
Write-Host "`n[3/4] Subindo API FastAPI..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", @"
`$Host.UI.RawUI.WindowTitle = 'finanalytics — API'
Set-Location '$ROOT'
`$env:DATABASE_URL       = 'postgresql+asyncpg://finanalytics:secret@localhost:5432/finanalytics'
`$env:ASYNC_DATABASE_URL = 'postgresql+asyncpg://finanalytics:secret@localhost:5432/finanalytics'
`$env:PROFIT_TIMESCALE_DSN = 'postgresql://finanalytics:timescale_secret@localhost:5433/market_data'
Write-Host '[API] Iniciando servidor...' -ForegroundColor Cyan
uv run uvicorn 'finanalytics_ai.interfaces.api.run:app' --host 0.0.0.0 --port 8000 --no-access-log
"@

# Aguarda API subir (testa /api/v1/marketdata/live/tickers)
Write-Host "  Aguardando API inicializar..." -ForegroundColor Gray
$tries = 0
do {
    Start-Sleep -Seconds 3
    $tries++
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:8000/api/v1/marketdata/live/tickers" -TimeoutSec 2 -ErrorAction Stop
        $ok = $true
    } catch {
        $ok = $false
    }
} while (-not $ok -and $tries -lt 20)

if ($ok) {
    Write-Host "  API: OK (${tries}x3s)" -ForegroundColor Green
} else {
    Write-Host "  API: aguardando (verificar terminal da API)" -ForegroundColor Yellow
}

# ── 4. Sobe TapeService ───────────────────────────────────────────────────────
Write-Host "`n[4/4] Subindo TapeService..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", @"
`$Host.UI.RawUI.WindowTitle = 'finanalytics — TapeService'
Set-Location '$ROOT'
`$env:REDIS_URL        = 'redis://localhost:6379/0'
`$env:TIMESCALE_DSN    = 'postgresql://finanalytics:timescale_secret@localhost:5433/market_data'
`$env:OHLC_RESOLUTIONS = '1,5,15,60'
`$env:FLUSH_INTERVAL   = '5'
Write-Host '[TapeService] Iniciando...' -ForegroundColor Cyan
uv run python tape_service.py
"@
Write-Host "  TapeService: iniciado." -ForegroundColor Green

# ── Opcional: Tick Worker ─────────────────────────────────────────────────────
if ($ComTicks) {
    Write-Host "`n[+] Subindo Tick Worker (DLL Nelogica)..." -ForegroundColor Yellow
    Start-Process powershell -ArgumentList "-NoExit", "-Command", @"
`$Host.UI.RawUI.WindowTitle = 'finanalytics — TickWorker'
Set-Location '$ROOT'
`$env:PROFIT_ACTIVATION_KEY = '1834404599450006070'
`$env:PROFIT_USERNAME       = 'marceloabisquarisi@gmail.com'
`$env:PROFIT_PASSWORD       = 'o<{zr*QQ52*g[yzn'
`$env:PROFIT_DLL_PATH       = 'C:\Nelogica\profitdll.dll'
`$env:PROFIT_TICKERS        = 'PETR4:B,VALE3:B,WINFUT:F'
`$env:REDIS_URL             = 'redis://localhost:6379/0'
Write-Host '[TickWorker] Iniciando...' -ForegroundColor Cyan
uv run python profit_tick_worker.py
"@
    Write-Host "  Tick Worker: iniciado." -ForegroundColor Green
}

# ── Resumo ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "╔══════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║         Sistema no ar!               ║" -ForegroundColor Green
Write-Host "╚══════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  Dashboard : http://localhost:8000/" -ForegroundColor White
Write-Host "  Swagger   : http://localhost:8000/docs" -ForegroundColor White
Write-Host "  Login     : marceloabisquarisi@gmail.com / admin123" -ForegroundColor Gray
Write-Host ""
if (-not $ComTicks) {
    Write-Host "  Para iniciar coleta ao vivo (durante pregao):" -ForegroundColor Gray
    Write-Host "  .\start_finanalytics.ps1 -ComTicks" -ForegroundColor Yellow
}
Write-Host ""
