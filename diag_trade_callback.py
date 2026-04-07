"""diag_trade_callback.py — testa se _trade_cb e chamado pela DLL."""
import ctypes, os, time
from ctypes import WINFUNCTYPE, c_int, c_size_t, c_uint, c_wchar_p
from dotenv import load_dotenv
load_dotenv(override=False)

dll_path = os.getenv("PROFIT_DLL_PATH", r"C:\Nelogica\ProfitDLL64.dll")
dll = ctypes.WinDLL(dll_path)
print(f"[OK] DLL carregada: {dll_path}", flush=True)

trades_received = []

@WINFUNCTYPE(None, c_int, c_int)
def state_cb(t, r):
    print(f"[STATE] conn_type={t} result={r}", flush=True)

@WINFUNCTYPE(None, c_size_t, c_size_t, c_uint)
def trade_cb(asset_id, trade_ptr, flags):
    trades_received.append(1)
    print(f"[TRADE] asset_id={asset_id:#x} trade_ptr={trade_ptr:#x} flags={flags}", flush=True)

dll.SetTradeCallbackV2(trade_cb)
print("[OK] SetTradeCallbackV2 registrado", flush=True)

dll.DLLInitializeLogin(
    c_wchar_p(os.getenv("PROFIT_ACTIVATION_KEY", "")),
    c_wchar_p(os.getenv("PROFIT_USERNAME", "")),
    c_wchar_p(os.getenv("PROFIT_PASSWORD", "")),
    state_cb,
    None, None, None, None, None, None, None, None, None, None,
)
print("[OK] DLLInitializeLogin chamado — aguardando routing (15s)...", flush=True)
time.sleep(15)

ret = dll.SubscribeTicker(c_wchar_p("WINFUT"), c_wchar_p("B"))
print(f"[OK] SubscribeTicker WINFUT ret={ret}", flush=True)

ret2 = dll.SubscribeTicker(c_wchar_p("WDOFUT"), c_wchar_p("B"))
print(f"[OK] SubscribeTicker WDOFUT ret={ret2}", flush=True)

print("[...] Aguardando 30s por ticks ao vivo...", flush=True)
time.sleep(30)

print(f"[RESULTADO] Total ticks recebidos: {len(trades_received)}", flush=True)
dll.DLLFinalize()
print("[OK] DLLFinalize", flush=True)
