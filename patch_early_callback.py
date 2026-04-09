"""
Patch: registra SetTradeCallbackV2 imediatamente após DLLInitializeLogin.
O r=4 (MARKET_CONNECTED) dispara em ~1s após init — não pode aguardar o worker.
"""
import sys

TARGET = "src/finanalytics_ai/infrastructure/market_data/profit_dll/client.py"

OLD = 'log.info("profit_dll.initialized", mode="full_login")'

NEW = (
    'log.info("profit_dll.initialized", mode="full_login")\n'
    '\n'
    '        # Registra SetTradeCallbackV2 IMEDIATAMENTE apos DLLInitializeLogin\n'
    '        # r=4 (MARKET_CONNECTED) dispara em ~1s — nao pode aguardar o worker\n'
    '        self._dll.SetTradeCallbackV2(_trade_cb_v2)\n'
    '        log.info("profit_dll.trade_callback_v2_registered_early")'
)

content = open(TARGET, encoding="utf-8").read()

if OLD not in content:
    print("ERRO: pattern nao encontrado")
    sys.exit(1)

if "trade_callback_v2_registered_early" in content:
    print("JA APLICADO — nenhuma alteracao necessaria")
    sys.exit(0)

patched = content.replace(OLD, NEW, 1)
open(TARGET, "w", encoding="utf-8").write(patched)
print("PATCH OK")
