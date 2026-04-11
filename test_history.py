"""
test_history.py — Teste mínimo de GetHistoryTrades baseado no diag que funciona.
Replica EXATAMENTE o diag_asyncio_dll.py + GetHistoryTrades após market_connected.
"""
import asyncio, ctypes, os, sys, time, threading
from ctypes import WINFUNCTYPE, byref, c_int, c_size_t, c_uint, c_ushort, c_wchar_p, c_ubyte, c_int64, c_double
from ctypes import Structure, POINTER

sys.path.insert(0, r"D:\Projetos\finanalytics_ai_fresh\src")
from dotenv import load_dotenv
load_dotenv(r"D:\Projetos\finanalytics_ai_fresh\.env.local", override=False)

# ── DLL ──────────────────────────────────────────────────────────────────────
dll = ctypes.WinDLL(os.getenv("PROFIT_DLL_PATH", r"C:\Nelogica\profitdll.dll"))

# ── Tipos ─────────────────────────────────────────────────────────────────────
class SystemTime(Structure):
    _fields_ = [("wYear",c_ushort),("wMonth",c_ushort),("wDayOfWeek",c_ushort),
                ("wDay",c_ushort),("wHour",c_ushort),("wMinute",c_ushort),
                ("wSecond",c_ushort),("wMilliseconds",c_ushort)]

class TConnectorAssetIdentifier(Structure):
    _fields_ = [("Version",c_ubyte),("Ticker",c_wchar_p),
                ("Exchange",c_wchar_p),("FeedType",c_ubyte)]

class TConnectorTrade(Structure):
    _fields_ = [("Version",c_ubyte),("TradeDate",SystemTime),
                ("TradeNumber",c_uint),("Price",c_double),
                ("Quantity",c_int64),("Volume",c_double),
                ("TradeType",c_int),("BuyAgent",c_int),("SellAgent",c_int)]

# ── Configura argtypes ────────────────────────────────────────────────────────
dll.GetHistoryTrades.argtypes  = [c_wchar_p, c_wchar_p, c_wchar_p, c_wchar_p]
dll.GetHistoryTrades.restype   = c_int
dll.TranslateTrade.argtypes    = [c_size_t, POINTER(TConnectorTrade)]
dll.TranslateTrade.restype     = c_int
dll.SetHistoryTradeCallbackV2.restype = None

# ── Estado global (igual ao diag) ─────────────────────────────────────────────
routing_done     = False
market_connected = False
history_ticks    = []
history_done     = threading.Event()
TC_LAST_PACKET   = 0x02
TC_IS_EDIT       = 0x01
NL_OK            = 0

# ── Callbacks globais ─────────────────────────────────────────────────────────
@WINFUNCTYPE(None, c_int, c_int)
def state_cb(t, r):
    global routing_done, market_connected
    print(f"[STATE] {t} {r}", flush=True)
    if t == 1 and r >= 4: routing_done = True
    if t == 2 and r == 4: market_connected = True

@WINFUNCTYPE(None, TConnectorAssetIdentifier, c_size_t, c_uint)
def history_cb(asset_id, p_trade, flags):
    is_last = bool(flags & TC_LAST_PACKET)
    if not bool(flags & TC_IS_EDIT) and p_trade:
        trade = TConnectorTrade(Version=0)
        if dll.TranslateTrade(p_trade, byref(trade)) == NL_OK and trade.Price > 0:
            st = trade.TradeDate
            history_ticks.append({
                "ticker":   asset_id.Ticker,
                "price":    trade.Price / 100.0,
                "qty":      int(trade.Quantity),
                "type":     int(trade.TradeType),
                "date":     f"{st.wDay:02d}/{st.wMonth:02d}/{st.wYear} "
                            f"{st.wHour:02d}:{st.wMinute:02d}:{st.wSecond:02d}",
            })
            if len(history_ticks) % 500 == 0:
                print(f"[HIST] {len(history_ticks)} ticks...", flush=True)
    if is_last:
        print(f"[HIST] TC_LAST_PACKET — total: {len(history_ticks)} ticks", flush=True)
        history_done.set()

# ── Main (igual ao diag) ──────────────────────────────────────────────────────
async def main():
    dll.DLLInitializeLogin(
        c_wchar_p(os.getenv("PROFIT_ACTIVATION_KEY","")),
        c_wchar_p(os.getenv("PROFIT_USERNAME","")),
        c_wchar_p(os.getenv("PROFIT_PASSWORD","")),
        state_cb, None, None, None, None, None, None, None, None, None, None,
    )
    print("[OK] DLLInitializeLogin", flush=True)

    # Registra history callback IMEDIATAMENTE (padrão Delphi)
    dll.SetHistoryTradeCallbackV2(history_cb)
    print("[OK] SetHistoryTradeCallbackV2", flush=True)

    # Aguarda routing
    for _ in range(60):
        if routing_done: break
        await asyncio.sleep(0.5)
    print(f"[OK] routing_done={routing_done}", flush=True)

    # Aguarda market data
    for i in range(60):
        if market_connected:
            print(f"[OK] market_connected em {i*0.5:.1f}s", flush=True)
            break
        await asyncio.sleep(0.5)

    if not market_connected:
        print("[FAIL] market data não conectou", flush=True)
        dll.DLLFinalize()
        return

    # GetHistoryTrades — mesmo período do teste Delphi
    ticker   = os.getenv("PROFIT_TICKERS", "PETR4:B").split(",")[0].split(":")[0].strip()
    exchange = "B"
    dt_start = "09/04/2026 09:00:00"
    dt_end   = "10/04/2026 18:00:00"

    ret = dll.GetHistoryTrades(
        c_wchar_p(ticker), c_wchar_p(exchange),
        c_wchar_p(dt_start), c_wchar_p(dt_end),
    )
    print(f"[GetHistoryTrades] ret={ret} — aguardando callback...", flush=True)

    # Aguarda TC_LAST_PACKET (120s)
    if history_done.wait(timeout=120):
        print(f"\n[SUCESSO] {len(history_ticks)} ticks recebidos", flush=True)
        if history_ticks:
            print(f"  Primeiro: {history_ticks[0]}", flush=True)
            print(f"  Último:   {history_ticks[-1]}", flush=True)
    else:
        print(f"[TIMEOUT] Apenas {len(history_ticks)} ticks recebidos", flush=True)

    dll.DLLFinalize()
    print("[DONE]", flush=True)

if __name__ == "__main__":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
