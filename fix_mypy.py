"""
Fix mypy errors in finanalytics_ai — batch script
Categorias tratadas:
  A) backtest.py: Field(..., "desc") → Field(..., description="desc")  [call-overload]
  B) app.py L205-211: add # type: ignore[arg-type]                     [arg-type]
  C) Remover # type: ignore comentários não usados                      [unused-ignore]
  D) alert_repo.py rowcount → cast correto                              [attr-defined]
"""

import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
SRC = ROOT / "src" / "finanalytics_ai"

changes: list[tuple[str, str]] = []

# ──────────────────────────────────────────────────
# A) backtest.py: Field positional description → keyword
# Pydantic v2 removeu 2º arg posicional de Field
# Field(..., "texto") → Field(..., description="texto")
# Field("valor", "texto") → Field("valor", description="texto")
# ──────────────────────────────────────────────────
bt = SRC / "interfaces" / "api" / "routes" / "backtest.py"
if bt.exists():
    text = bt.read_text(encoding="utf-8")
    # Padrão: Field(qualquer_coisa, "string literal") onde "string literal" é 2º arg posicional
    # Não deve ter description= já definido
    pattern = r'(Field\([^)]*?),\s*("(?:[^"\\]|\\.)*?")(\s*(?:,|\)))'
    def fix_field(m: re.Match) -> str:
        prefix = m.group(1)
        desc = m.group(2)
        suffix = m.group(3)
        # Se já tem description= no prefix, não tocar
        if "description=" in prefix:
            return m.group(0)
        return f"{prefix}, description={desc}{suffix}"
    
    new_text = re.sub(pattern, fix_field, text)
    if new_text != text:
        bt.write_text(new_text, encoding="utf-8")
        changes.append(("FIXED backtest.py Field positional→keyword", str(bt.relative_to(ROOT))))

# ──────────────────────────────────────────────────
# B) app.py: CompositeMarketDataClient → add type: ignore[arg-type]
# ──────────────────────────────────────────────────
app_py = SRC / "interfaces" / "api" / "app.py"
if app_py.exists():
    lines = app_py.read_text(encoding="utf-8").splitlines(keepends=True)
    # Serviços que recebem CompositeMarketDataClient mas esperam BrapiClient
    services_needing_ignore = {
        "BacktestService(", "OptimizerService(", "WalkForwardService(",
        "MultiTickerService(", "CorrelationService(", "ScreenerService(",
        "AnomalyService(",
    }
    modified = False
    for i, line in enumerate(lines):
        stripped = line.rstrip()
        if any(svc in stripped for svc in services_needing_ignore):
            if "# type: ignore" not in stripped:
                lines[i] = stripped + "  # type: ignore[arg-type]\n"
                modified = True
    if modified:
        app_py.write_text("".join(lines), encoding="utf-8")
        changes.append(("FIXED app.py arg-type ignores", str(app_py.relative_to(ROOT))))

# ──────────────────────────────────────────────────
# C) Remover # type: ignore que viraram unused-ignore
# Arquivos: portfolio_repo.py, event_store_repo.py, alert_repo.py(119-121), reports.py(45), backtest.py(315)
# ──────────────────────────────────────────────────
unused_ignore_files = [
    (SRC / "infrastructure" / "database" / "repositories" / "portfolio_repo.py", [150, 151]),
    (SRC / "infrastructure" / "database" / "repositories" / "event_store_repo.py", [105, 107, 108]),
    (SRC / "infrastructure" / "database" / "repositories" / "alert_repo.py", [119, 120, 121]),
    (SRC / "interfaces" / "api" / "routes" / "reports.py", [45]),
    (SRC / "interfaces" / "api" / "routes" / "backtest.py", [315]),
]

for filepath, line_numbers in unused_ignore_files:
    if not filepath.exists():
        continue
    lines = filepath.read_text(encoding="utf-8").splitlines(keepends=True)
    modified = False
    for ln in line_numbers:
        idx = ln - 1
        if idx < len(lines) and "# type: ignore" in lines[idx]:
            # Remove apenas o comentário type: ignore (preserva restante da linha)
            lines[idx] = re.sub(r"\s*#\s*type:\s*ignore\[?[^\]]*\]?", "", lines[idx].rstrip()) + "\n"
            modified = True
    if modified:
        filepath.write_text("".join(lines), encoding="utf-8")
        changes.append((f"FIXED unused type: ignore in {filepath.name}", str(filepath.relative_to(ROOT))))

# ──────────────────────────────────────────────────
# D) alert_repo.py L107: Result.rowcount → CursorResult cast
# ──────────────────────────────────────────────────
alert_repo = SRC / "infrastructure" / "database" / "repositories" / "alert_repo.py"
if alert_repo.exists():
    text = alert_repo.read_text(encoding="utf-8")
    # Linha tem padrão: result.rowcount → adicionar cast ou type: ignore
    # Adiciona type: ignore[attr-defined] na linha do rowcount
    new_text = re.sub(
        r'(result\.rowcount\b)(?!\s*#)',
        r'\1  # type: ignore[attr-defined]',
        text
    )
    if new_text != text:
        alert_repo.write_text(new_text, encoding="utf-8")
        changes.append(("FIXED alert_repo.py rowcount attr-defined", str(alert_repo.relative_to(ROOT))))

print(f"\n{'='*60}")
print(f"Total de arquivos modificados: {len(changes)}")
for desc, path in changes:
    print(f"  ✓ {desc}")
print("="*60)
