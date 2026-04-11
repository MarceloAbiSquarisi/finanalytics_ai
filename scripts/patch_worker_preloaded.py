import pathlib, sys

f = pathlib.Path("src/finanalytics_ai/workers/profit_market_worker.py")
t = f.read_text(encoding="utf-8")

changes = 0

# 1: _pre_dll -> _PRELOADED_DLL
if "    _pre_dll = WinDLL(_dll_path)" in t:
    t = t.replace("    _pre_dll = WinDLL(_dll_path)", "    _PRELOADED_DLL = WinDLL(_dll_path)")
    changes += 1

if "    _pre_dll.SetTradeCallback(_pre_state_cb)" in t:
    t = t.replace("    _pre_dll.SetTradeCallback(_pre_state_cb)", "    _PRELOADED_DLL.SetTradeCallback(_pre_state_cb)")
    changes += 1

if "    _pre_dll.SetChangeCotationCallback(_pre_state_cb)" in t:
    t = t.replace("    _pre_dll.SetChangeCotationCallback(_pre_state_cb)", "    _PRELOADED_DLL.SetChangeCotationCallback(_pre_state_cb)")
    changes += 1

if "    _pre_dll.DLLInitializeLogin(" in t:
    t = t.replace("    _pre_dll.DLLInitializeLogin(", "    _PRELOADED_DLL.DLLInitializeLogin(")
    changes += 1

# 2: remove DLLFinalize e muda mensagem
old_fin = "        print(\"DLL pre-init: market connected! Subindo asyncio...\", flush=True)\n    else:\n        print(\"DLL pre-init: timeout \u2014 subindo asyncio sem market connected\", flush=True)\n    _pre_dll.DLLFinalize()"
new_fin = "        print(\"DLL pre-init: market connected! Passando para worker...\", flush=True)\n    else:\n        print(\"DLL pre-init: timeout - subindo sem market connected\", flush=True)\n        _PRELOADED_DLL = None"
if old_fin in t:
    t = t.replace(old_fin, new_fin)
    changes += 1
else:
    # variante sem em dash
    old_fin2 = "        print(\"DLL pre-init: market connected! Subindo asyncio...\", flush=True)\n    else:\n        print(\"DLL pre-init: timeout - subindo asyncio sem market connected\", flush=True)\n    _pre_dll.DLLFinalize()"
    if old_fin2 in t:
        t = t.replace(old_fin2, new_fin)
        changes += 1

# 3: injeta _GLOBAL_PRELOADED_DLL antes do asyncio.run
old_run = "    if sys.platform == \"win32\":\n        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())\n    asyncio.run(run_profit_worker())"
new_run = "    import finanalytics_ai.workers.profit_market_worker as _wmod\n    _wmod._GLOBAL_PRELOADED_DLL = _PRELOADED_DLL\n\n    if sys.platform == \"win32\":\n        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())\n    asyncio.run(run_profit_worker())"
if old_run in t:
    t = t.replace(old_run, new_run)
    changes += 1

# 4: variavel global no modulo
if "_GLOBAL_PRELOADED_DLL" not in t:
    t = t.replace(
        "# Publisher Redis para TapeService",
        "_GLOBAL_PRELOADED_DLL = None  # DLL pre-conectada antes do asyncio\n\n# Publisher Redis para TapeService"
    )
    changes += 1

# 5: _get_profit_client passa preloaded_dll
old_client = (
    "    return ProfitDLLClient(\n"
    "        dll_path=dll_path,\n"
    "        activation_key=os.getenv(\"PROFIT_ACTIVATION_KEY\", \"\"),\n"
    "        username=os.getenv(\"PROFIT_USERNAME\", \"\"),\n"
    "        password=os.getenv(\"PROFIT_PASSWORD\", \"\"),\n"
    "    )"
)
new_client = (
    "    import finanalytics_ai.workers.profit_market_worker as _wmod\n"
    "    _preloaded = getattr(_wmod, '_GLOBAL_PRELOADED_DLL', None)\n"
    "    return ProfitDLLClient(\n"
    "        dll_path=dll_path,\n"
    "        activation_key=os.getenv(\"PROFIT_ACTIVATION_KEY\", \"\"),\n"
    "        username=os.getenv(\"PROFIT_USERNAME\", \"\"),\n"
    "        password=os.getenv(\"PROFIT_PASSWORD\", \"\"),\n"
    "        preloaded_dll=_preloaded,\n"
    "    )"
)
if old_client in t:
    t = t.replace(old_client, new_client)
    changes += 1

f.write_text(t, encoding="utf-8")
print(f"CONCLUIDO — {changes} mudancas aplicadas")
