$destino = "D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\workers\profit_history_worker.py"

$conteudo = @'
"""
profit_history_worker.py  (v15 - baseado no profit_agent.py que funciona)

SOLUCAO DEFINITIVA encontrada no profit_agent.py:

  CRITICO 1: NewTradeCallback (pos 8) e NewDailyCallback (pos 9) precisam ser
             callbacks REAIS com primeiro argumento c_void_p — NAO None e NAO
             TAssetID. A DLL nao entrega result=4 (MARKET_CONNECTED) sem eles.

  CRITICO 2: Set*Callback chamado ANTES do DLLInitializeLogin impede result=4.
             Todo Set* deve ser chamado APOS market_connected.

  CRITICO 3: AccountCallback (pos 7) tambem precisa ser real.

  Pattern do profit_agent.py (comentario interno):
    'padrao identico ao 02_test_state_callback.py e 05_test_trade_v2.py
     que conectaram'
    'Callbacks V1 REAIS para DLLInitializeLogin — padrao do teste 11'
"""
from __future__ import annotations

import asyncio, ctypes, os, sys, time, threading
from ctypes import (
    WINFUNCTYPE, WinDLL, Structure, POINTER, byref,
    c_char, c_double, c_int, c_int64, c_longlong, c_size_t,
    c_ubyte, c_uint, c_ushort, c_void_p, c_wchar_p, c_wchar,
)
from dataclasses import dataclass
from datetime import datetime

if sys.platform != "win32":
    sys.exit("Requer Windows.")

# Carrega .env igual ao profit_agent.py
def _load_env(path: str) -> None:
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip(); v = v.strip().strip('"').strip("'")
                    if k not in os.environ:
                        os.environ[k] = v
    except FileNotFoundError:
        pass

_load_env(r"D:\Projetos\finanalytics_ai_fresh\.env")
_load_env(r"D:\Projetos\finanalytics_ai_fresh\.env.local")

import structlog
log = structlog.get_logger(__name__)

NL_OK          = 0
TC_IS_EDIT     = 0x01
TC_LAST_PACKET = 0x02

CONN_STATE_MARKET_DATA = 2
MARKET_CONNECTED       = 4

# ── DLL ───────────────────────────────────────────────────────────────────────
dll = WinDLL(os.getenv("PROFIT_DLL_PATH", r"C:\Nelogica\profitdll.dll"))
dll.DLLInitializeLogin.restype       = c_int
dll.DLLFinalize.restype              = c_int
dll.SetHistoryTradeCallbackV2.restype= None
dll.SetEnabledHistOrder.restype      = None
dll.SubscribeTicker.argtypes         = [c_wchar_p, c_wchar_p]
dll.SubscribeTicker.restype          = c_int
dll.GetHistoryTrades.argtypes        = [c_wchar_p, c_wchar_p, c_wchar_p, c_wchar_p]
dll.GetHistoryTrades.restype         = c_int

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
        ("Version", c_ubyte), ("TradeDate", SystemTime),
        ("TradeNumber", c_uint), ("Price", c_double),
        ("Quantity", c_longlong), ("Volume", c_double),
        ("BuyAgent", c_int), ("SellAgent", c_int), ("TradeType", c_ubyte),
    ]

# ── Estado global ─────────────────────────────────────────────────────────────
routing_done     = False
market_connected = threading.Event()

_ticks: list[dict] = []
_ticks_lock   = threading.Lock()
_history_done = threading.Event()

# ── Callbacks para DLLInitializeLogin ────────────────────────────────────────
# CRITICO: usar c_void_p como primeiro arg dos callbacks V1 (nao TAssetID)
# CRITICO: _trade_v1 e _daily_v1 precisam ser REAIS (nao None) para result=4

@WINFUNCTYPE(None, c_int, c_int)
def _state_cb(conn_type: int, result: int) -> None:
    global routing_done
    print(f"[STATE] {conn_type} {result}", flush=True)
    if conn_type == 1 and result >= 4:
        routing_done = True
    if conn_type == CONN_STATE_MARKET_DATA and result == MARKET_CONNECTED:
        market_connected.set()

# NewTradeCallback V1 — REAL, c_void_p (nao TAssetID)
@WINFUNCTYPE(None, c_void_p, c_wchar_p, c_uint, c_double, c_double,
             c_int, c_int, c_int, c_int, c_char)
def _trade_v1(asset_ptr, date, trade_num, price, vol,
              qty, buy_agent, sell_agent, trade_type, edit) -> None:
    pass  # V2 callback cuida dos dados reais

# NewDailyCallback V1 — REAL, c_void_p (nao TAssetID)
@WINFUNCTYPE(None, c_void_p, c_wchar_p,
             c_double, c_double, c_double, c_double, c_double, c_double,
             c_double, c_double, c_double, c_double,
             c_int, c_int, c_int, c_int, c_int, c_int, c_int)
def _daily_v1(*_) -> None:
    pass

# AccountCallback — REAL (pos 7)
@WINFUNCTYPE(None, c_int, c_wchar_p, c_wchar_p, c_wchar_p)
def _account_cb(bid, bname, aid, owner) -> None:
    pass

# Progress e TinyBook — noops com c_void_p
@WINFUNCTYPE(None, c_void_p, c_int)
def _progress_cb(*_) -> None:
    pass

@WINFUNCTYPE(None, c_void_p, c_double, c_int, c_int)
def _tiny_book_cb(*_) -> None:
    pass

# Guarda refs contra GC — CRITICO
_INIT_REFS = [_state_cb, _trade_v1, _daily_v1, _account_cb, _progress_cb, _tiny_book_cb]

# SetHistoryTradeCallbackV2 — registrado APOS market_connected
@WINFUNCTYPE(None, TConnectorAssetIdentifier, c_size_t, c_uint)
def _history_trade_v2(asset_id, p_trade, flags) -> None:
    is_last = bool(flags & TC_LAST_PACKET)
    if not bool(flags & TC_IS_EDIT) and p_trade:
        trade = TConnectorTrade(Version=0)
        if dll.TranslateTrade(p_trade, byref(trade)) == NL_OK and trade.Price > 0:
            st = trade.TradeDate
            try:
                ts = datetime(st.wYear, st.wMonth, st.wDay,
                              st.wHour, st.wMinute, st.wSecond,
                              st.wMilliseconds * 1000)
            except ValueError:
                ts = datetime.utcnow()
            with _ticks_lock:
                _ticks.append({
                    "ticker":   asset_id.Ticker or "",
                    "exchange": asset_id.Exchange or "",
                    "ts": ts, "price": trade.Price,
                    "quantity": int(trade.Quantity),
                    "volume":   trade.Volume,
                })
    if is_last:
        ticker = asset_id.Ticker or "?"
        with _ticks_lock:
            n = len(_ticks)
        print(f"[DONE] TC_LAST_PACKET ticker={ticker} ticks={n}", flush=True)
        _history_done.set()

dll.TranslateTrade.argtypes = [c_size_t, POINTER(TConnectorTrade)]
dll.TranslateTrade.restype  = c_int

# ── OHLC ──────────────────────────────────────────────────────────────────────
@dataclass
class OHLCBar:
    ticker: str; exchange: str; ts: datetime; resolution: str
    open: float; high: float; low: float; close: float
    volume: float; quantity: int; trade_count: int

def _bucket(ts: datetime, res: str) -> datetime:
    if res == "D":
        return ts.replace(hour=0, minute=0, second=0, microsecond=0)
    m = int(res); total = ts.hour * 60 + ts.minute; b = (total // m) * m
    return ts.replace(hour=b // 60, minute=b % 60, second=0, microsecond=0)

def aggregate(ticks: list[dict], res: str) -> list[OHLCBar]:
    bars: dict = {}
    for t in ticks:
        k = (t["ticker"], t["exchange"], _bucket(t["ts"], res))
        if k not in bars:
            bars[k] = {"open": t["price"], "high": t["price"], "low": t["price"],
                       "close": t["price"], "volume": t["volume"],
                       "quantity": t["quantity"], "trade_count": 1}
        else:
            b = bars[k]
            b["high"]        = max(b["high"], t["price"])
            b["low"]         = min(b["low"],  t["price"])
            b["close"]       = t["price"]
            b["volume"]     += t["volume"]
            b["quantity"]   += t["quantity"]
            b["trade_count"]+= 1
    return sorted(
        [OHLCBar(k[0], k[1], k[2], res, **v) for k, v in bars.items()],
        key=lambda x: x.ts)

async def persist(bars: list[OHLCBar], resolution: str, dsn: str) -> int:
    if not bars:
        return 0
    import asyncpg
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ohlc_history (
                ticker TEXT, exchange TEXT, ts TIMESTAMPTZ, resolution TEXT,
                open DOUBLE PRECISION, high DOUBLE PRECISION,
                low DOUBLE PRECISION, close DOUBLE PRECISION,
                volume DOUBLE PRECISION, quantity BIGINT, trade_count INT,
                PRIMARY KEY (ticker, ts, resolution))""")
        rows = [(b.ticker, b.exchange, b.ts, b.resolution,
                 b.open, b.high, b.low, b.close, b.volume, b.quantity, b.trade_count)
                for b in bars]
        await conn.executemany("""
            INSERT INTO ohlc_history
              (ticker,exchange,ts,resolution,open,high,low,close,volume,quantity,trade_count)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            ON CONFLICT (ticker,ts,resolution) DO UPDATE SET
              open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
              close=EXCLUDED.close, volume=EXCLUDED.volume,
              quantity=EXCLUDED.quantity, trade_count=EXCLUDED.trade_count""", rows)
        log.info("persisted", resolution=resolution, rows=len(rows))
        return len(rows)
    finally:
        await conn.close()

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    tickers = []
    for item in os.getenv("PROFIT_TICKERS", "WINFUT:F").split(","):
        parts = item.strip().split(":")
        tickers.append((parts[0].strip(), parts[1].strip() if len(parts) > 1 else "B"))

    fmt      = "%d/%m/%Y %H:%M:%S"
    dt_start = datetime.strptime(os.getenv("HISTORY_DATE_START", "01/04/2026 09:00:00"), fmt)
    dt_end   = datetime.strptime(os.getenv("HISTORY_DATE_END",   "11/04/2026 18:00:00"), fmt)
    resolutions = [r.strip() for r in os.getenv("HISTORY_RESOLUTIONS", "1,5,15,60,D").split(",") if r.strip()]
    timeout  = float(os.getenv("HISTORY_TIMEOUT", "300"))
    ts_dsn   = os.getenv("TIMESCALE_DSN", os.getenv("PROFIT_TIMESCALE_DSN",
               "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"))

    print(f"[INFO] tickers={tickers} start={dt_start} end={dt_end}", flush=True)

    # DLLInitializeLogin — padrao do profit_agent.py que funciona:
    # pos 8 (_trade_v1) e pos 9 (_daily_v1) REAIS com c_void_p
    ret = dll.DLLInitializeLogin(
        c_wchar_p(os.getenv("PROFIT_ACTIVATION_KEY", "")),
        c_wchar_p(os.getenv("PROFIT_USERNAME", "")),
        c_wchar_p(os.getenv("PROFIT_PASSWORD", "")),
        _state_cb,     # 4 state
        None,          # 5 history (deprecated)
        None,          # 6 order_change (deprecated)
        _account_cb,   # 7 account — REAL
        _trade_v1,     # 8 new_trade V1 — REAL (necessario para result=4)
        _daily_v1,     # 9 new_daily V1 — REAL (necessario para result=4)
        None,          # 10 price_book
        None,          # 11 offer_book
        None,          # 12 history_trade (deprecated, usamos V2)
        _progress_cb,  # 13 progress
        _tiny_book_cb, # 14 tiny_book
    )
    if ret != 0:
        raise RuntimeError(f"DLLInitializeLogin falhou: ret={ret}")
    print(f"[OK] DLLInitializeLogin ret={ret}", flush=True)

    # Aguarda routing
    for _ in range(120):
        if routing_done: break
        await asyncio.sleep(0.5)
    print(f"[OK] routing_done={routing_done}", flush=True)

    # Aguarda market data — SEM Set*Callback antes (bloqueia result=4)
    print("[WAIT] market data...", flush=True)
    connected = market_connected.wait(timeout=120.0)
    if not connected:
        dll.DLLFinalize()
        raise RuntimeError("market data nao conectou em 120s")
    print("[OK] market_connected!", flush=True)
    log.info("dll.market_connected")

    # Set*Callback APOS market_connected — padrao profit_agent.py
    dll.SetEnabledHistOrder(1)
    dll.SetHistoryTradeCallbackV2(_history_trade_v2)
    print("[OK] SetEnabledHistOrder + SetHistoryTradeCallbackV2", flush=True)

    all_ticks: dict[str, list[dict]] = {}
    for ticker, exchange in tickers:
        global _ticks
        _ticks = []
        _history_done.clear()

        ret_sub = dll.SubscribeTicker(c_wchar_p(ticker), c_wchar_p(exchange))
        print(f"[SUB] SubscribeTicker({ticker}) ret={ret_sub}", flush=True)
        await asyncio.sleep(5.0)

        ret = dll.GetHistoryTrades(
            c_wchar_p(ticker), c_wchar_p(exchange),
            c_wchar_p(dt_start.strftime(fmt)),
            c_wchar_p(dt_end.strftime(fmt)),
        )
        print(f"[HIST] GetHistoryTrades({ticker}) ret={ret}", flush=True)

        if ret < NL_OK:
            log.error("history.error", ticker=ticker, error=f"ret={ret}")
            all_ticks[ticker] = []
            continue

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if _history_done.is_set(): break
            await asyncio.sleep(0.1)
        else:
            log.error("history.timeout", ticker=ticker)
            all_ticks[ticker] = []
            continue

        with _ticks_lock:
            all_ticks[ticker] = list(_ticks)
        log.info("history.collected", ticker=ticker, ticks=len(all_ticks[ticker]))
        await asyncio.sleep(5.0)

    dll.DLLFinalize()
    log.info("dll.finalized")

    total = 0
    for ticker, exchange in tickers:
        for res in resolutions:
            bars = aggregate(all_ticks.get(ticker, []), res)
            n    = await persist(bars, res, ts_dsn)
            total += n
            log.info("aggregated", ticker=ticker, resolution=res, bars=len(bars))
    log.info("history.worker.done", total_bars=total)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    log.info("history.worker.starting")
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    finally:
        try: dll.DLLFinalize()
        except Exception: pass
'@

$dir = Split-Path $destino
if (-not (Test-Path $dir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
}
[System.IO.File]::WriteAllText($destino, $conteudo, [System.Text.UTF8Encoding]::new($false))
Write-Host "Instalado: $destino" -ForegroundColor Green
