"""
patch_set_trade_timing.py
--------------------------
Fix: SetTradeCallbackV2 deve ser chamado APOS [STATE] 2 4 (market_connected).
O diag confirma que registrar antes faz a DLL ignorar o callback silenciosamente.
"""
from pathlib import Path
import sys

TARGET = Path("src/finanalytics_ai/workers/profit_market_worker.py")

if not TARGET.exists():
    print(f"ERRO: {TARGET} não encontrado.")
    sys.exit(1)

content = TARGET.read_text(encoding="utf-8")
original = content

OLD = """    # Registra SetTradeCallbackV2 imediatamente apos init (nao espera routing).
    if hasattr(profit_client, '_cb_trade') and profit_client._cb_trade is not None:
        if hasattr(profit_client, '_dll') and profit_client._dll is not None:
            profit_client._dll.SetTradeCallbackV2(profit_client._cb_trade)
            log.info("profit_market_worker.trade_callback_registered_post_routing")"""

NEW = """    # Aguarda market_connected ANTES de registrar SetTradeCallbackV2.
    # O diag confirma: registrar antes de [STATE] 2 4 faz DLL ignorar o callback.
    for _mci in range(20):  # max 10s
        if profit_client.state.market_connected:
            break
        await asyncio.sleep(0.5)
    if hasattr(profit_client, '_cb_trade') and profit_client._cb_trade is not None:
        if hasattr(profit_client, '_dll') and profit_client._dll is not None:
            profit_client._dll.SetTradeCallbackV2(profit_client._cb_trade)
            log.info(
                "profit_market_worker.trade_callback_registered_post_routing",
                market_connected=profit_client.state.market_connected,
            )"""

if OLD not in content:
    print("ERRO: padrão não encontrado. Linhas relevantes:")
    for i, line in enumerate(content.splitlines(), 1):
        if "SetTradeCallbackV2" in line or "trade_callback_registered" in line:
            print(f"  L{i}: {line}")
    sys.exit(1)

content = content.replace(OLD, NEW, 1)
TARGET.write_text(content, encoding="utf-8")
print("[ok] Timing do SetTradeCallbackV2 corrigido")

# Verificação
final = TARGET.read_text(encoding="utf-8")
checks = [
    ("loop de espera market_connected",   "for _mci in range(20)" in final),
    ("log com market_connected",          "market_connected=profit_client.state.market_connected" in final),
    ("SetTradeCallbackV2 ainda presente", "SetTradeCallbackV2" in final),
]
for label, ok in checks:
    print(f"  {'[ok]' if ok else '[!!]'} {label}")
