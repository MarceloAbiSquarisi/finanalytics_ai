"""
profit_history_worker.py — v16
Baseado EXATAMENTE em scripts/test_history.py que recebeu 92.883 ticks.

Padrão correto:
  DLLInitializeLogin(state_cb, None x11)   ← sem V1 callbacks
  SetHistoryTradeCallbackV2(cb)            ← IMEDIATAMENTE após init
  aguarda routing (t=1 r>=4)
  aguarda market_connected (t=2 r=4)
  GetHistoryTrades(ticker, exchange, dt_start, dt_end)
  aguarda TC_LAST_PACKET
  persiste no TimescaleDB

Configuração via .env:
  PROFIT_DLL_PATH       = C:\\Nelogica\\profitdll.dll
  PROFIT_ACTIVATION_KEY = ...
  PROFIT_USERNAME       = ...
  PROFIT_PASSWORD       = ...
  PROFIT_TICKERS        = WINFUT:F   (pega só o ticker, exchange="B")
  HISTORY_DATE_START    = 01/04/2026 09:00:00
  HISTORY_DATE_END      = 11/04/2026 18:00:00
  HISTORY_TIMEOUT       = 300
  TIMESCALE_DSN         = postgresql://...
"""
from __future__ import annotations

import asyncio
import ctypes
import os
import sys
import threading
from ctypes import (
    POINTER,
    WINFUNCTYPE,
    Structure,
    byref,
    c_double,
    c_int,
    c_int64,
    c_size_t,
    c_ubyte,
    c_uint,
    c_ushort,
    c_wchar_p,
)
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

# ── env ──────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parents[4]  # finanalytics_ai_fresh/
for _env in (_ROOT / ".env", _ROOT / ".env.local"):
    if _env.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(_env, override=False)
        except ImportError:
            pass
        break

# ── logging ───────────────────────────────────────────────────────────────────
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ]
)
log = structlog.get_logger("profit_history_worker")

# ── constantes ────────────────────────────────────────────────────────────────
TC_LAST_PACKET = 0x02
TC_IS_EDIT     = 0x01
NL_OK          = 0


# ── estruturas C ─────────────────────────────────────────────────────────────
class SystemTime(Structure):
    _fields_ = [
        ("wYear",         c_ushort),
        ("wMonth",        c_ushort),
        ("wDayOfWeek",    c_ushort),
        ("wDay",          c_ushort),
        ("wHour",         c_ushort),
        ("wMinute",       c_ushort),
        ("wSecond",       c_ushort),
        ("wMilliseconds", c_ushort),
    ]


class TConnectorAssetIdentifier(Structure):
    _fields_ = [
        ("Version",  c_ubyte),
        ("Ticker",   c_wchar_p),
        ("Exchange", c_wchar_p),
        ("FeedType", c_ubyte),
    ]


class TConnectorTrade(Structure):
    _fields_ = [
        ("Version",     c_ubyte),
        ("TradeDate",   SystemTime),
        ("TradeNumber", c_uint),
        ("Price",       c_double),
        ("Quantity",    c_int64),
        ("Volume",      c_double),
        ("TradeType",   c_int),
        ("BuyAgent",    c_int),
        ("SellAgent",   c_int),
    ]


# ── dataclass resultado ───────────────────────────────────────────────────────
@dataclass
class HistoryTick:
    ticker:       str
    trade_date:   datetime
    trade_number: int
    price:        float
    quantity:     int
    volume:       float
    trade_type:   int
    buy_agent:    int
    sell_agent:   int


# ── worker ────────────────────────────────────────────────────────────────────
class ProfitHistoryWorker:
    """
    Worker de coleta de histórico de trades via ProfitDLL.
    Padrão EXATO de scripts/test_history.py (funcionou com 92k ticks).
    """

    def __init__(self) -> None:
        self.dll_path      = os.getenv("PROFIT_DLL_PATH", r"C:\Nelogica\profitdll.dll")
        self.act_key       = os.getenv("PROFIT_ACTIVATION_KEY", "")
        self.username      = os.getenv("PROFIT_USERNAME", "")
        self.password      = os.getenv("PROFIT_PASSWORD", "")
        self.timescale_dsn = os.getenv("TIMESCALE_DSN", "")
        self.timeout       = int(os.getenv("HISTORY_TIMEOUT", "300"))

        # Ticker: "WINFUT:F" → ticker="WINFUT", exchange="B"
        raw = os.getenv("PROFIT_TICKERS", "WINFUT:F").split(",")[0].strip()
        self.ticker   = raw.split(":")[0].strip()
        self.exchange = "B"

        self.dt_start = os.getenv("HISTORY_DATE_START", "01/04/2026 09:00:00")
        self.dt_end   = os.getenv("HISTORY_DATE_END",   "11/04/2026 18:00:00")

        # estado
        self._routing_done     = False
        self._market_connected = False
        self._ticks: list[HistoryTick] = []
        self._history_done = threading.Event()
        self._dll: Any = None

        # mantém referência para evitar GC dos callbacks
        self._state_cb_ref:   Any = None
        self._history_cb_ref: Any = None

    # ── setup DLL ─────────────────────────────────────────────────────────────
    def _load_dll(self) -> None:
        log.info("carregando DLL", path=self.dll_path)
        self._dll = ctypes.WinDLL(self.dll_path)
        self._dll.GetHistoryTrades.argtypes  = [c_wchar_p, c_wchar_p, c_wchar_p, c_wchar_p]
        self._dll.GetHistoryTrades.restype   = c_int
        self._dll.TranslateTrade.argtypes    = [c_size_t, POINTER(TConnectorTrade)]
        self._dll.TranslateTrade.restype     = c_int
        self._dll.SetHistoryTradeCallbackV2.restype = None
        self._dll.DLLInitializeLogin.restype  = c_int
        self._dll.DLLFinalize.restype         = None

    # ── callbacks ─────────────────────────────────────────────────────────────
    def _make_state_cb(self) -> Any:
        @WINFUNCTYPE(None, c_int, c_int)
        def state_cb(t: int, r: int) -> None:
            log.debug("state_cb", t=t, r=r)
            if t == 1 and r >= 4:
                self._routing_done = True
                log.info("routing OK", t=t, r=r)
            if t == 2 and r == 4:
                self._market_connected = True
                log.info("market_connected", t=t, r=r)
            if t == 2 and r == 1:
                log.warning("market_data login inválido (t=2 r=1)", t=t, r=r)
        return state_cb

    def _make_history_cb(self) -> Any:
        dll        = self._dll
        ticks      = self._ticks
        done_event = self._history_done

        @WINFUNCTYPE(None, TConnectorAssetIdentifier, c_size_t, c_uint)
        def history_cb(
            asset_id: TConnectorAssetIdentifier,
            p_trade:  int,
            flags:    int,
        ) -> None:
            is_last = bool(flags & TC_LAST_PACKET)
            if not bool(flags & TC_IS_EDIT) and p_trade:
                trade = TConnectorTrade(Version=0)
                if dll.TranslateTrade(p_trade, byref(trade)) == NL_OK and trade.Price > 0:
                    st = trade.TradeDate
                    try:
                        trade_date = datetime(
                            st.wYear, st.wMonth, st.wDay,
                            st.wHour, st.wMinute, st.wSecond,
                        )
                    except ValueError:
                        trade_date = datetime(2000, 1, 1)

                    ticks.append(HistoryTick(
                        ticker=asset_id.Ticker or "",
                        trade_date=trade_date,
                        trade_number=int(trade.TradeNumber),
                        price=trade.Price / 100.0,
                        quantity=int(trade.Quantity),
                        volume=trade.Volume / 100.0,
                        trade_type=int(trade.TradeType),
                        buy_agent=int(trade.BuyAgent),
                        sell_agent=int(trade.SellAgent),
                    ))
                    if len(ticks) % 1000 == 0:
                        log.info("ticks acumulados", count=len(ticks))

            if is_last:
                log.info("TC_LAST_PACKET", total=len(ticks))
                done_event.set()

        return history_cb

    # ── persistência TimescaleDB ──────────────────────────────────────────────
    async def _persist(self, ticks: list[HistoryTick]) -> int:
        if not self.timescale_dsn:
            log.warning("TIMESCALE_DSN não configurado — pulando persistência")
            return 0
        if not ticks:
            return 0
        try:
            import asyncpg  # type: ignore
        except ImportError:
            log.error("asyncpg não instalado — pip install asyncpg")
            return 0

        CREATE = """
        CREATE TABLE IF NOT EXISTS market_history_trades (
            ticker        TEXT             NOT NULL,
            trade_date    TIMESTAMPTZ      NOT NULL,
            trade_number  BIGINT           NOT NULL,
            price         DOUBLE PRECISION NOT NULL,
            quantity      BIGINT           NOT NULL,
            volume        DOUBLE PRECISION NOT NULL,
            trade_type    INT              NOT NULL,
            buy_agent     INT              NOT NULL,
            sell_agent    INT              NOT NULL,
            PRIMARY KEY (ticker, trade_date, trade_number)
        );
        """
        UPSERT = """
        INSERT INTO market_history_trades
            (ticker, trade_date, trade_number, price, quantity, volume,
             trade_type, buy_agent, sell_agent)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        ON CONFLICT (ticker, trade_date, trade_number) DO NOTHING;
        """
        conn = await asyncpg.connect(self.timescale_dsn)
        try:
            await conn.execute(CREATE)
            rows = [
                (t.ticker, t.trade_date, t.trade_number,
                 t.price, t.quantity, t.volume,
                 t.trade_type, t.buy_agent, t.sell_agent)
                for t in ticks
            ]
            await conn.executemany(UPSERT, rows)
            log.info("ticks persistidos", count=len(rows))
            return len(rows)
        finally:
            await conn.close()

    # ── run ───────────────────────────────────────────────────────────────────
    async def run(self) -> None:
        log.info(
            "worker iniciado",
            ticker=self.ticker, exchange=self.exchange,
            dt_start=self.dt_start, dt_end=self.dt_end,
            timeout_s=self.timeout,
        )

        self._load_dll()

        # Cria callbacks e guarda referência (evita GC)
        self._state_cb_ref   = self._make_state_cb()
        self._history_cb_ref = self._make_history_cb()

        # ── 1. DLLInitializeLogin ─────────────────────────────────────────────
        # CRÍTICO: apenas state_cb real, todos os outros = None.
        # V1 callbacks (trade_v1, daily_v1) NÃO devem ser passados aqui —
        # ao contrário do profit_agent.py, eles bloqueiam t=2 r=4 neste contexto.
        ret = self._dll.DLLInitializeLogin(
            c_wchar_p(self.act_key),
            c_wchar_p(self.username),
            c_wchar_p(self.password),
            self._state_cb_ref,  # pos 4: state_cb
            None,                # pos 5: history_cb (não usar aqui)
            None,                # pos 6: order_change_cb
            None,                # pos 7: account_cb
            None,                # pos 8: trade_v1   ← None (crítico)
            None,                # pos 9: daily_v1   ← None (crítico)
            None,                # pos 10: price_book
            None,                # pos 11: offer_book
            None,                # pos 12: history_trade_cb
            None,                # pos 13: progress
            None,                # pos 14: tiny
        )
        log.info("DLLInitializeLogin", ret=ret)

        # ── 2. SetHistoryTradeCallbackV2 IMEDIATAMENTE ────────────────────────
        # Padrão Delphi / test_history.py: registra antes de aguardar conexão.
        self._dll.SetHistoryTradeCallbackV2(self._history_cb_ref)
        log.info("SetHistoryTradeCallbackV2 registrado")

        # ── 3. Aguarda routing (t=1 r>=4) ─────────────────────────────────────
        log.info("aguardando routing (t=1 r>=4)...")
        for _ in range(120):   # 60s
            if self._routing_done:
                break
            await asyncio.sleep(0.5)
        if not self._routing_done:
            log.error("TIMEOUT aguardando routing")
            self._dll.DLLFinalize()
            return

        # ── 4. Aguarda market data (t=2 r=4) ─────────────────────────────────
        log.info("aguardando market_connected (t=2 r=4)...")
        for i in range(120):   # 60s
            if self._market_connected:
                log.info("market_connected", after_s=f"{i * 0.5:.1f}")
                break
            await asyncio.sleep(0.5)
        if not self._market_connected:
            log.error(
                "TIMEOUT aguardando market data — "
                "verifique se outro processo está usando as credenciais"
            )
            self._dll.DLLFinalize()
            return

        # ── 5. GetHistoryTrades ───────────────────────────────────────────────
        log.info(
            "chamando GetHistoryTrades",
            ticker=self.ticker, exchange=self.exchange,
            dt_start=self.dt_start, dt_end=self.dt_end,
        )
        ret = self._dll.GetHistoryTrades(
            c_wchar_p(self.ticker),
            c_wchar_p(self.exchange),
            c_wchar_p(self.dt_start),
            c_wchar_p(self.dt_end),
        )
        log.info("GetHistoryTrades disparado", ret=ret)

        # ── 6. Aguarda TC_LAST_PACKET ─────────────────────────────────────────
        log.info("aguardando TC_LAST_PACKET...", timeout_s=self.timeout)
        loop = asyncio.get_event_loop()
        received = await loop.run_in_executor(
            None,
            lambda: self._history_done.wait(timeout=self.timeout),
        )

        if received:
            log.info("✓ coleta concluída", ticks=len(self._ticks))
            if self._ticks:
                log.info("primeiro", tick=self._ticks[0])
                log.info("último",   tick=self._ticks[-1])
        else:
            log.warning("✗ TIMEOUT — coleta parcial", ticks=len(self._ticks))

        # ── 7. Persistência ───────────────────────────────────────────────────
        if self._ticks:
            inserted = await self._persist(self._ticks)
            log.info("persistência", inserted=inserted)
        else:
            log.warning("nenhum tick para persistir")

        # ── 8. Finaliza ───────────────────────────────────────────────────────
        self._dll.DLLFinalize()
        log.info("DLLFinalize OK — worker encerrado")


# ── entrypoint ────────────────────────────────────────────────────────────────
async def main() -> None:
    worker = ProfitHistoryWorker()
    await worker.run()


if __name__ == "__main__":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
