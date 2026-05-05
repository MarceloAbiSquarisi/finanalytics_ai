# Wrapper invocado pelo Task Scheduler 21:00 BRT diariamente.
# Coordena Fase B (daily de todos subscritos) <-> Fase A/C (backfill 2y WIN/WDO):
#
#   Step 1: para bf2y.py (Fase A) se estiver rodando
#   Step 2: roda backfill_today_subscribed.py (Fase B), bloqueante
#   Step 3: relanca bf2y.py em background (Fase C, retoma de onde parou)
#
# Logs em logs/daily_backfill/<datetime>.log

$ErrorActionPreference = 'Continue'
$logDir = 'D:\Projetos\finanalytics_ai_fresh\logs\daily_backfill'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logFile = Join-Path $logDir ("daily_$(Get-Date -Format 'yyyyMMdd_HHmmss').log")

function Log($msg) {
    $line = "{0} {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg
    Add-Content -Path $logFile -Value $line
    Write-Host $line
}

Log "=== START run_daily_backfill ==="

# Step 1: pausa Fase A (backfill 2y)
Log "Step 1: detectando Fase A (bf2y.py)..."
docker exec finanalytics_api sh -c "ps -ef | grep 'python /tmp/bf2y' | grep -v grep > /data/_bf2y_pre.txt"
Start-Sleep 1
$prePid = Get-Content E:\finanalytics_data\_bf2y_pre.txt -Raw -ErrorAction SilentlyContinue
if ($prePid -and $prePid.Trim() -ne "") {
    Log "Fase A rodando: $($prePid.Trim()) -- pausando..."
    docker exec finanalytics_api sh -c "pkill -f bf2y.py"
    Start-Sleep 3
    Log "Fase A pausada."
} else {
    Log "Fase A nao estava rodando."
}

# Step 2: Fase B (daily backfill bloqueante)
Log "Step 2: iniciando Fase B (daily de todos subscritos)..."
docker cp D:\Projetos\finanalytics_ai_fresh\scripts\backfill_today_subscribed.py finanalytics_api:/tmp/bft.py 2>&1 | Out-Null

docker exec `
    -e PROFIT_AGENT_URL=http://host.docker.internal:8002 `
    -e PROFIT_TIMESCALE_DSN=postgresql://finanalytics:timescale_secret@timescale:5432/market_data `
    finanalytics_api sh -c "python /tmp/bft.py > /data/_daily_bft.log 2>&1"

$exitCode = $LASTEXITCODE
Log "Step 2 concluido (exit=$exitCode)."

if (Test-Path E:\finanalytics_data\_daily_bft.log) {
    $tail = Get-Content E:\finanalytics_data\_daily_bft.log -Tail 5 -ErrorAction SilentlyContinue
    Log "Daily backfill tail:"
    foreach ($t in $tail) { Log "  $t" }
    Copy-Item E:\finanalytics_data\_daily_bft.log (Join-Path $logDir "daily_bft_$(Get-Date -Format 'yyyyMMdd_HHmmss').log") -Force
}

# Step 3: Fase C (retomar backfill 2y, mesmo se Fase B falhou)
Log "Step 3: retomando Fase C (backfill 2y WIN/WDO)..."
& 'D:\Projetos\finanalytics_ai_fresh\scripts\start_backfill_2y.ps1'
Log "Step 3 concluido."

Log "=== END run_daily_backfill ==="
