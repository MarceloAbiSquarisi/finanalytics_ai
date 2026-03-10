# deploy_fixes.ps1
# Move os arquivos corrigidos de D:\Downloads para as pastas corretas do projeto
# Uso: .\deploy_fixes.ps1 [-ProjectRoot "D:\Projetos\finanalytics_ai"] [-Source "D:\Downloads"]

param(
    [string]$ProjectRoot = "D:\Projetos\finanalytics_ai",
    [string]$Source      = "D:\Downloads"
)

$files = @{
    "dependencies.py"       = "src\finanalytics_ai\interfaces\api\dependencies.py"
    "jwt_handler.py"        = "src\finanalytics_ai\infrastructure\auth\jwt_handler.py"
    "__init__.py"           = "src\finanalytics_ai\infrastructure\notifications\__init__.py"
    "market_data_client.py" = "src\finanalytics_ai\infrastructure\adapters\market_data_client.py"
    "entities.py"           = "src\finanalytics_ai\domain\watchlist\entities.py"
    "etf_service.py"        = "src\finanalytics_ai\application\services\etf_service.py"
    "fixed_income.py"       = "src\finanalytics_ai\interfaces\api\routes\fixed_income.py"
    "technical.py"          = "src\finanalytics_ai\domain\indicators\technical.py"
    "brapi_client.py"       = "src\finanalytics_ai\infrastructure\adapters\brapi_client.py"
}

$ok    = 0
$skipped = 0
$errors  = 0

foreach ($filename in $files.Keys) {
    $src  = Join-Path $Source $filename
    $dest = Join-Path $ProjectRoot $files[$filename]

    if (-not (Test-Path $src)) {
        Write-Warning "  NAO ENCONTRADO  $src"
        $skipped++
        continue
    }

    try {
        Copy-Item -Path $src -Destination $dest -Force
        Write-Host "  OK  $filename  -->  $($files[$filename])" -ForegroundColor Green
        $ok++
    } catch {
        Write-Error "  ERRO  $filename : $_"
        $errors++
    }
}

Write-Host ""
Write-Host "Resultado: $ok copiados  |  $skipped nao encontrados  |  $errors erros" -ForegroundColor Cyan
