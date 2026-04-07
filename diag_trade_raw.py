"""diag_trade_raw.py
Diagnostico minimo: conta callbacks brutos sem TranslateTrade.
Se count > 0: callback funciona, problema e no TranslateTrade.
Se count == 0: callback nao esta sendo chamado pela DLL.
"""
import ctypes, os, sys, time
from ctypes import WINFUNCTYPE, c_int, c_size_t, c_uint, c_wchar_p
from dotenv import load_dotenv
load_dotenv(override=False)

dll_path = os.getenv("PROFIT_DLL_PATH", r"C:\Nelogica\profitdll.dll")
dll = ctypes.WinDLL(dll_path)
print(f"[OK] DLL: {dll_path}", flush=True)

raw_calls = []
translate_ok = []
translate_fail = []
routing_done = False

@WINFUNCTYPE(None, c_int, c_int)
def state_cb(t, r):
    global routing_done
    print(f"[STATE] conn_type={t} result={r}", flush=True)
    if t == 1 and r >= 4:
        routing_done = True

@WINFUNCTYPE(None, c_size_t, c_size_t, c_uint)
def trade_cb_raw(asset_id_raw, trade_ptr, flags):
    raw_calls.append(1)
    sys.stdout.write(f"[RAW_TRADE] #{len(raw_calls)} asset={asset_id_raw:#x} ptr={trade_ptr:#x}\n")
    sys.stdout.flush()
    # Tenta TranslateTrade
    try:
        from profitTypes import TConnectorTrade
        trade = TConnectorTrade()
        ret = dll.TranslateTrade(c_size_t(trade_ptr), ctypes.byref(trade))
        if ret != 0:
            translate_fail.append(ret)
            sys.stdout.write(f"[TRANSLATE_FAIL] ret={ret}\n")
        else:
            translate_ok.append(trade.Price)
            sys.stdout.write(f"[TICK] price={trade.Price} qty={trade.Quantity} type={trade.TradeType}\n")
        sys.stdout.flush()
    except Exception as e:
        sys.stdout.write(f"[TRANSLATE_ERR] {e}\n")
        sys.stdout.flush()

dll.DLLInitializeLogin(
    c_wchar_p(os.getenv("PROFIT_ACTIVATION_KEY", "")),
    c_wchar_p(os.getenv("PROFIT_USERNAME", "")),
    c_wchar_p(os.getenv("PROFIT_PASSWORD", "")),
    state_cb,
    None, None, None, None, None, None, None, None, None, None,
)

for i in range(60):
    if routing_done:
        print(f"[OK] Routing em {i*0.5:.1f}s", flush=True)
        break
    time.sleep(0.5)

dll.SetTradeCallbackV2(trade_cb_raw)
print("[OK] SetTradeCallbackV2 registrado", flush=True)

for _ in range(20): time.sleep(0.5)

for ticker in ["WINFUT", "WDOFUT", "PETR4"]:
    ret = dll.SubscribeTicker(c_wchar_p(ticker), c_wchar_p("B"))
    print(f"[SUB] {ticker} ret={ret}", flush=True)

print("[...] Aguardando 60s...", flush=True)
for i in range(60):
    time.sleep(1)
    if (i+1) % 10 == 0:
        print(f"  {i+1}s — raw={len(raw_calls)} ok={len(translate_ok)} fail={len(translate_fail)}", flush=True)

print(f"\n[RESULTADO] raw_calls={len(raw_calls)} translate_ok={len(translate_ok)} translate_fail={len(translate_fail)}", flush=True)
dll.DLLFinalize()
