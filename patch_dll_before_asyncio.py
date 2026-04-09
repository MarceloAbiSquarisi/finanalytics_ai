"""
Patch: inicializa a DLL em thread separada ANTES do asyncio.run()
Aguarda t=2 r=4 (market connected) antes de subir o event loop.
Isso evita o conflito entre ProactorEventLoop (IOCP) e a ConnectorThread da DLL.
"""
import pathlib, sys

TARGET = pathlib.Path("src/finanalytics_ai/workers/profit_market_worker.py")
text = TARGET.read_text(encoding="utf-8")

OLD = """if __name__ == "__main__":
    # ProactorEventLoop (padrao no Windows) interfere com Winsock da ProfitDLL.
    # SelectorEventLoop evita o conflito de IOCP.
    import sys
    if sys.platform == "win32":
        # ProactorEventLoop (default Windows) funciona com a DLL.
        # SelectorEventLoop impede routing (t=1 r=4 nunca chega) — NAO usar.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_profit_worker())"""

NEW = """if __name__ == "__main__":
    # SOLUCAO DEFINITIVA: inicializa DLL antes do asyncio.run()
    # ProactorEventLoop (IOCP) interfere com ConnectorThread da DLL Nelogica.
    # SelectorEventLoop dentro do asyncio tambem interfere.
    # Unica solucao: DLL conectada (t=2 r=4) antes de qualquer event loop.
    import sys, threading, os
    from ctypes import WinDLL, WINFUNCTYPE, c_int, c_wchar_p
    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv(Path(".env.local"), override=True)
    load_dotenv(Path(".env"), override=False)

    _dll_path = os.getenv("PROFIT_DLL_PATH", r"C:\\Nelogica\\profitdll.dll")
    _key      = os.getenv("PROFIT_ACTIVATION_KEY", "")
    _usr      = os.getenv("PROFIT_USERNAME", "")
    _pwd      = os.getenv("PROFIT_PASSWORD", "")

    _market_ready = threading.Event()
    _pre_dll = WinDLL(_dll_path)

    @WINFUNCTYPE(None, c_int, c_int)
    def _pre_state_cb(t, r):
        if t == 2 and r == 4:
            _market_ready.set()

    _pre_dll.SetTradeCallback(_pre_state_cb)
    _pre_dll.SetChangeCotationCallback(_pre_state_cb)
    _pre_dll.DLLInitializeLogin(
        c_wchar_p(_key), c_wchar_p(_usr), c_wchar_p(_pwd),
        _pre_state_cb, None, None, None, None, None, None, None, None, None, None,
    )
    print("DLL pre-init: aguardando market connected (t=2 r=4)...", flush=True)
    connected = _market_ready.wait(timeout=90)
    if connected:
        print("DLL pre-init: market connected! Subindo asyncio...", flush=True)
    else:
        print("DLL pre-init: timeout — subindo asyncio sem market connected", flush=True)
    _pre_dll.DLLFinalize()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(run_profit_worker())"""

if OLD in text:
    text = text.replace(OLD, NEW, 1)
    # Precisa importar WINFUNCTYPE no topo
    if "from ctypes import WINFUNCTYPE" not in text:
        text = text.replace(
            "import asyncio",
            "import asyncio\nfrom ctypes import WINFUNCTYPE",
            1
        )
    TARGET.write_text(text, encoding="utf-8")
    print("PATCH OK")
else:
    print("ERRO: pattern nao encontrado")
    sys.exit(1)
