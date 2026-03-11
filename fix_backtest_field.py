"""
Fix backtest.py: Field(..., "positional string") → Field(..., description="...")
Versão robusta com tokenização simples linha a linha
"""
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
bt = ROOT / "src" / "finanalytics_ai" / "interfaces" / "api" / "routes" / "backtest.py"

if not bt.exists():
    print(f"Arquivo não encontrado: {bt}")
    sys.exit(1)

lines = bt.read_text(encoding="utf-8").splitlines(keepends=True)
changed = 0

for i, line in enumerate(lines):
    # Procura: Field( qualquer_coisa , "string"
    # onde "string" é o 2º argumento posicional (não tem keyword= antes)
    # Não pode já ter description=
    if "Field(" not in line or "description=" in line:
        continue

    # Padrão: Field(ALGO, "TEXTO" onde TEXTO não contém quebra de linha
    m = re.search(r'(Field\([^"\']*?),\s*("(?:[^"\\]|\\.)*?")\s*([,)])', line)
    if m:
        new_line = line[:m.start(2)] + "description=" + line[m.start(2):]
        lines[i] = new_line
        changed += 1
        print(f"  L{i+1}: {line.rstrip()}")
        print(f"    → {new_line.rstrip()}")

if changed:
    bt.write_text("".join(lines), encoding="utf-8")
    print(f"\n✓ {changed} ocorrências corrigidas em backtest.py")
else:
    print("Nenhuma ocorrência encontrada — talvez já corrigido ou padrão diferente")
    # Vamos inspecionar as linhas problemáticas
    for i in [33, 34, 35, 36, 203, 204, 205, 209, 210, 302, 303, 304, 308]:
        if i < len(lines):
            print(f"  L{i+1}: {lines[i].rstrip()}")
