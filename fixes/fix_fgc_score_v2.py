#!/usr/bin/env python3
"""
Fix v2 — usa regex para localizar o padrão, imune a encoding BOM/CRLF.
"""
import sys, re, pathlib

path = sys.argv[1] if len(sys.argv) > 1 else r"src\finanalytics_ai\domain\fixed_income\entities.py"

raw = pathlib.Path(path).read_bytes()

# Detecta encoding: tenta UTF-8-BOM primeiro, depois UTF-8, depois latin-1
for enc in ("utf-8-sig", "utf-8", "latin-1"):
    try:
        src = raw.decode(enc)
        break
    except UnicodeDecodeError:
        continue

OLD = r"score\s*=\s*max\(0,\s*100\s*-\s*n_critical\s*\*\s*25\s*-\s*n_warning\s*\*\s*10\)"
NEW = "score = max(0, 100 - n_critical * 50 - n_warning * 10)"

if re.search(OLD, src):
    src_new = re.sub(OLD, NEW, src)
    pathlib.Path(path).write_text(src_new, encoding="utf-8")
    print(f"[OK] FGC score corrigido: n_critical * 25 → * 50")
else:
    # Tenta busca literal para diagnóstico
    for line in src.splitlines():
        if "n_critical" in line and "score" in line:
            print(f"[DEBUG] Linha encontrada: {repr(line)}")
    print(f"[FAIL] Padrão regex não encontrado — verifique o arquivo")
