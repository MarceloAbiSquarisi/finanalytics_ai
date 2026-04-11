"""
profit_history_worker.py

Arquitetura baseada nos exemplos oficiais Nelogica:
- Globais de módulo para flags de estado (igual ao diag que funciona)  
- DLL carregada no módulo antes do asyncio
- Callbacks definidos no módulo (não dentro de coroutine)
- SetHistoryTradeCallbackV2 registrado IMEDIATAMENTE após init (padrão Delphi)
- Busy-wait puro com time.sleep até market connected
- asyncio APENAS para persistência no DB
"""
from __future__ import annotations

import asyncio, os, sys, time, threading
from ctypes import (
    WINFUNCTYPE, WinDLL, Structure, POINTER, byref,
    c_double, c_int, c_int32, c_int64, c_size_t, c_ubyte, c_uint, c_ushort, c_wchar_p,
)
from dataclasses import dataclass
from datetime import datetime
from typing import Any

if sys.platform != "win32":
    sys.exit("Requer Windows.")

import structlog
log = structlog.get_logger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────
NL_OK          = 0
TC_IS_EDIT     = 0x01
TC_LAST_PACKET = 0x02

# ── Flags GLOBAIS (igual ao diag) ─────────────────────────────────────────────
routing_done     = False
market_connected = False

# ── Tipos ─────────────────────────────────────────────────────────────────────
class SystemTime(Structure):
    _fields_ = [
        ("wYear", c_ushort), ("wMonth", c_ushort), ("wDayOfWeek", c_ushort),
        ("wDay",  c_ushort), ("wHour",  c_ushort), ("wMinute",    c_ushort),
        ("wSecond", c_ushort), ("wMilliseconds", c_ushort),
    ]

class TAssetID(Structure):
    _fields_ = [("ticker", c_wchar_p), ("bolsa", c_wchar_p), ("feed", c_int)]

class TConnectorAssetIdentifier(Structure):
    _fields_ = [
        ("Version", c_ubyte), ("Ticker", c_wchar_p),
        ("Exchange", c_wchar_p), ("FeedType", c_ubyte),
    ]

class TConnectorTrade(Structure):
    _fields_ = [
        ("Version", c_ubyte), ("TradeDate", SystemTime),
        ("TradeNumber", c_uint), ("Price", c_double),
        ("Quantity", c_int64), ("Volume", c_double),
        ("BuyAgent", c_int), ("SellAgent", c_int), ("TradeType", c_ubyte),
    ]

# ── DLL global ────────────────────────────────────────────────────────────────
_dll: WinDLL | None = None

# ── Acumulador de ticks ───────────────────────────────────────────────────────
_ticks: list[dict] = []
_ticks_lock       = threading.Lock()
_history_done     = threading.Event()

# ── Callbacks GLOBAIS (como o diag) ───────────────────────────────────────────
@WINFUNCTYPE(None, c_int, c_int)
def state_cb(t: int, r: int) -> None:
    global routing_done, market_connected
    print(f"[STATE] {t} {r}", flush=True)
    if t == 1 and r >= 4:
        routing_done = True
    if t == 2 and r == 4:
        market_connected = True

@WINFUNCTYPE(None, TConnectorAssetIdentifier, c_size_t, c_uint)
def history_trade_cb(asset_id: TConnectorAssetIdentifier, p_trade: int, flags: int) -> None:
    """Callback V2 para SetHistoryTradeCallbackV2."""
    is_last = bool(flags & TC_LAST_PACKET)

    if not bool(flags & TC_IS_EDIT) and p_trade and _dll:
        trade = TConnectorTrade(Version=0)
        if _dll.TranslateTrade(p_trade, byref(trade)) == NL_OK and trade.Price > 0:
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
                    "ts": ts,
                    "price": trade.Price,
                    "quantity": int(trade.Quantity),
                    "volume": trade.Volume,
                })

    if is_last:
        _history_done.set()


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
            b["high"] = max(b["high"], t["price"]); b["low"] = min(b["low"], t["price"])
            b["close"] = t["price"]; b["volume"] += t["volume"]
            b["quantity"] += t["quantity"]; b["trade_count"] += 1
    return sorted(
        [OHLCBar(k[0], k[1], k[2], res, **v) for k, v in bars.items()],
        key=lambda x: x.ts)


# ── Persistência async ────────────────────────────────────────────────────────
async def persist(bars: list[OHLCBar], resolution: str, dsn: str) -> int:
    if not bars: return 0
    import asyncpg
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ohlc_history (
                ticker TEXT, exchange TEXT, ts TIMESTAMPTZ, resolution TEXT,
                open DOUBLE PRECISION, high DOUBLE PRECISION, low DOUBLE PRECISION,
                close DOUBLE PRECISION, volume DOUBLE PRECISION,
                quantity BIGINT, trade_count INT,
                PRIMARY KEY (ticker, ts, resolution))""")
        rows = [(b.ticker, b.exchange, b.ts, b.resolution, b.open, b.high,
                 b.low, b.close, b.volume, b.quantity, b.trade_count) for b in bars]
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


# ── Inicialização da DLL ──────────────────────────────────────────────────────
def init_dll(dll_path: str, activation_key: str, username: str, password: str) -> None:
    """
    Inicia a DLL e aguarda market data.
    
    Padrão: globais de módulo + callbacks globais + busy-wait com time.sleep
    Idêntico ao diag_asyncio_dll.py que funciona, mas sem asyncio nesta fase.
    
    Delphi: SetHistoryTradeCallbackV2 registrado IMEDIATAMENTE após init.
    """
    global _dll, routing_done, market_connected

    routing_done = market_connected = False

    _dll = WinDLL(dll_path)
    _dll.DLLInitializeLogin.restype        = c_int32
    _dll.GetHistoryTrades.argtypes         = [c_wchar_p, c_wchar_p, c_wchar_p, c_wchar_p]
    _dll.GetHistoryTrades.restype          = c_int32
    _dll.TranslateTrade.argtypes           = [c_size_t, POINTER(TConnectorTrade)]
    _dll.TranslateTrade.restype            = c_int
    _dll.SetHistoryTradeCallbackV2.restype = None

    ret = _dll.DLLInitializeLogin(
        c_wchar_p(activation_key), c_wchar_p(username), c_wchar_p(password),
        state_cb,
        None, None, None, None, None, None, None, None, None, None,
    )
    if ret < NL_OK:
        raise RuntimeError(f"DLLInitializeLogin falhou: {ret}")
    print(f"[INIT] ret={ret}", flush=True)

    # Registra SetHistoryTradeCallbackV2 IMEDIATAMENTE — padrão Delphi
    _dll.SetHistoryTradeCallbackV2(history_trade_cb)
    print("[INIT] SetHistoryTradeCallbackV2 registrado", flush=True)

    # Aguarda routing — busy-wait com time.sleep (não asyncio)
    print("[WAIT] routing...", flush=True)
    for _ in range(60):
        if routing_done: break
        time.sleep(0.5)
    if not routing_done:
        _dll.DLLFinalize(); raise RuntimeError("routing não conectou em 30s")
    print("[OK] routing_done", flush=True)

    # Aguarda market data — busy-wait
    print("[WAIT] market data...", flush=True)
    for i in range(240):  # 120s
        if market_connected:
            print(f"[OK] market_connected em {i*0.5:.1f}s", flush=True)
            break
        time.sleep(0.5)
    if not market_connected:
        _dll.DLLFinalize()
        raise RuntimeError("market data não conectou em 120s")
    log.info("dll.market_connected")


def collect_history(ticker: str, exchange: str, dt_start: datetime,
                    dt_end: datetime, timeout: float = 120.0) -> list[dict]:
    """Solicita histórico e aguarda TC_LAST_PACKET."""
    global _ticks
    _ticks = []
    _history_done.clear()

    fmt = "%d/%m/%Y %H:%M:%S"
    ret = _dll.GetHistoryTrades(
        c_wchar_p(ticker), c_wchar_p(exchange),
        c_wchar_p(dt_start.strftime(fmt)), c_wchar_p(dt_end.strftime(fmt)),
    )

    if ret < NL_OK:
        raise RuntimeError(f"GetHistoryTrades({ticker}) falhou: ret={ret}")

    log.info("history.requesting", ticker=ticker)

    if not _history_done.wait(timeout=timeout):
        raise TimeoutError(f"Timeout aguardando histórico de {ticker}")

    with _ticks_lock:
        return list(_ticks)


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    import logging; logging.basicConfig(level=logging.INFO)

    dll_path       = os.environ["PROFIT_DLL_PATH"]
    activation_key = os.environ["PROFIT_ACTIVATION_KEY"]
    username       = os.environ["PROFIT_USERNAME"]
    password       = os.environ["PROFIT_PASSWORD"]
    ts_dsn         = os.environ.get("TIMESCALE_DSN",
                     "postgresql://finanalytics:finanalytics@localhost:5433/market_data")

    tickers = []
    for item in os.environ.get("PROFIT_TICKERS", "PETR4:B").split(","):
        parts = item.strip().split(":")
        tickers.append((parts[0].strip(), parts[1].strip() if len(parts) > 1 else "B"))

    fmt      = "%d/%m/%Y %H:%M:%S"
    dt_start = datetime.strptime(os.environ.get("HISTORY_DATE_START", "09/04/2026 09:00:00"), fmt)
    dt_end   = datetime.strptime(os.environ.get("HISTORY_DATE_END",   "10/04/2026 18:00:00"), fmt)
    resolutions = [r.strip() for r in os.environ.get("HISTORY_RESOLUTIONS", "1,5,15,60,D").split(",") if r.strip()]
    timeout  = float(os.environ.get("HISTORY_TIMEOUT", "120"))

    log.info("history.worker.starting")

    # Fase 1: DLL — sem asyncio, globais de módulo
    init_dll(dll_path, activation_key, username, password)

    # Fase 2: coleta de histórico — síncrona
    all_ticks: dict[str, list[dict]] = {}
    for ticker, exchange in tickers:
        try:
            ticks = collect_history(ticker, exchange, dt_start, dt_end, timeout)
            all_ticks[ticker] = ticks
            log.info("history.collected", ticker=ticker, ticks=len(ticks))
        except (RuntimeError, TimeoutError) as ex:
            log.error("history.error", ticker=ticker, error=str(ex))
        time.sleep(1.0)

    _dll.DLLFinalize()
    log.info("dll.finalized")

    # Fase 3: persistência — asyncio
    async def _persist_all() -> None:
        total = 0
        for ticker, exchange in tickers:
            for res in resolutions:
                bars = aggregate(all_ticks.get(ticker, []), res)
                n = await persist(bars, res, ts_dsn)
                total += n
                log.info("aggregated", ticker=ticker, resolution=res, bars=len(bars))
        log.info("history.worker.done", total_bars=total)

    asyncio.run(_persist_all())


if __name__ == "__main__":
    main()
