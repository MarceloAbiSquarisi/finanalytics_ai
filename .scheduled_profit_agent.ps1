$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path 'D:\Investimentos\FinAnalytics_AI\Melhorias\logs\profit_agent_starter.log' -Value "
=== $ts == iniciando ==="

# Verifica que a DLL existe (pre-requisito)
$dllPath = 'C:\Nelogica\profitdll.dll'
if (-not (Test-Path $dllPath)) {
    Add-Content -Path 'D:\Investimentos\FinAnalytics_AI\Melhorias\logs\profit_agent_starter.log' -Value "$(Get-Date -Format HH:mm:ss) DLL nao encontrada em $dllPath. Abortando."
    exit 1
}

# Se agent ja esta no ar, abortar (idempotente)
try {
    $resp = Invoke-WebRequest -Uri 'http://localhost:8002/health' -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
    if ($resp.StatusCode -eq 200) {
        Add-Content -Path 'D:\Investimentos\FinAnalytics_AI\Melhorias\logs\profit_agent_starter.log' -Value "$(Get-Date -Format HH:mm:ss) profit_agent ja esta rodando (health 200). Skip."
        exit 0
    }
} catch { }

# Mata instancias antigas
Get-WmiObject Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*profit_agent*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 3

# Inicia em background (separado da task) com redirect de logs
$out = 'D:\Investimentos\FinAnalytics_AI\Melhorias\logs\profit_agent_stdout.log'
$err = 'D:\Investimentos\FinAnalytics_AI\Melhorias\logs\profit_agent_stderr.log'
Start-Process -FilePath 'D:\Projetos\finanalytics_ai_fresh\.venv\Scripts\python.exe' `
    -ArgumentList 'D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\workers\profit_agent.py' `
    -WorkingDirectory 'D:\Projetos\finanalytics_ai_fresh' `
    -WindowStyle Hidden `
    -RedirectStandardOutput $out `
    -RedirectStandardError  $err
Add-Content -Path 'D:\Investimentos\FinAnalytics_AI\Melhorias\logs\profit_agent_starter.log' -Value "$(Get-Date -Format HH:mm:ss) profit_agent iniciado (stdout=$out)"

# Aguarda /health responder (timeout 60s)
$ok = $false
for ($i = 0; $i -lt 12; $i++) {
    Start-Sleep -Seconds 5
    try {
        $h = Invoke-WebRequest -Uri 'http://localhost:8002/health' -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
        if ($h.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
}
if ($ok) {
    Add-Content -Path 'D:\Investimentos\FinAnalytics_AI\Melhorias\logs\profit_agent_starter.log' -Value "$(Get-Date -Format HH:mm:ss) /health 200 OK"
} else {
    Add-Content -Path 'D:\Investimentos\FinAnalytics_AI\Melhorias\logs\profit_agent_starter.log' -Value "$(Get-Date -Format HH:mm:ss) /health nao respondeu em 60s. Verificar profit_agent_stderr.log"
    exit 1
}
