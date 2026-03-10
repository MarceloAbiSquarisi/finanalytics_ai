#!/usr/bin/env python3
import sys

path = sys.argv[1] if len(sys.argv) > 1 else r"src\finanalytics_ai\domain\fixed_income\ir_calculator.py"

with open(path, "r", encoding="utf-8") as f:
    src = f.read()

# Bug: iof_v não arredondado antes de calcular ir_base
# Isso causa ir_base = 18.05 enquanto gross_yield - iof_amount = 18.04 (diff = 0.01)
# Fix: arredondar iof_v (= iof_amount) para 2 casas antes de calcular ir_base
OLD = "        iof_r = 0.0 if exempt else iof_rate_for_days(days)\n        iof_v = gross_yield * iof_r\n\n        ir_base = max(0.0, gross_yield - iof_v)"
NEW = "        iof_r = 0.0 if exempt else iof_rate_for_days(days)\n        iof_v = round(gross_yield * iof_r, 2)  # arredondado = consistente com iof_amount\n\n        ir_base = max(0.0, gross_yield - iof_v)"

if OLD in src:
    src = src.replace(OLD, NEW)
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"[OK] ir_calculator.py corrigido: iof_v arredondado antes de ir_base")
else:
    print(f"[FAIL] Padrão não encontrado — verificar indentação do arquivo original")
    # Tenta variação com diferentes espaçamentos
    alt_old = "        iof_v = gross_yield * iof_r\n\n        ir_base"
    if alt_old in src:
        src = src.replace(
            "        iof_v = gross_yield * iof_r",
            "        iof_v = round(gross_yield * iof_r, 2)  # consistente com iof_amount"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)
        print(f"[OK] ir_calculator.py corrigido (variação alternativa)")
    else:
        print(f"[FAIL] Nenhuma variação encontrada")
