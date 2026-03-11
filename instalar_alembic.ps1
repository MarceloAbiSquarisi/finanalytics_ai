param(
    [string]$Downloads = "D:\Downloads",
    [string]$Projeto   = "D:\Projetos\finanalytics_ai"
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=== Instalando Alembic ===" -ForegroundColor Cyan

New-Item -ItemType Directory -Path "$Projeto\alembic\versions" -Force | Out-Null

$arquivos = @{
    "alembic.ini"             = "$Projeto\alembic.ini"
    "env.py"                  = "$Projeto\alembic\env.py"
    "script.py.mako"          = "$Projeto\alembic\script.py.mako"
    "0001_baseline.py"        = "$Projeto\alembic\versions\0001_baseline.py"
    "0002_portfolio_multi.py" = "$Projeto\alembic\versions\0002_portfolio_multi.py"
    "portfolio_repo.py"       = "$Projeto\src\finanalytics_ai\infrastructure\database\repositories\portfolio_repo.py"
}

$ok    = 0
$erros = 0

foreach ($item in $arquivos.GetEnumerator()) {
    $origem  = Join-Path $Downloads $item.Key
    $destino = $item.Value
    if (Test-Path $origem) {
        Copy-Item -Path $origem -Destination $destino -Force
        Write-Host "  [ok] $($item.Key)" -ForegroundColor Green
        $ok++
    } else {
        Write-Host "  [FALTANDO] $origem" -ForegroundColor Red
        $erros++
    }
}

Write-Host ""
if ($erros -eq 0) {
    Write-Host "=== $ok arquivos instalados! ===" -ForegroundColor Green
    Write-Host ""
    Write-Host "Proximos passos:" -ForegroundColor Cyan
    Write-Host "  alembic stamp 0001_baseline"
    Write-Host "  alembic upgrade head"
    Write-Host "  alembic current"
} else {
    Write-Host "=== $ok ok / $erros faltando ===" -ForegroundColor Red
}
