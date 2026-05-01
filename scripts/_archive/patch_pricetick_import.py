"""
patch_pricetick_import.py
-------------------------
Fix: _trade_cb_v2 importa PriceTick de types mas ela está definida em client.py.
O import falha silenciosamente e o tick é descartado.
Fix: usa a referência local `PriceTick` que já está no escopo do módulo.
"""
from pathlib import Path
import sys

TARGET = Path("src/finanalytics_ai/infrastructure/market_data/profit_dll/client.py")

if not TARGET.exists():
    print(f"ERRO: {TARGET} não encontrado.")
    sys.exit(1)

content = TARGET.read_text(encoding="utf-8")
original = content

# Fix: remove import de PriceTick de dentro do callback e usa referência direta
OLD = "                from finanalytics_ai.infrastructure.market_data.profit_dll.types import PriceTick as _PT\n                tick = _PT("
NEW = "                tick = PriceTick("

if OLD not in content:
    # tenta variante sem newline Windows
    OLD2 = "                from finanalytics_ai.infrastructure.market_data.profit_dll.types import PriceTick as _PT\r\n                tick = _PT("
    if OLD2 in content:
        content = content.replace(OLD2, NEW, 1)
    else:
        # busca só o import e renomeia
        OLD3 = "from finanalytics_ai.infrastructure.market_data.profit_dll.types import PriceTick as _PT"
        if OLD3 in content:
            content = content.replace(OLD3 + "\n                tick = _PT(", NEW, 1)
            content = content.replace(OLD3 + "\r\n                tick = _PT(", NEW, 1)
        else:
            print("ERRO: padrão não encontrado — busca manual:")
            for i, line in enumerate(content.splitlines(), 1):
                if "PriceTick" in line:
                    print(f"  L{i}: {line.strip()}")
            sys.exit(1)
else:
    content = content.replace(OLD, NEW, 1)

if content == original:
    print("ERRO: nenhuma alteração feita")
    sys.exit(1)

TARGET.write_text(content, encoding="utf-8")
print("[ok] PriceTick import corrigido")

# Verificação
final = TARGET.read_text(encoding="utf-8")
checks = [
    ("import PriceTick de types removido", "import PriceTick as _PT" not in final),
    ("tick = PriceTick( presente",         "tick = PriceTick(" in final),
    ("_trade_cb_v2 ainda existe",          "_trade_cb_v2" in final),
]
for label, ok in checks:
    print(f"  {'[ok]' if ok else '[!!]'} {label}")
