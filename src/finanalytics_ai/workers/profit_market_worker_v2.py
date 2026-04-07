"""
profit_market_worker_v2.py
Worker minimo com ctypes direto.
IMPORTANTE: Redis inicializado APOS routing — Winsock conflict.
"""
from __future__ import annotations
import asyncio, ctypes, json, os, signal, sys
from ctypes import WINFUNCTYPE, c_int, c_size_t, c_uint, c_wchar_p
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dotenv import load_dotenv
load_dotenv(override=False)

from finanalytics_ai.observability.logging import get_logger, configure_logging
from finanalytics_ai.config import get_settings

log = get_logger(__name__)

routing_connected = False
market_connected  = False
dll = None
cb_trade_ref = None

def _parse_tickers() -> list[str]:
    raw = os.getenv("PROFIT_TICKERS", "PETR4,VALE3,ITUB4,BBDC4,ABEV3,WEGE3,WINFUT,WDOFUT")
    return [t.strip().upper() for t in raw.split(",") if t.strip()]

async def run() -> None:
    global routing_connected, market_connected, dll, cb_trade_ref

    settings = get_settings()
    configure_logging(settings)
    log.info("profit_worker_v2.starting")

    dll_path = os.getenv("PROFIT_DLL_PATH", r"C:\Nelogica\profitdll.dll")
    if not os.path.exists(dll_path):
        log.error("profit_worker_v2.dll_not_found", path=dll_path); return

    # Carrega DLL primeiro, antes de qualquer network
    dll = ctypes.WinDLL(dll_path)
    log.info("profit_worker_v2.dll_loaded")

    @WINFUNCTYPE(None, c_int, c_int)
    def state_cb(t: int, r: int) -> None:
        global routing_connected, market_connected
        if t == 1 and r >= 4: routing_connected = True
        if t == 2 and r >= 4: market_connected  = True
        if t == 2 and r == 0: market_connected  = False

    @WINFUNCTYPE(None, c_size_t, c_size_t, c_uint)
    def trade_cb(asset_id_raw: int, trade_ptr: int, flags: int) -> None:
        if dll is None or _redis_pub[0] is None:
            return
        try:
            from finanalytics_ai.infrastructure.market_data.profit_dll.types import (
                TConnectorAssetIdentifier as _AI, TConnectorTrade as _CT,
            )
            ai     = _AI.from_address(asset_id_raw)
            ticker = ai.Ticker or ""
            if not ticker:
                return
            trade = _CT()
            if dll.TranslateTrade(c_size_t(trade_ptr), ctypes.byref(trade)) != 0:
                return
            _redis_pub[0].publish("tape:ticks", json.dumps({
                "ticker":     ticker,
                "price":      trade.Price,
                "volume":     trade.Volume,
                "quantity":   int(trade.Quantity),
                "trade_type": int(trade.TradeType),
                "buy_agent":  int(trade.BuyAgent),
                "sell_agent": int(trade.SellAgent),
                "ts":         "now",
                "trade_number": int(trade.TradeNumber),
            }))
        except Exception:
            pass

    cb_trade_ref = trade_cb
    _redis_pub = [None]  # mutable container para acesso no callback

    key  = os.getenv("PROFIT_ACTIVATION_KEY", "")
    user = os.getenv("PROFIT_USERNAME", "")
    pwd  = os.getenv("PROFIT_PASSWORD", "")

    # Init DLL antes de qualquer network (evita conflito Winsock)
    dll.DLLInitializeLogin(
        c_wchar_p(key), c_wchar_p(user), c_wchar_p(pwd),
        state_cb,
        None, None, None, None, None, None, None, None, None, None,
    )
    log.info("profit_worker_v2.dll_initialized")

    # Aguarda routing
    for i in range(60):
        if routing_connected:
            log.info("profit_worker_v2.routing_connected", attempts=i); break
        await asyncio.sleep(0.5)
    else:
        log.warning("profit_worker_v2.routing_timeout")

    # Registra trade callback APOS routing
    dll.SetTradeCallbackV2(trade_cb)
    log.info("profit_worker_v2.trade_callback_registered")

    # Aguarda market data
    for i in range(60):
        if market_connected:
            log.info("profit_worker_v2.market_connected", attempts=i); break
        await asyncio.sleep(0.5)
    else:
        log.warning("profit_worker_v2.market_data_timeout")

    # SÓ AGORA inicializa Redis (depois de DLL conectar)
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        import redis as _rsync
        _redis_pub[0] = _rsync.from_url(redis_url, decode_responses=True)
        log.info("profit_worker_v2.redis_ready", url=redis_url)
    except Exception as e:
        log.warning("profit_worker_v2.redis_failed", error=str(e))

    # Subscreve tickers
    tickers = _parse_tickers()
    for ticker in tickers:
        ret = dll.SubscribeTicker(c_wchar_p(ticker), c_wchar_p("B"))
        if ret == 0:
            log.info("profit_worker_v2.subscribed", ticker=ticker)
        else:
            log.warning("profit_worker_v2.subscribe_failed", ticker=ticker, ret=ret)

    log.info("profit_worker_v2.running", market_connected=market_connected)

    stop = asyncio.Event()
    def _sig(*_): stop.set()
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT,  _sig)
    await stop.wait()

    dll.DLLFinalize()
    log.info("profit_worker_v2.stopped")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run())
