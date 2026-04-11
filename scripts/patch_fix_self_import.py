import pathlib

f = pathlib.Path("src/finanalytics_ai/workers/profit_market_worker.py")
t = f.read_text(encoding="utf-8")

old = (
    "    import finanalytics_ai.workers.profit_market_worker as _wmod\n"
    "    _wmod._GLOBAL_PRELOADED_DLL = _PRELOADED_DLL\n"
    "\n"
    "    if sys.platform"
)
new = (
    "    # Injeta no modulo atual sem duplo import\n"
    "    globals()['_GLOBAL_PRELOADED_DLL'] = _PRELOADED_DLL\n"
    "\n"
    "    if sys.platform"
)

if old in t:
    f.write_text(t.replace(old, new, 1), encoding="utf-8")
    print("OK")
else:
    print("NAO ENCONTRADO")
