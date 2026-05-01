"""
Refatoração: ProfitDLLClient aceita DLL já conectada via preloaded_dll.
Se preloaded_dll for passado, pula DLLInitializeLogin e usa a instância existente.
Isso resolve o conflito ProactorEventLoop vs ConnectorThread da Nelogica.
"""
import pathlib, sys

CLIENT = pathlib.Path("src/finanalytics_ai/infrastructure/market_data/profit_dll/client.py")
WORKER = pathlib.Path("src/finanalytics_ai/workers/profit_market_worker.py")

# ── Patch 1: __init__ aceita preloaded_dll ────────────────────────────────────
ct = CLIENT.read_text(encoding="utf-8")

OLD1 = (
    "    def __init__(\n"
    "        self,\n"
    "        dll_path: str,\n"
    "        activation_key: str,\n"
    "        username: str,\n"
    "        password: str,\n"
    "        tick_queue_size: int = 10_000,\n"
    "    ) -> None:\n"
    "        self._dll_path = dll_path\n"
    "        self._activation_key = activation_key\n"
    "        self._username = username\n"
    "        self._password = password\n"
    "\n"
    "        self._dll: WinDLL | None = None\n"
)

NEW1 = (
    "    def __init__(\n"
    "        self,\n"
    "        dll_path: str,\n"
    "        activation_key: str,\n"
    "        username: str,\n"
    "        password: str,\n"
    "        tick_queue_size: int = 10_000,\n"
    "        preloaded_dll: Any = None,\n"
    "    ) -> None:\n"
    "        self._dll_path = dll_path\n"
    "        self._activation_key = activation_key\n"
    "        self._username = username\n"
    "        self._password = password\n"
    "\n"
    "        self._dll: WinDLL | None = preloaded_dll  # DLL ja conectada ou None\n"
)

if OLD1 in ct:
    ct = ct.replace(OLD1, NEW1, 1)
    print("Patch 1 (__init__ preloaded_dll): OK")
else:
    print("ERRO: Patch 1 nao encontrado")
    sys.exit(1)

# ── Patch 2: start() pula init se _dll ja existe ─────────────────────────────
OLD2 = (
    "    async def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:\n"
    "        \"\"\"Inicializa a DLL — padrao identico ao script de diagnostico que funciona.\"\"\"\n"
    "        self._loop = loop or asyncio.get_running_loop()\n"
    "        # Recria o Event dentro do loop ativo (Python 3.12 requer isso)\n"
    "        self._connected_event = asyncio.Event()\n"
    "\n"
    "        # Carrega DLL sem configurar restype (igual ao diagnostico)\n"
    "        from ctypes import WinDLL as _WinDLL, WINFUNCTYPE as _WFTYPE, c_int as _cint, c_wchar_p as _wstr\n"
    "        self._dll = _WinDLL(self._dll_path)\n"
)

NEW2 = (
    "    async def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:\n"
    "        \"\"\"Inicializa a DLL — usa preloaded_dll se disponivel, senao conecta do zero.\"\"\"\n"
    "        self._loop = loop or asyncio.get_running_loop()\n"
    "        # Recria o Event dentro do loop ativo (Python 3.12 requer isso)\n"
    "        self._connected_event = asyncio.Event()\n"
    "\n"
    "        from ctypes import WinDLL as _WinDLL, WINFUNCTYPE as _WFTYPE, c_int as _cint, c_wchar_p as _wstr\n"
    "\n"
    "        # Se DLL ja foi pre-conectada (evita conflito ProactorEventLoop vs ConnectorThread)\n"
    "        if self._dll is not None:\n"
    "            log.info(\"profit_dll.using_preloaded_dll\")\n"
    "            self._state.market_connected = True\n"
    "            self._subscribe_event.set()\n"
    "            self._consumer_task = asyncio.create_task(self._consume_loop())\n"
    "            return\n"
    "\n"
    "        # Carrega DLL sem configurar restype (igual ao diagnostico)\n"
    "        self._dll = _WinDLL(self._dll_path)\n"
)

if OLD2 in ct:
    ct = ct.replace(OLD2, NEW2, 1)
    print("Patch 2 (start preloaded): OK")
else:
    print("ERRO: Patch 2 nao encontrado")
    sys.exit(1)

CLIENT.write_text(ct, encoding="utf-8")

# ── Patch 3: worker passa preloaded_dll ──────────────────────────────────────
wt = WORKER.read_text(encoding="utf-8")

OLD3 = (
    "if __name__ == \"__main__\":\n"
    "    # SOLUCAO DEFINITIVA: inicializa DLL antes do asyncio.run()\n"
    "    # ProactorEventLoop (IOCP) interfere com ConnectorThread da DLL Nelogica.\n"
    "    # SelectorEventLoop dentro do asyncio tambem interfere.\n"
    "    # Unica solucao: DLL conectada (t=2 r=4) antes de qualquer event loop.\n"
    "    import sys, threading, os\n"
    "    from ctypes import WinDLL, WINFUNCTYPE, c_int, c_wchar_p\n"
    "    from pathlib import Path\n"
    "    from dotenv import load_dotenv\n"
    "\n"
    "    load_dotenv(Path(\".env.local\"), override=True)\n"
    "    load_dotenv(Path(\".env\"), override=False)\n"
    "\n"
    "    _dll_path = os.getenv(\"PROFIT_DLL_PATH\", r\"C:\\\\Nelogica\\\\profitdll.dll\")\n"
    "    _key      = os.getenv(\"PROFIT_ACTIVATION_KEY\", \"\")\n"
    "    _usr      = os.getenv(\"PROFIT_USERNAME\", \"\")\n"
    "    _pwd      = os.getenv(\"PROFIT_PASSWORD\", \"\")\n"
    "\n"
    "    _market_ready = threading.Event()\n"
    "    _pre_dll = WinDLL(_dll_path)\n"
    "\n"
    "    @WINFUNCTYPE(None, c_int, c_int)\n"
    "    def _pre_state_cb(t, r):\n"
    "        if t == 2 and r == 4:\n"
    "            _market_ready.set()\n"
    "\n"
    "    _pre_dll.SetTradeCallback(_pre_state_cb)\n"
    "    _pre_dll.SetChangeCotationCallback(_pre_state_cb)\n"
    "    _pre_dll.DLLInitializeLogin(\n"
    "        c_wchar_p(_key), c_wchar_p(_usr), c_wchar_p(_pwd),\n"
    "        _pre_state_cb, None, None, None, None, None, None, None, None, None, None,\n"
    "    )\n"
    "    print(\"DLL pre-init: aguardando market connected (t=2 r=4)...\", flush=True)\n"
    "    connected = _market_ready.wait(timeout=90)\n"
    "    if connected:\n"
    "        print(\"DLL pre-init: market connected! Subindo asyncio...\", flush=True)\n"
    "    else:\n"
    "        print(\"DLL pre-init: timeout — subindo asyncio sem market connected\", flush=True)\n"
    "    _pre_dll.DLLFinalize()\n"
    "\n"
    "    if sys.platform == \"win32\":\n"
    "        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())\n"
    "    asyncio.run(run_profit_worker())"
)

NEW3 = (
    "if __name__ == \"__main__\":\n"
    "    # SOLUCAO: conecta DLL antes do asyncio.run() e reutiliza a instancia.\n"
    "    # ProactorEventLoop (IOCP) e SelectorEventLoop interferem com ConnectorThread.\n"
    "    # Passamos a DLL ja conectada para ProfitDLLClient via preloaded_dll.\n"
    "    import sys, threading, os\n"
    "    from ctypes import WinDLL, WINFUNCTYPE, c_int, c_wchar_p\n"
    "    from pathlib import Path\n"
    "    from dotenv import load_dotenv\n"
    "\n"
    "    load_dotenv(Path(\".env.local\"), override=True)\n"
    "    load_dotenv(Path(\".env\"), override=False)\n"
    "\n"
    "    _dll_path = os.getenv(\"PROFIT_DLL_PATH\", r\"C:\\\\Nelogica\\\\profitdll.dll\")\n"
    "    _key      = os.getenv(\"PROFIT_ACTIVATION_KEY\", \"\")\n"
    "    _usr      = os.getenv(\"PROFIT_USERNAME\", \"\")\n"
    "    _pwd      = os.getenv(\"PROFIT_PASSWORD\", \"\")\n"
    "\n"
    "    _market_ready = threading.Event()\n"
    "    _PRELOADED_DLL = WinDLL(_dll_path)\n"
    "\n"
    "    @WINFUNCTYPE(None, c_int, c_int)\n"
    "    def _pre_state_cb(t, r):\n"
    "        if t == 2 and r == 4:\n"
    "            _market_ready.set()\n"
    "\n"
    "    _PRELOADED_DLL.SetTradeCallback(_pre_state_cb)\n"
    "    _PRELOADED_DLL.SetChangeCotationCallback(_pre_state_cb)\n"
    "    _PRELOADED_DLL.DLLInitializeLogin(\n"
    "        c_wchar_p(_key), c_wchar_p(_usr), c_wchar_p(_pwd),\n"
    "        _pre_state_cb, None, None, None, None, None, None, None, None, None, None,\n"
    "    )\n"
    "    print(\"DLL pre-init: aguardando market connected (t=2 r=4)...\", flush=True)\n"
    "    connected = _market_ready.wait(timeout=90)\n"
    "    if connected:\n"
    "        print(\"DLL pre-init: market connected! Passando para worker...\", flush=True)\n"
    "    else:\n"
    "        print(\"DLL pre-init: timeout — subindo asyncio sem market connected\", flush=True)\n"
    "        _PRELOADED_DLL = None\n"
    "\n"
    "    # Injeta a DLL pre-conectada no worker via variavel global\n"
    "    import finanalytics_ai.workers.profit_market_worker as _self_mod\n"
    "    _self_mod._GLOBAL_PRELOADED_DLL = _PRELOADED_DLL\n"
    "\n"
    "    if sys.platform == \"win32\":\n"
    "        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())\n"
    "    asyncio.run(run_profit_worker())"
)

if OLD3 in wt:
    wt = wt.replace(OLD3, NEW3, 1)
    # Adiciona variavel global no inicio do modulo
    if "_GLOBAL_PRELOADED_DLL" not in wt:
        wt = wt.replace(
            "# Publisher Redis para TapeService",
            "_GLOBAL_PRELOADED_DLL: Any = None  # DLL pre-conectada antes do asyncio\n\n# Publisher Redis para TapeService"
        )
    WORKER.write_text(wt, encoding="utf-8")
    print("Patch 3 (worker preloaded): OK")
else:
    print("ERRO: Patch 3 nao encontrado")
    sys.exit(1)

print("REFATORACAO COMPLETA")

# ── Patch 4: _get_profit_client passa preloaded_dll ──────────────────────────
wt2 = WORKER.read_text(encoding="utf-8")

OLD4 = (
    "    return ProfitDLLClient(\n"
    "        dll_path=dll_path,\n"
    "        activation_key=os.getenv(\"PROFIT_ACTIVATION_KEY\", \"\"),\n"
    "        username=os.getenv(\"PROFIT_USERNAME\", \"\"),\n"
    "        password=os.getenv(\"PROFIT_PASSWORD\", \"\"),\n"
    "    )"
)

NEW4 = (
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

if OLD4 in wt2:
    wt2 = wt2.replace(OLD4, NEW4, 1)
    WORKER.write_text(wt2, encoding="utf-8")
    print("Patch 4 (_get_profit_client preloaded_dll): OK")
else:
    print("ERRO: Patch 4 nao encontrado")
