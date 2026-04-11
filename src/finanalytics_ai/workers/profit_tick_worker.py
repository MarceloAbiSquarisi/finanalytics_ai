"""
profit_tick_worker.py

Baseado DIRETAMENTE no test_history.py que recebeu 92.883 ticks.
Estrutura idêntica + SubscribeTicker + SetTradeCallbackV2 após market connected.
Roda como script standalone: uv run python profit_tick_worker.py
"""
import asyncio, ctypes, json, os, sys, time
from ctypes import WINFUNCTYPE, byref, c_int, c_int64, c_size_t, c_uint, c_ubyte, c_ushort, c_wchar_p
from ctypes import Structure, POINTER

sys.path.insert(0, r"D:\Projetos\finanalytics_ai_fresh\src")
from dotenv import load_dotenv
load_dotenv(r"D:\Projetos\finanalytics_ai_fresh\.env.local", override=False)

import structlog
log = structlog.get_logger(__name__)

# ── DLL no módulo — IGUAL ao test_history.py ─────────────────────────────────
dll = ctypes.WinDLL(os.getenv("PROFIT_DLL_PATH", r"C:\Nelogica\profitdll.dll"))

NL_OK = 0

class SystemTime(Structure):
    _fields_ = [
        ("wYear", c_ushort), ("wMonth", c_ushort), ("wDayOfWeek", c_ushort),
        ("wDay",  c_ushort), ("wHour",  c_ushort), ("wMinute",    c_ushort),
        ("wSecond", c_ushort), ("wMilliseconds", c_ushort),
    ]

class TConnectorAssetIdentifier(Structure):
    _fields_ = [
        ("Version", c_ubyte), ("Ticker", c_wchar_p),
        ("Exchange", c_wchar_p), ("FeedType", c_ubyte),
    ]

class TConnectorTrade(Structure):
    _fields_ = [
        ("Version",     c_ubyte),  ("TradeDate",   SystemTime),
        ("TradeNumber", c_uint),   ("Price",       ctypes.c_double),
        ("Quantity",    c_int64),  ("Volume",      ctypes.c_double),
        ("BuyAgent",    c_int),    ("SellAgent",   c_int),
        ("TradeType",   c_ubyte),
    ]

dll.TranslateTrade.argtypes           = [c_size_t, POINTER(TConnectorTrade)]
dll.TranslateTrade.restype            = c_int
dll.SetHistoryTradeCallbackV2.restype = None
dll.SetTradeCallbackV2.restype        = None
dll.SubscribeTicker.argtypes          = [c_wchar_p, c_wchar_p]
dll.SubscribeTicker.restype           = c_int

# ── Estado global — IGUAL ao test_history.py ─────────────────────────────────
routing_done     = False
market_connected = False
tick_count       = 0
_queue: list     = []

# ── Callbacks — IGUAL ao test_history.py ─────────────────────────────────────
@WINFUNCTYPE(None, c_int, c_int)
def state_cb(t, r):
    global routing_done, market_connected
    print(f"[STATE] {t} {r}", flush=True)
    if t == 1 and r >= 4: routing_done = True
    if t == 2 and r == 4: market_connected = True

@WINFUNCTYPE(None, TConnectorAssetIdentifier, c_size_t, c_uint)
def hist_noop(*_): pass

@WINFUNCTYPE(None, TConnectorAssetIdentifier, c_size_t, c_uint)
def trade_cb(asset_id, p_trade, flags):
    global tick_count
    if not p_trade or bool(flags & 1): return
    trade = TConnectorTrade(Version=0)
    if dll.TranslateTrade(p_trade, byref(trade)) != NL_OK or trade.Price <= 0: return
    st = trade.TradeDate
    tick_count += 1
    tick = {
        "ticker":   asset_id.Ticker or "",
        "exchange": asset_id.Exchange or "",
        "ts":       f"{st.wYear:04d}-{st.wMonth:02d}-{st.wDay:02d}T"
                    f"{st.wHour:02d}:{st.wMinute:02d}:{st.wSecond:02d}.{st.wMilliseconds:03d}",
        "price":    trade.Price,
        "qty":      int(trade.Quantity),
        "vol":      trade.Volume,
        "type":     int(trade.TradeType),
    }
    if tick_count % 10 == 1:
        print(f"[TICK #{tick_count}] {tick['ticker']} "
              f"P={tick['price']:.4f} Q={tick['qty']} {tick['ts'][11:19]}", flush=True)
    _queue.append(json.dumps(tick))


# ── Main — IGUAL ao test_history.py ──────────────────────────────────────────
async def main():
    tickers = [
        (p[0].strip(), p[1].strip() if len(p) > 1 else "B")
        for item in os.getenv("PROFIT_TICKERS", "PETR4:B").split(",")
        for p in [item.strip().split(":")]
    ]
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # IGUAL ao test_history.py
    dll.DLLInitializeLogin(
        c_wchar_p(os.getenv("PROFIT_ACTIVATION_KEY", "")),
        c_wchar_p(os.getenv("PROFIT_USERNAME", "")),
        c_wchar_p(os.getenv("PROFIT_PASSWORD", "")),
        state_cb, None, None, None, None, None, None, None, None, None, None,
    )
    print("[OK] DLLInitializeLogin", flush=True)

    dll.SetHistoryTradeCallbackV2(hist_noop)
    print("[OK] SetHistoryTradeCallbackV2", flush=True)

    # Aguarda routing — IGUAL ao test_history.py
    for _ in range(60):
        if routing_done: break
        await asyncio.sleep(0.5)
    print(f"[OK] routing_done={routing_done}", flush=True)

    # Aguarda market_connected — IGUAL ao test_history.py
    for i in range(60):
        if market_connected:
            print(f"[OK] market_connected em {i*0.5:.1f}s", flush=True)
            break
        await asyncio.sleep(0.5)

    if not market_connected:
        dll.DLLFinalize()
        raise RuntimeError("market data não conectou")

    log.info("dll.market_connected")

    # SetTradeCallbackV2 após market connected — igual ao diag
    dll.SetTradeCallbackV2(trade_cb)
    print("[OK] SetTradeCallbackV2", flush=True)

    # SubscribeTicker
    for ticker, exchange in tickers:
        ret = dll.SubscribeTicker(c_wchar_p(ticker), c_wchar_p(exchange))
        print(f"[SUB] {ticker}:{exchange} ret={ret}", flush=True)

    log.info("tick.worker.running", tickers=[f"{t}:{e}" for t, e in tickers])

    # Redis
    async def publish():
        try:
            import redis.asyncio as r
            rc = await r.from_url(redis_url)
            log.info("redis.connected")
            while True:
                await asyncio.sleep(0.05)
                if _queue:
                    batch, _queue[:] = _queue[:], []
                    pipe = rc.pipeline()
                    for m in batch: pipe.publish("tape:ticks", m)
                    await pipe.execute()
        except Exception as e:
            log.warning("redis.offline", error=str(e))
            while True: await asyncio.sleep(5)

    asyncio.create_task(publish())

    last = time.time()
    while True:
        await asyncio.sleep(5)
        if time.time() - last >= 30:
            log.info("heartbeat", ticks=tick_count)
            last = time.time()


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    log.info("tick.worker.starting")
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    finally:
        dll.DLLFinalize()
        log.info("dll.finalized")
