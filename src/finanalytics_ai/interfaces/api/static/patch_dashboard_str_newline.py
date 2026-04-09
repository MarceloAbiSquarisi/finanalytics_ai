"""
Corrige strings JS quebradas em múltiplas linhas no dashboard.html.
"""
from pathlib import Path
import hashlib, sys

TARGET = Path(
    r"D:\Projetos\finanalytics_ai_fresh"
    r"\src\finanalytics_ai\interfaces\api\static\dashboard.html"
)

FIXES = [
    # Sem posicoes string quebrada
    (
        "Sem posicoes\r\n</div>'",
        "Sem posicoes</div>'"
    ),
    (
        "Sem posicoes\n</div>'",
        "Sem posicoes</div>'"
    ),
]

def fix(path, dry=False):
    raw = path.read_bytes()
    text = raw.decode('utf-8')
    changed = []
    for old, new in FIXES:
        if old in text:
            text = text.replace(old, new)
            changed.append(repr(old[:40]))
    if not changed:
        print("[OK] Nada a corrigir")
        return
    if dry:
        print(f"[DRY] Corrigiria: {changed}")
        return
    bak = path.with_suffix(f".html.bak_{hashlib.sha256(raw).hexdigest()[:8]}")
    bak.write_bytes(raw)
    path.write_bytes(text.encode('utf-8'))
    print(f"[FIXED] {changed}")
    print(f"[BACKUP] {bak.name}")

dry = "--dry-run" in sys.argv
fix(TARGET, dry)
