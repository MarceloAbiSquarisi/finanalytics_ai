"""diag_asyncio_dll.py — testa DLL com asyncio rodando."""
import asyncio, ctypes, os, sys, time
from ctypes import WINFUNCTYPE, c_int, c_size_t, c_uint, c_wchar_p
sys.path.insert(0, r"D:\Projetos\finanalytics_ai_fresh\src")
from dotenv import load_dotenv
load_dotenv(override=False)

dll = ctypes.WinDLL(os.getenv("PROFIT_DLL_PATH", r"C:\Nelogica\profitdll.dll"))
raw_calls = []
market_connected = False
routing_done = False

@WINFUNCTYPE(None, c_int, c_int)
def state_cb(t, r):
    global routing_done, market_connected
    print(f"[STATE] {t} {r}", flush=True)
    if t == 1 and r >= 4: routing_done = True
    if t == 2 and r == 4: market_connected = True

@WINFUNCTYPE(None, c_size_t, c_size_t, c_uint)
def trade_cb(asset_id, trade_ptr, flags):
    raw_calls.append(1)
    print(f"[TRADE] #{len(raw_calls)} ptr={trade_ptr:#x}", flush=True)

async def main():
    dll.DLLInitializeLogin(
        c_wchar_p(os.getenv("PROFIT_ACTIVATION_KEY","")),
        c_wchar_p(os.getenv("PROFIT_USERNAME","")),
        c_wchar_p(os.getenv("PROFIT_PASSWORD","")),
        state_cb, None, None, None, None, None, None, None, None, None, None,
    )
    print("[OK] DLLInitializeLogin chamado", flush=True)

    # Espera routing
    for _ in range(30):
        if routing_done: break
        await asyncio.sleep(0.5)
    print(f"[OK] routing_done={routing_done}", flush=True)

    # Registra callback APOS routing (igual ao diagnostico que funciona)
    dll.SetTradeCallbackV2(trade_cb)
    print("[OK] SetTradeCallbackV2 registrado", flush=True)

    # Espera market data (max 15s)
    for i in range(30):
        if market_connected:
            print(f"[OK] market_connected em {i*0.5:.1f}s", flush=True)
            break
        await asyncio.sleep(0.5)
    print(f"[INFO] market_connected={market_connected}", flush=True)

    # Subscreve
    for t in ["WINFUT", "WDOFUT", "PETR4"]:
        ret = dll.SubscribeTicker(c_wchar_p(t), c_wchar_p("B"))
        print(f"[SUB] {t} ret={ret}", flush=True)

    print("[...] Aguardando 60s...", flush=True)
    for i in range(60):
        await asyncio.sleep(1)
        if (i+1) % 10 == 0:
            print(f"  {i+1}s — raw={len(raw_calls)} market={market_connected}", flush=True)

    print(f"\n[RESULTADO] raw={len(raw_calls)} market_connected={market_connected}", flush=True)
    dll.DLLFinalize()

if __name__ == "__main__":
    import sys
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
