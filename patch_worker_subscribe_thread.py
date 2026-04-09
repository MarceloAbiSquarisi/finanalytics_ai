"""
Patch profit_market_worker.py:
- Remove _register_trade_cb (callbacks ja registrados no client.py)
- Remove _reregister_on_market_connected (nao necessario)
- Substitui subscribe_tickers por start_subscribe_thread
"""
import sys, pathlib

TARGET = pathlib.Path("src/finanalytics_ai/workers/profit_market_worker.py")
text = TARGET.read_text(encoding="utf-8")

if "patch_worker_subscribe_thread_applied" in text:
    print("JA APLICADO"); sys.exit(0)

# Patch 1: remove bloco _register_trade_cb + _reregister_on_market_connected
OLD1 = (
    "    # Registra SetTradeCallbackV2 imediatamente (cobre market_connected durante init)\n"
    "    def _register_trade_cb():\n"
    "        if hasattr(profit_client, '_cb_trade') and profit_client._cb_trade is not None:\n"
    "            if hasattr(profit_client, '_dll') and profit_client._dll is not None:\n"
    "                profit_client._dll.SetTradeCallbackV2(profit_client._cb_trade)\n"
    "                log.info(\n"
    "                    \"profit_market_worker.trade_callback_registered\",\n"
    "                    market_connected=profit_client.state.market_connected,\n"
    "                )\n"
    "    _register_trade_cb()\n"
    "\n"
    "    # Background task: re-registra quando market_connected vira True (caso tardio)\n"
    "    async def _reregister_on_market_connected():\n"
    "        for _ in range(120):  # max 60s\n"
    "            await asyncio.sleep(0.5)\n"
    "            if profit_client.state.market_connected:\n"
    "                _register_trade_cb()\n"
    "                log.info(\"profit_market_worker.trade_callback_reregistered_market_connected\")\n"
    "                return\n"
    "        log.warning(\"profit_market_worker.market_connected_never_fired\")\n"
    "    asyncio.create_task(_reregister_on_market_connected())\n"
    "    # market_connected nao e gate para ticks — routing_connected e suficiente.\n"
    "    # conn_type=2 result=4 dispara durante init antes deste ponto; nao bloquear.\n"
    "    await asyncio.sleep(1.0)  # yield para callbacks pendentes\n"
)

NEW1 = (
    "    # patch_worker_subscribe_thread_applied\n"
    "    # callbacks ja registrados no client.py apos DLLInitializeLogin\n"
    "    await asyncio.sleep(1.0)  # yield para callbacks pendentes\n"
)

if OLD1 in text:
    text = text.replace(OLD1, NEW1, 1)
    print("Patch 1 (remove register_trade_cb): OK")
else:
    print("AVISO: Patch 1 nao encontrado - verificando linhas...")
    for i, l in enumerate(text.splitlines()):
        if "_register_trade_cb" in l or "_reregister_on_market_connected" in l:
            print(f"  {i+1}: {l[:80]}")

# Patch 2: substitui subscribe_tickers por start_subscribe_thread
OLD2 = (
    "    if hasattr(profit_client, \"subscribe_tickers\"):\n"
    "        await profit_client.subscribe_tickers(tickers)\n"
    "        log.info(\"profit_market_worker.subscribed\", tickers=tickers)\n"
)
NEW2 = (
    "    # Inicia thread de subscribe — chama SubscribeTicker apos t=2 r=4\n"
    "    # (nunca chamar DLL dentro de callback — manual secao 3.2)\n"
    "    if hasattr(profit_client, \"start_subscribe_thread\"):\n"
    "        profit_client.start_subscribe_thread(tickers)\n"
    "        log.info(\"profit_market_worker.subscribe_thread_started\", tickers=tickers)\n"
    "    elif hasattr(profit_client, \"subscribe_tickers\"):\n"
    "        await profit_client.subscribe_tickers(tickers)\n"
    "        log.info(\"profit_market_worker.subscribed\", tickers=tickers)\n"
)

if OLD2 in text:
    text = text.replace(OLD2, NEW2, 1)
    print("Patch 2 (start_subscribe_thread): OK")
else:
    print("AVISO: Patch 2 nao encontrado")
    for i, l in enumerate(text.splitlines()):
        if "subscribe_tickers" in l:
            print(f"  {i+1}: {l[:80]}")

TARGET.write_text(text, encoding="utf-8")
print("PATCH COMPLETO")
