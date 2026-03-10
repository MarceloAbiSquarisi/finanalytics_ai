#!/usr/bin/env python3
import sys, re

path = sys.argv[1] if len(sys.argv) > 1 else r"src\finanalytics_ai\domain\fixed_income\entities.py"

with open(path, "r", encoding="utf-8") as f:
    src = f.read()

OLD = "score = max(0, 100 - n_critical * 25 - n_warning * 10)"
NEW = "score = max(0, 100 - n_critical * 50 - n_warning * 10)"

if OLD in src:
    src = src.replace(OLD, NEW)
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"[OK] FGC score corrigido em {path}: *25 → *50")
else:
    print(f"[SKIP] Padrão não encontrado em {path} — verifique o arquivo correto")
