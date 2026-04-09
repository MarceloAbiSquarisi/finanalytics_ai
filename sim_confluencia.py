"""
sim_confluencia.py
------------------
Simulador offline da engine de confluência.

Testa três cenários sem precisar de mercado aberto:
    1. Pressão compradora institucional  → espera LONG FORTE
    2. Pressão vendedora                 → espera SHORT FORTE
    3. Equilíbrio                        → espera NEUTRO

Uso:
    uv run python sim_confluencia.py

Depois, para testar com Redis ao vivo:
    docker exec finanalytics_redis redis-cli PUBLISH tape:ticks \
      '{"ticker":"WINFUT","price":127500,"volume":5,"quantity":5,"trade_type":1,"buy_agent":1,"sell_agent":2,"ts":"now","trade_number":0}'
"""
from __future__ import annotations

import sys
import os

# Garante que src/ está no path — não precisa instalar o pacote
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from finanalytics_ai.domain.tape.confluence import ConflunceEngine, ConflunceConfig

engine = ConflunceEngine()


def print_signal(label: str, **kwargs) -> None:
    sig = engine.evaluate(**kwargs)
    print(f"\n{'='*60}")
    print(f"CENÁRIO: {label}")
    print(f"{'='*60}")
    print(f"  Ticker:    {sig.ticker}")
    print(f"  Score:     {sig.score:.1f}/100")
    print(f"  Direção:   {sig.direction.value}")
    print(f"  Força:     {sig.strength.value}")
    print(f"  Acionável: {'SIM' if sig.is_actionable else 'NÃO'}")
    if not sig.is_valid:
        print(f"  Motivo:    {sig.reason}")
    else:
        print(f"\n  Fatores:")
        for f in sig.factors:
            bar = "█" * int(f.score / 5)
            print(f"    [{f.direction.value:6}] {f.name:12} {f.score:5.1f}  {bar}")
            print(f"             {f.reason}")


# ── Cenário 1: Pressão compradora institucional ───────────────────────────────
print_signal(
    "LONG FORTE — comprador institucional dominando",
    ticker="WINFUT",
    ratio_cv=2.1,
    saldo_fluxo=8_500.0,
    trades_por_min=75.0,
    total_trades=450,
    vol_compra=12_000.0,
    vol_venda=3_500.0,
)

# ── Cenário 2: Pressão vendedora forte ────────────────────────────────────────
print_signal(
    "SHORT FORTE — vendedores distribuindo",
    ticker="WDOFUT",
    ratio_cv=0.45,
    saldo_fluxo=-6_200.0,
    trades_por_min=55.0,
    total_trades=320,
    vol_compra=3_000.0,
    vol_venda=9_200.0,
)

# ── Cenário 3: Equilíbrio / lateralização ────────────────────────────────────
print_signal(
    "NEUTRO — mercado em equilíbrio",
    ticker="PETR4",
    ratio_cv=1.05,
    saldo_fluxo=200.0,
    trades_por_min=18.0,
    total_trades=90,
    vol_compra=5_100.0,
    vol_venda=4_900.0,
)

# ── Cenário 4: Dados insuficientes ───────────────────────────────────────────
print_signal(
    "INVÁLIDO — poucos trades",
    ticker="COGN3",
    ratio_cv=1.8,
    saldo_fluxo=500.0,
    trades_por_min=15.0,
    total_trades=3,
    vol_compra=600.0,
    vol_venda=100.0,
)

# ── Cenário 5: Conflito de fatores ────────────────────────────────────────────
print_signal(
    "CONFLITO — C/V comprador mas saldo vendedor",
    ticker="VALE3",
    ratio_cv=1.6,           # comprador no ratio
    saldo_fluxo=-3_000.0,   # mas vendedor no saldo
    trades_por_min=35.0,
    total_trades=180,
    vol_compra=4_000.0,
    vol_venda=7_000.0,
)

print(f"\n{'='*60}")
print("Simulação concluída. Para testar ao vivo com Redis:")
print("""
docker exec finanalytics_redis redis-cli PUBLISH tape:ticks \\
  '{"ticker":"WINFUT","price":127500,"volume":5,"quantity":5,\\
    "trade_type":1,"buy_agent":1,"sell_agent":2,\\
    "ts":"now","trade_number":0}'
""")
