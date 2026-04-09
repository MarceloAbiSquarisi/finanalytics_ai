import pathlib, sys
f = pathlib.Path("src/finanalytics_ai/infrastructure/market_data/profit_dll/client.py")
t = f.read_text(encoding="utf-8")
old = "        self._dll.SetChangeCotationCallback(_trade_cb_v2)\n        self._dll.SetTradeCallback(_trade_cb_v2)\n        log.info(\"profit_dll.callbacks_registered_after_init\")"
new = "        self._dll.SetTradeCallback(_trade_cb_v2)\n        log.info(\"profit_dll.callbacks_registered_after_init\")"
if old in t:
    f.write_text(t.replace(old, new, 1), encoding="utf-8")
    print("OK")
else:
    print("NAO ENCONTRADO")
