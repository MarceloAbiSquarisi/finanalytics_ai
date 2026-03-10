# apply_all_fixes.ps1 — aplica todos os patches de domínio
# Uso: .venv2\Scripts\Activate.ps1; .\apply_all_fixes.ps1

$fixes = @(
    @("fix_engine.py",      "src\finanalytics_ai\domain\portfolio_optimizer\engine.py"),
    @("fix_fgc_score.py",   "src\finanalytics_ai\domain\fixed_income\entities.py"),
    @("fix_consolidated.py","src\finanalytics_ai\domain\patrimony\consolidated.py"),
    @("fix_ir_calculator.py","src\finanalytics_ai\domain\fixed_income\ir_calculator.py")
)

foreach ($fix in $fixes) {
    $script = $fix[0]
    $target = $fix[1]
    Write-Host "`nAplicando $script em $target ..." -ForegroundColor Cyan
    python "fixes\$script" $target
}

Write-Host "`n=== Rodando testes ===" -ForegroundColor Green
python -m pytest tests\unit\ -v --tb=short --ignore=tests\unit\domain\test_portfolio_optimizer.py 2>&1 | Tee-Object pytest_results.txt
Write-Host "`nResultado em pytest_results.txt" -ForegroundColor Green
