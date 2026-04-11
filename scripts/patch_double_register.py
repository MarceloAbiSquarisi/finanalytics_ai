"""
patch_double_register.py
-------------------------
Registra SetTradeCallbackV2 em dois momentos:
1. Imediatamente após routing (cobre caso market_connected veio durante init)
2. Em background task quando market_connected vira True (cobre caso tardio)
"""
from pathlib import Path
import sys

TARGET = Path("src/finanalytics_ai/workers/profit_market_worker.py")

if not TARGET.exists():
    print(f"ERRO: {TARGET} não encontrado.")
    sys.exit(1)

content = TARGET.read_text(encoding="utf-8")
original = content

OLD = """    # Aguarda market_connected ANTES de registrar SetTradeCallbackV2.
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

NEW = """    # Registra SetTradeCallbackV2 imediatamente (cobre market_connected durante init)
    def _register_trade_cb():
        if hasattr(profit_client, '_cb_trade') and profit_client._cb_trade is not None:
            if hasattr(profit_client, '_dll') and profit_client._dll is not None:
                profit_client._dll.SetTradeCallbackV2(profit_client._cb_trade)
                log.info(
                    "profit_market_worker.trade_callback_registered",
                    market_connected=profit_client.state.market_connected,
                )
    _register_trade_cb()

    # Background task: re-registra quando market_connected vira True (caso tardio)
    async def _reregister_on_market_connected():
        for _ in range(120):  # max 60s
            await asyncio.sleep(0.5)
            if profit_client.state.market_connected:
                _register_trade_cb()
                log.info("profit_market_worker.trade_callback_reregistered_market_connected")
                return
        log.warning("profit_market_worker.market_connected_never_fired")
    asyncio.create_task(_reregister_on_market_connected())"""

if OLD not in content:
    print("ERRO: padrão não encontrado. Procurando alternativa...")
    for i, line in enumerate(content.splitlines(), 1):
        if "SetTradeCallbackV2" in line or "_mci in range" in line:
            print(f"  L{i}: {line}")
    sys.exit(1)

content = content.replace(OLD, NEW, 1)
TARGET.write_text(content, encoding="utf-8")
print("[ok] Double-register aplicado")

final = TARGET.read_text(encoding="utf-8")
checks = [
    ("_register_trade_cb definido",          "def _register_trade_cb()" in final),
    ("background task criada",               "_reregister_on_market_connected" in final),
    ("registro imediato presente",           "_register_trade_cb()\n\n    # Background" in final),
    ("SetTradeCallbackV2 ainda presente",    "SetTradeCallbackV2" in final),
]
for label, ok in checks:
    print(f"  {'[ok]' if ok else '[!!]'} {label}")
