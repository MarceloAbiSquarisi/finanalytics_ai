#!/usr/bin/env python3
"""
Aplica o patch em engine.py: adiciona a função _returns que estava faltando.
Uso: python fix_engine.py <caminho_para_engine.py>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else r"src\finanalytics_ai\domain\portfolio_optimizer\engine.py"

with open(path, "r", encoding="utf-8") as f:
    src = f.read()

MARKER = "def _portfolio_stats("
INSERT = '''\
def _returns(prices: list[float]) -> list[float]:
    """Retornos simples diários: (p[i] - p[i-1]) / p[i-1]."""
    if len(prices) < 2:
        return []
    return [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]


'''

if "_returns" not in src:
    src = src.replace(MARKER, INSERT + MARKER)
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"[OK] _returns adicionado em {path}")
else:
    print(f"[SKIP] _returns já existe em {path}")
