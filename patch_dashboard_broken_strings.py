"""
Corrige todas as strings JS quebradas em múltiplas linhas no dashboard.html.
Uma string JS não pode ter newline literal — une as linhas afetadas.
"""
from __future__ import annotations
import hashlib, re, sys
from pathlib import Path

TARGET = Path(
    r"D:\Projetos\finanalytics_ai_fresh"
    r"\src\finanalytics_ai\interfaces\api\static\dashboard.html"
)

def find_broken_strings(text: str) -> list[tuple[str, str]]:
    """
    Encontra strings JS com newline literal dentro de aspas simples.
    Padrão: '...\n...' onde a string começa na linha e continua na próxima.
    """
    fixes = []
    # Encontra linhas que terminam com string aberta (sem fechar aspas)
    # e a próxima linha fecha a string
    lines = text.split('\n')
    i = 0
    while i < len(lines) - 1:
        line = lines[i]
        next_line = lines[i + 1]
        # Conta aspas simples não escapadas na linha
        # Se linha termina com string aberta que fecha na próxima linha
        stripped = line.rstrip()
        # Padrão: linha termina dentro de string aspas simples
        # e próxima linha fecha essa string
        if (stripped.count("'") % 2 == 1 and  # aspas desbalanceadas
            not stripped.endswith("'") and      # não fechou na mesma linha
            next_line.strip().startswith('<')):  # próxima é HTML dentro da string
            # Une as duas linhas
            combined = line + next_line
            old = line + '\n' + next_line
            fixes.append((old, combined))
        i += 1
    return fixes

def apply(path: Path, dry: bool = False) -> None:
    raw = path.read_bytes()
    text = raw.decode('utf-8').replace('\r\n', '\n')
    
    fixes = find_broken_strings(text)
    
    if not fixes:
        print("[OK] Nenhuma string quebrada encontrada")
        return
    
    print(f"{'[DRY] ' if dry else ''}Encontradas {len(fixes)} strings quebradas:")
    for old, new in fixes:
        print(f"  L: {repr(old[:80])}")
        print(f"  → {repr(new[:80])}")
        print()
    
    if dry:
        return
    
    patched = text
    for old, new in fixes:
        patched = patched.replace(old, new, 1)
    
    bak = path.with_suffix(f".html.bak_{hashlib.sha256(raw).hexdigest()[:8]}")
    bak.write_bytes(raw)
    print(f"[BACKUP] {bak.name}")
    path.write_bytes(patched.encode('utf-8'))
    print(f"[FIXED] {path.name}")

dry = '--dry-run' in sys.argv
apply(TARGET, dry)
