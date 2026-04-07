"""diag_trade_callback_v2.py
Registra SetTradeCallbackV2 APOS routing conectar.
Testa tambem contratos especificos (WINM26, WDOJ26).
"""
import ctypes, os, time
from ctypes import WINFUNCTYPE, c_int, c_size_t, c_uint, c_wchar_p
from dotenv import load_dotenv
load_dotenv(override=False)

dll_path = os.getenv("PROFIT_DLL_PATH", r"C:\Nelogica\profitdll.dll")
dll = ctypes.WinDLL(dll_path)
print(f"[OK] DLL: {dll_path}", flush=True)

trades_received = []
routing_done = False

@WINFUNCTYPE(None, c_int, c_int)
def state_cb(t, r):
    global routing_done
    print(f"[STATE] conn_type={t} result={r}", flush=True)
    if t == 1 and r >= 4:
        routing_done = True

@WINFUNCTYPE(None, c_size_t, c_size_t, c_uint)
def trade_cb(asset_id, trade_ptr, flags):
    trades_received.append(1)
    print(f"[TRADE] raw asset_id={asset_id:#x} trade_ptr={trade_ptr:#x} flags={flags}", flush=True)

dll.DLLInitializeLogin(
    c_wchar_p(os.getenv("PROFIT_ACTIVATION_KEY", "")),
    c_wchar_p(os.getenv("PROFIT_USERNAME", "")),
    c_wchar_p(os.getenv("PROFIT_PASSWORD", "")),
    state_cb,
    None, None, None, None, None, None, None, None, None, None,
)
print("[OK] DLLInitializeLogin chamado", flush=True)

# Espera routing (conn_type=1 result>=4)
for i in range(60):
    if routing_done:
        print(f"[OK] Routing conectado em {i*0.5:.1f}s", flush=True)
        break
    time.sleep(0.5)
else:
    print("[WARN] Routing timeout — continuando mesmo assim", flush=True)

# Registra callback APOS routing (nao antes do init)
dll.SetTradeCallbackV2(trade_cb)
print("[OK] SetTradeCallbackV2 registrado APOS routing", flush=True)

# Aguarda market connected (conn_type=2 result=4)
for i in range(40):
    time.sleep(0.5)

# Subscreve tickers genericos E contratos especificos
tickers = [
    ("WINFUT", "B"), ("WDOFUT", "B"),
    ("WINM26", "B"), ("WDOJ26", "B"),
    ("WINJ26", "B"),
]
for ticker, exch in tickers:
    ret = dll.SubscribeTicker(c_wchar_p(ticker), c_wchar_p(exch))
    print(f"[SUB] {ticker} ret={ret}", flush=True)

print("[...] Aguardando 30s por ticks...", flush=True)
time.sleep(30)

print(f"[RESULTADO] Ticks recebidos: {len(trades_received)}", flush=True)
dll.DLLFinalize()
