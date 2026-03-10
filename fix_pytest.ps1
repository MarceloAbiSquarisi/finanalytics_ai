# fix_pytest.ps1
# Uso: .venv2\Scripts\Activate.ps1; .\fix_pytest.ps1

Write-Host "`n=== DIAGNOSTICO ===" -ForegroundColor Cyan

$pyexe = (Get-Command python).Source
Write-Host "Python ativo: $pyexe"

$pkg = python -c "import finanalytics_ai; print(finanalytics_ai.__file__)" 2>&1
Write-Host "finanalytics_ai: $pkg"

$domain = python -c "import finanalytics_ai.domain; print('domain OK')" 2>&1
Write-Host "finanalytics_ai.domain: $domain"

if ($domain -notmatch "domain OK") {
    Write-Host "`n=== INSTALANDO PACOTE NO VENV ===" -ForegroundColor Yellow
    pip install -e "." --no-deps
}

# Cria conftest.py raiz com here-string (evita conflito de aspas)
$conftest = @'
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
'@
Set-Content -Path conftest.py -Value $conftest -Encoding UTF8
Write-Host "conftest.py raiz criado"

# Limpa cache
if (Test-Path ".pytest_cache") { Remove-Item ".pytest_cache" -Recurse -Force }
Get-ChildItem -Path "." -Filter "__pycache__" -Recurse -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force

Write-Host "`n=== RODANDO TESTES ===" -ForegroundColor Cyan
pytest tests\unit\ -v --tb=short 2>&1 | Tee-Object pytest_results.txt

Write-Host "`nResultado salvo em pytest_results.txt" -ForegroundColor Green
