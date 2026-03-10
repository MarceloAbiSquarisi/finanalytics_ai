#!/usr/bin/env python3
import sys

path = sys.argv[1] if len(sys.argv) > 1 else r"src\finanalytics_ai\domain\patrimony\consolidated.py"

with open(path, "r", encoding="utf-8") as f:
    src = f.read()

# Bug: cash incluído em total_invested torna pl=8000 em vez de 13000
# Fix: excluir cash_value de total_invested
# Fix: total_pl_pct usa total_invested + cash como denominador (total assets)
OLD = """\
    total_invested = equities_invested + etfs_invested + rf_invested + cash_value
    total_pl       = total - total_invested
    total_pl_pct   = (total_pl / total_invested * 100) if total_invested else 0.0"""

NEW = """\
    # Cash é parte do patrimônio mas não é "capital investido" — tratamos separado.
    # total_pl_pct usa total_invested + cash como base (retorno sobre total de ativos).
    total_invested = equities_invested + etfs_invested + rf_invested
    total_pl       = total - total_invested
    _pl_base       = total_invested + cash_value
    total_pl_pct   = (total_pl / _pl_base * 100) if _pl_base else 0.0"""

if OLD in src:
    src = src.replace(OLD, NEW)
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"[OK] consolidated.py corrigido: cash excluído de total_invested")
else:
    print(f"[FAIL] Padrão não encontrado em {path}")
    print("Fragmento esperado:")
    print(repr(OLD))
