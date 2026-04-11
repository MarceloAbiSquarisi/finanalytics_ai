# install_service.ps1
# Instala o finanalytics_ai como servico Windows via NSSM
# Requer: executar como Administrador
# Uso: .\install_service.ps1

param(
    [string]$Action = "install",  # install | uninstall | status | start | stop
    [string]$NssmPath = "C:\nssm\nssm.exe"
)

$ROOT        = "D:\Projetos\finanalytics_ai_fresh"
$SERVICE     = "FinAnalyticsAI"
$UV_PATH     = (Get-Command uv -ErrorAction SilentlyContinue)?.Source
$LOG_DIR     = "$ROOT\logs"
$PYTHON_CMD  = "uvicorn"

# ── Verifica privilegios de admin ─────────────────────────────────────────────
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERRO: Execute como Administrador (clique com botao direito -> Executar como administrador)" -ForegroundColor Red
    exit 1
}

# ── Verifica NSSM ─────────────────────────────────────────────────────────────
if (-not (Test-Path $NssmPath)) {
    Write-Host "NSSM nao encontrado em $NssmPath" -ForegroundColor Yellow
    Write-Host "Baixando NSSM automaticamente..." -ForegroundColor Cyan
    
    $nssmDir = "C:\nssm"
    New-Item -ItemType Directory -Path $nssmDir -Force | Out-Null
    $nssmUrl = "https://nssm.cc/release/nssm-2.24.zip"
    $nssmZip = "$env:TEMP\nssm.zip"
    
    Invoke-WebRequest -Uri $nssmUrl -OutFile $nssmZip -UseBasicParsing
    Expand-Archive -Path $nssmZip -DestinationPath "$env:TEMP\nssm_extract" -Force
    Copy-Item "$env:TEMP\nssm_extract\nssm-2.24\win64\nssm.exe" "$nssmDir\nssm.exe" -Force
    Remove-Item $nssmZip, "$env:TEMP\nssm_extract" -Recurse -Force
    Write-Host "NSSM instalado em $nssmDir" -ForegroundColor Green
}

# ── Pasta de logs ─────────────────────────────────────────────────────────────
New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null

# ── Acoes ─────────────────────────────────────────────────────────────────────
switch ($Action) {

    "install" {
        Write-Host "`n=== Instalando servico $SERVICE ===" -ForegroundColor Cyan

        # Encontra o executavel UV no venv
        $uvExe = "$ROOT\.venv\Scripts\uvicorn.exe"
        if (-not (Test-Path $uvExe)) {
            $uvExe = (Get-Command uvicorn -ErrorAction SilentlyContinue)?.Source
        }
        if (-not $uvExe) {
            Write-Host "ERRO: uvicorn nao encontrado. Execute 'uv sync' primeiro." -ForegroundColor Red
            exit 1
        }

        # Remove servico anterior se existir
        & $NssmPath stop    $SERVICE 2>$null
        & $NssmPath remove  $SERVICE confirm 2>$null

        # Instala o servico
        & $NssmPath install $SERVICE $uvExe
        & $NssmPath set     $SERVICE AppParameters "finanalytics_ai.interfaces.api.run:app --host 0.0.0.0 --port 8000 --no-access-log"
        & $NssmPath set     $SERVICE AppDirectory  $ROOT

        # Variaveis de ambiente
        $envVars = "DATABASE_URL=postgresql+asyncpg://finanalytics:secret@localhost:5432/finanalytics"
        $envVars += "`tASYNC_DATABASE_URL=postgresql+asyncpg://finanalytics:secret@localhost:5432/finanalytics"
        $envVars += "`tPROFIT_TIMESCALE_DSN=postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
        & $NssmPath set $SERVICE AppEnvironmentExtra $envVars

        # Logs
        & $NssmPath set $SERVICE AppStdout  "$LOG_DIR\api_stdout.log"
        & $NssmPath set $SERVICE AppStderr  "$LOG_DIR\api_stderr.log"
        & $NssmPath set $SERVICE AppRotateFiles 1
        & $NssmPath set $SERVICE AppRotateBytes 10485760  # 10MB

        # Restart automatico em caso de falha
        & $NssmPath set $SERVICE AppExit     Default  Restart
        & $NssmPath set $SERVICE AppRestartDelay 5000  # 5s

        # Dependencias (Docker deve estar rodando)
        & $NssmPath set $SERVICE DependOnService "com.docker.service"

        # Inicia automaticamente com o Windows
        & $NssmPath set $SERVICE Start SERVICE_AUTO_START

        # Inicia o servico agora
        & $NssmPath start $SERVICE

        Write-Host "`n=== Servico instalado e iniciado! ===" -ForegroundColor Green
        Write-Host "  Dashboard: http://localhost:8000/"
        Write-Host "  Logs:      $LOG_DIR\api_stdout.log"
        Write-Host "  Gerenciar: services.msc (busque FinAnalyticsAI)"
    }

    "uninstall" {
        Write-Host "Removendo servico $SERVICE..." -ForegroundColor Yellow
        & $NssmPath stop   $SERVICE
        & $NssmPath remove $SERVICE confirm
        Write-Host "Removido." -ForegroundColor Green
    }

    "start" {
        Write-Host "Iniciando $SERVICE..." -ForegroundColor Cyan
        & $NssmPath start $SERVICE
    }

    "stop" {
        Write-Host "Parando $SERVICE..." -ForegroundColor Yellow
        & $NssmPath stop $SERVICE
    }

    "status" {
        & $NssmPath status $SERVICE
        Write-Host ""
        Get-Service -Name $SERVICE -ErrorAction SilentlyContinue | 
            Select-Object Name, Status, StartType
    }
}
