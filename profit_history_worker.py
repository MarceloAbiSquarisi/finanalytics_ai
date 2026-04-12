"""
profit_history_worker.py

Worker de histórico via ProfitDLL → TimescaleDB.

Padrão baseado no profit_tick_worker.py (standalone script) que funciona:
  - DLL carregada no nível de módulo
  - Callbacks globais
  - asyncio apenas para persistência

Rodando: uv run python profit_history_worker.py

Variáveis de ambiente (.env.local):
  PROFIT_DLL_PATH, PROFIT_ACTIVATION_KEY, PROFIT_USERNAME, PROFIT_PASSWORD
  PROFIT_TICKERS       = PETR4:B,VALE3:B,WINFUT:F
  HISTORY_DATE_START   = 09/04/2026 09:00:00
  HISTORY_DATE_END     = 10/04/2026 18:00:00
  HISTORY_RESOLUTIONS  = 1,5,15,60,D
  TIMESCALE_DSN        = postgresql://finanalytics:finanalytics@localhost:5433/market_data
"""
import asyncio, ctypes, os, sys, time, threading
from ctypes import (
    WINFUNCTYPE, byref, c_double, c_int, c_int64,
    c_size_t, c_ubyte, c_uint, c_ushort, c_wchar_p, c_int32,
)
from ctypes import Structure, POINTER
from dataclasses import dataclass
from datetime import datetime, timezone

if sys.platform != "win32":
    sys.exit("Requer Windows.")

sys.path.insert(0, r"D:\Projetos\finanalytics_ai_fresh\src")

import structlog
log = structlog.get_logger(__name__)

NL_OK          = 0
TC_IS_EDIT     = 0x01
TC_LAST_PACKET = 0x02

# ── DLL no módulo — igual ao profit_tick_worker.py ───────────────────────────
dll = ctypes.WinDLL(os.getenv("PROFIT_DLL_PATH", r"C:\Nelogica\profitdll.dll"))

# ── Tipos ─────────────────────────────────────────────────────────────────────
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
        ("TradeNumber", c_uint),   ("Price",       c_double),
        ("Quantity",    c_int64),  ("Volume",      c_double),
        ("BuyAgent",    c_int),    ("SellAgent",   c_int),
        ("TradeType",   c_ubyte),
    ]

# ── argtypes ──────────────────────────────────────────────────────────────────
dll.DLLInitializeLogin.restype        = c_int32
dll.GetHistoryTrades.argtypes         = [c_wchar_p, c_wchar_p, c_wchar_p, c_wchar_p]
dll.GetHistoryTrades.restype          = c_int32
dll.TranslateTrade.argtypes           = [c_size_t, POINTER(TConnectorTrade)]
dll.TranslateTrade.restype            = c_int
dll.SetHistoryTradeCallbackV2.restype = None

# ── Estado global ─────────────────────────────────────────────────────────────
routing_done     = False
market_connected = False
_ticks: list     = []
_ticks_lock      = threading.Lock()
_history_done    = threading.Event()
_current_ticker  = ""

# ── Callbacks globais ─────────────────────────────────────────────────────────
@WINFUNCTYPE(None, c_int, c_int)
def state_cb(t, r):
    global routing_done, market_connected
    print(f"[STATE] {t} {r}", flush=True)
    if t == 1 and r >= 4: routing_done = True
    if t == 2:
        market_connected = (r == 4)  # False em disconnect, True em reconnect

@WINFUNCTYPE(None, TConnectorAssetIdentifier, c_size_t, c_uint)
def history_cb(asset_id, p_trade, flags):
    is_last = bool(flags & TC_LAST_PACKET)
    if not bool(flags & TC_IS_EDIT) and p_trade:
        trade = TConnectorTrade(Version=0)
        if dll.TranslateTrade(p_trade, byref(trade)) == NL_OK and trade.Price > 0:
            st = trade.TradeDate
            try:
                ts = datetime(
                    st.wYear, st.wMonth, st.wDay,
                    st.wHour, st.wMinute, st.wSecond,
                    st.wMilliseconds * 1000,
                    tzinfo=timezone.utc,
                )
            except ValueError:
                ts = datetime.now(timezone.utc)

            with _ticks_lock:
                _ticks.append({
                    "ticker":       asset_id.Ticker or _current_ticker,
                    "exchange":     asset_id.Exchange or "B",
                    "ts":           ts,
                    "trade_number": int(trade.TradeNumber),
                    "price":        trade.Price,
                    "quantity":     int(trade.Quantity),
                    "volume":       trade.Volume,
                    "buy_agent":    trade.BuyAgent,
                    "sell_agent":   trade.SellAgent,
                    "trade_type":   int(trade.TradeType),
                })
    if is_last:
        print(f"[HIST] TC_LAST_PACKET — {len(_ticks)} ticks", flush=True)
        _history_done.set()

# ── Modelo OHLC ───────────────────────────────────────────────────────────────
@dataclass
class OHLCBar:
    ticker: str; exchange: str; ts: datetime; resolution: str
    open: float; high: float; low: float; close: float
    volume: float; quantity: int; trade_count: int

def _bucket(ts: datetime, res: str) -> datetime:
    if res == "D":
        return ts.replace(hour=0, minute=0, second=0, microsecond=0)
    m = int(res)
    total = ts.hour * 60 + ts.minute
    b = (total // m) * m
    return ts.replace(hour=b // 60, minute=b % 60, second=0, microsecond=0)

def aggregate(ticks: list[dict], res: str) -> list[OHLCBar]:
    bars: dict = {}
    for t in ticks:
        k = (t["ticker"], t["exchange"], _bucket(t["ts"], res))
        if k not in bars:
            bars[k] = {
                "open": t["price"], "high": t["price"],
                "low":  t["price"], "close": t["price"],
                "volume": t["volume"], "quantity": t["quantity"],
                "trade_count": 1,
            }
        else:
            b = bars[k]
            b["high"]  = max(b["high"], t["price"])
            b["low"]   = min(b["low"],  t["price"])
            b["close"] = t["price"]
            b["volume"]   += t["volume"]
            b["quantity"]  += t["quantity"]
            b["trade_count"] += 1
    return sorted(
        [OHLCBar(k[0], k[1], k[2], res, **v) for k, v in bars.items()],
        key=lambda x: x.ts,
    )

# ── Persistência TimescaleDB ──────────────────────────────────────────────────
UPSERT_TICKS = """
    INSERT INTO ticks
        (ticker, exchange, ts, trade_number, price, quantity,
         volume, buy_agent, sell_agent, trade_type)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
    ON CONFLICT (ticker, ts, trade_number) DO UPDATE SET
        price      = EXCLUDED.price,
        quantity   = EXCLUDED.quantity,
        volume     = EXCLUDED.volume,
        trade_type = EXCLUDED.trade_type
"""

UPSERT_OHLC = """
    INSERT INTO ohlc
        (ticker, exchange, ts, resolution, open, high, low, close,
         volume, quantity, trade_count)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
    ON CONFLICT (ticker, ts, resolution) DO UPDATE SET
        open        = EXCLUDED.open,
        high        = GREATEST(ohlc.high, EXCLUDED.high),
        low         = LEAST(ohlc.low,     EXCLUDED.low),
        close       = EXCLUDED.close,
        volume      = EXCLUDED.volume,
        quantity    = EXCLUDED.quantity,
        trade_count = EXCLUDED.trade_count
"""

async def ensure_schema(conn) -> None:
    """Cria tabelas se não existirem (sem depender de migration manual)."""
    await conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ticks (
            ticker       TEXT        NOT NULL,
            exchange     TEXT        NOT NULL DEFAULT 'B',
            ts           TIMESTAMPTZ NOT NULL,
            trade_number BIGINT      NOT NULL DEFAULT 0,
            price        DOUBLE PRECISION NOT NULL,
            quantity     BIGINT      NOT NULL,
            volume       DOUBLE PRECISION,
            buy_agent    INT,
            sell_agent   INT,
            trade_type   SMALLINT    DEFAULT 0,
            CONSTRAINT ticks_pk PRIMARY KEY (ticker, ts, trade_number)
        )
    """)
    # hypertable (ignora erro se já existe)
    try:
        await conn.execute("""
            SELECT create_hypertable('ticks','ts',
                chunk_time_interval=>INTERVAL '1 day',
                if_not_exists=>TRUE)
        """)
    except Exception:
        pass

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlc (
            ticker      TEXT        NOT NULL,
            exchange    TEXT        NOT NULL DEFAULT 'B',
            ts          TIMESTAMPTZ NOT NULL,
            resolution  TEXT        NOT NULL,
            open        DOUBLE PRECISION NOT NULL,
            high        DOUBLE PRECISION NOT NULL,
            low         DOUBLE PRECISION NOT NULL,
            close       DOUBLE PRECISION NOT NULL,
            volume      DOUBLE PRECISION NOT NULL DEFAULT 0,
            quantity    BIGINT      NOT NULL DEFAULT 0,
            trade_count INT         NOT NULL DEFAULT 0,
            CONSTRAINT ohlc_pk PRIMARY KEY (ticker, ts, resolution)
        )
    """)
    try:
        await conn.execute("""
            SELECT create_hypertable('ohlc','ts',
                chunk_time_interval=>INTERVAL '30 days',
                if_not_exists=>TRUE)
        """)
    except Exception:
        pass

    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ticks_ticker_ts ON ticks (ticker, ts DESC)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ohlc_ticker_res_ts ON ohlc (ticker, resolution, ts DESC)
    """)
    log.info("db.schema_ready")


async def persist_ticks(conn, ticks: list[dict]) -> int:
    """Persiste ticks brutos com upsert idempotente."""
    if not ticks:
        return 0
    rows = [
        (t["ticker"], t["exchange"], t["ts"], t["trade_number"],
         t["price"], t["quantity"], t["volume"],
         t["buy_agent"], t["sell_agent"], t["trade_type"])
        for t in ticks
    ]
    await conn.executemany(UPSERT_TICKS, rows)
    return len(rows)


async def persist_ohlc(conn, bars: list[OHLCBar]) -> int:
    """Persiste candles OHLC com upsert idempotente."""
    if not bars:
        return 0
    rows = [
        (b.ticker, b.exchange, b.ts, b.resolution,
         b.open, b.high, b.low, b.close,
         b.volume, b.quantity, b.trade_count)
        for b in bars
    ]
    await conn.executemany(UPSERT_OHLC, rows)
    return len(rows)


# ── Coleta de histórico ───────────────────────────────────────────────────────
def _request_history(ticker, exchange, dt_start, dt_end):
    fmt = "%d/%m/%Y %H:%M:%S"
    return dll.GetHistoryTrades(
        c_wchar_p(ticker), c_wchar_p(exchange),
        c_wchar_p(dt_start.strftime(fmt)),
        c_wchar_p(dt_end.strftime(fmt)),
    )


def collect_history(ticker: str, exchange: str,
                    dt_start: datetime, dt_end: datetime,
                    timeout: float = 120.0) -> list[dict]:
    """
    Coleta histórico com retry automático em caso de reconexão de market data.
    Quando t=2 desconecta e reconecta (r=4), re-envia GetHistoryTrades.
    """
    global _ticks, _current_ticker, market_connected
    _ticks = []
    _current_ticker = ticker
    _history_done.clear()

    # Aguarda market data antes de solicitar
    for _ in range(60):
        if market_connected: break
        time.sleep(0.5)
    if not market_connected:
        raise RuntimeError(f"market data não conectado ao solicitar {ticker}")

    ret = _request_history(ticker, exchange, dt_start, dt_end)
    if ret < NL_OK:
        raise RuntimeError(f"GetHistoryTrades({ticker}) ret={ret}")
    log.info("history.requesting", ticker=ticker,
             start=dt_start.isoformat(), end=dt_end.isoformat())

    # Polling com detecção de reconexão de market data
    # Deadline reseta a cada retry para dar tempo completo após reconexão
    deadline = time.time() + timeout
    last_market_state = True
    while time.time() < deadline:
        if _history_done.wait(timeout=1.0):
            break
        cur = market_connected
        if not last_market_state and cur:
            # Market data reconectou — re-envia e reseta deadline
            print(f"[RETRY] market reconectou — re-enviando GetHistoryTrades {ticker}", flush=True)
            with _ticks_lock:
                _ticks.clear()
            _history_done.clear()
            _request_history(ticker, exchange, dt_start, dt_end)
            deadline = time.time() + timeout  # reseta deadline
            log.info("history.retry", ticker=ticker)
        last_market_state = cur
    else:
        raise TimeoutError(f"Timeout aguardando histórico de {ticker}")

    with _ticks_lock:
        return list(_ticks)


# ── Main ──────────────────────────────────────────────────────────────────────
async def persist_all(all_ticks: dict, tickers, resolutions, ts_dsn: str) -> None:
    import asyncpg
    conn = await asyncpg.connect(ts_dsn)
    try:
        await ensure_schema(conn)

        total_ticks = total_ohlc = 0
        for ticker, exchange in tickers:
            ticks = all_ticks.get(ticker, [])
            if not ticks:
                log.warning("persist.no_ticks", ticker=ticker)
                continue

            # 1) ticks brutos
            n = await persist_ticks(conn, ticks)
            total_ticks += n
            log.info("persist.ticks", ticker=ticker, rows=n)

            # 2) OHLC por resolução
            for res in resolutions:
                bars = aggregate(ticks, res)
                n = await persist_ohlc(conn, bars)
                total_ohlc += n
                log.info("persist.ohlc", ticker=ticker, resolution=res, bars=len(bars))

        log.info("persist.done", total_ticks=total_ticks, total_ohlc=total_ohlc)
    finally:
        await conn.close()


def main() -> None:
    import logging
    logging.basicConfig(level=logging.INFO)
    log.info("history.worker.starting")

    activation_key = os.environ["PROFIT_ACTIVATION_KEY"]
    username       = os.environ["PROFIT_USERNAME"]
    password       = os.environ["PROFIT_PASSWORD"]
    # DSN lido do .env.local (override=True garante valor correto)
    # Fallback garante senha certa independente de variável stale
    ts_dsn = os.environ.get("TIMESCALE_DSN", "")
    if not ts_dsn or ":finanalytics@" in ts_dsn:
        ts_dsn = "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
        log.info("timescale.dsn_default", dsn=ts_dsn)

    raw_tickers = os.environ.get("PROFIT_TICKERS", "PETR4:B")
    tickers = [
        (p[0].strip(), p[1].strip() if len(p) > 1 else "B")
        for item in raw_tickers.split(",")
        for p in [item.strip().split(":")]
    ]
    # Futuros primeiro: histórico maior e routing cicla após ~60s
    # Garante que WINFUT/futuros sejam coletados com sessão fresca
    futures = [t for t in tickers if t[1] == "F"]
    others  = [t for t in tickers if t[1] != "F"]
    tickers = futures + others
    if futures:
        log.info("history.order", order=[f"{t}:{e}" for t,e in tickers],
                 note="futuros primeiro para evitar timeout de sessão")

    fmt      = "%d/%m/%Y %H:%M:%S"
    dt_start = datetime.strptime(
        os.environ.get("HISTORY_DATE_START", "09/04/2026 09:00:00"), fmt)
    dt_end   = datetime.strptime(
        os.environ.get("HISTORY_DATE_END",   "10/04/2026 18:00:00"), fmt)
    resolutions = [
        r.strip()
        for r in os.environ.get("HISTORY_RESOLUTIONS", "1,5,15,60,D").split(",")
        if r.strip()
    ]
    timeout = float(os.environ.get("HISTORY_TIMEOUT", "120"))

    # ── Fase 1: conecta DLL ───────────────────────────────────────────────────
    dll.DLLInitializeLogin(
        c_wchar_p(activation_key), c_wchar_p(username), c_wchar_p(password),
        state_cb,
        None, None, None, None, None, None, None, None, None, None,
    )
    print("[OK] DLLInitializeLogin", flush=True)

    dll.SetHistoryTradeCallbackV2(history_cb)
    print("[OK] SetHistoryTradeCallbackV2", flush=True)

    # Aguarda routing
    for _ in range(60):
        if routing_done: break
        time.sleep(0.5)
    print(f"[OK] routing_done={routing_done}", flush=True)

    # Aguarda market data
    for i in range(120):
        if market_connected:
            print(f"[OK] market_connected em {i*0.5:.1f}s", flush=True)
            break
        time.sleep(0.5)

    if not market_connected:
        log.warning("market data nao conectou — tentando GetHistoryTrades mesmo assim (historico nao precisa de market feed)")
    log.info("dll.market_connected")

    # ── Fase 2+3: coleta e persiste imediatamente por ticker ─────────────────
    # Persiste cada ticker logo após coleta — PETR4/VALE3 ficam no DB
    # mesmo se WINFUT falhar ou for interrompido
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    for ticker, exchange in tickers:
        try:
            ticks = collect_history(ticker, exchange, dt_start, dt_end, timeout)
            log.info("history.collected", ticker=ticker, ticks=len(ticks))
        except (RuntimeError, TimeoutError) as e:
            log.error("history.error", ticker=ticker, error=str(e))
            time.sleep(1.0)
            continue

        # Persiste imediatamente após coleta
        try:
            asyncio.run(persist_all({ticker: ticks}, [(ticker, exchange)], resolutions, ts_dsn))
        except Exception as e:
            log.error("persist.error", ticker=ticker, error=str(e))
        time.sleep(1.0)

    dll.DLLFinalize()
    log.info("dll.finalized")


if __name__ == "__main__":
    main()

