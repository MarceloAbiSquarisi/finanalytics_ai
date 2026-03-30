
"""
profit_agent.py — Agente standalone ProfitDLL (Nelogica)

Arquitetura intencional: ZERO imports do projeto finanalytics_ai.
Usa apenas stdlib + psycopg2 + python-dotenv para garantir
que nada inicializa Winsock antes da DLL conectar.

Funcionalidades:
  - Conecta via DLLInitializeLogin (Market Data + Roteamento)
  - Baixa catalogo completo de ativos → profit_assets
  - Recebe ticks em tempo real → profit_ticks (TimescaleDB)
  - Recebe candles diarios → profit_daily_bars
  - Book de precos → profit_order_book
  - Ajustes corporativos → profit_adjustments
  - HTTP local :8001 para envio de ordens e consultas
  - Suporte a conta de simulacao e producao
  - Todos os tipos de ordem: Limite, Mercado, Stop, Zerar, Alterar, Cancelar

Configuracao no .env:
  PROFIT_DLL_PATH=C:\\Nelogica\\ProfitDLL.dll
  PROFIT_ACTIVATION_KEY=...
  PROFIT_USERNAME=...
  PROFIT_PASSWORD=...          # senha de login (Market Data)

  # Conta de simulacao
  PROFIT_SIM_BROKER_ID=...
  PROFIT_SIM_ACCOUNT_ID=...
  PROFIT_SIM_ROUTING_PASSWORD=...

  # Conta de producao
  PROFIT_PROD_BROKER_ID=...
  PROFIT_PROD_ACCOUNT_ID=...
  PROFIT_PROD_ROUTING_PASSWORD=...

  # TimescaleDB
  PROFIT_TIMESCALE_DSN=postgresql://finanalytics:timescale_secret@localhost:5433/market_data

  # Agente
  PROFIT_AGENT_PORT=8001
  PROFIT_SUBSCRIBE_TICKERS=PETR4,VALE3,WINFUT   # vazio = so catalogo, sem ticks
  PROFIT_LOG_FILE=D:\\Projetos\\finanalytics_ai_fresh\\logs\\profit_agent.log
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import queue
import signal
import struct
import sys
import threading
import time
from ctypes import (
    POINTER, WINFUNCTYPE, WinDLL,
    c_bool, c_char, c_double, c_int, c_int64, c_long,
    c_longlong, c_size_t, c_ubyte, c_uint, c_ushort,
    c_void_p, c_wchar, c_wchar_p,
    Structure, byref, create_unicode_buffer,
)
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Dotenv (sem pydantic-settings — stdlib pura para nao ativar Winsock)
# ---------------------------------------------------------------------------
def _load_env(path: str) -> None:
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k not in os.environ:
                        os.environ[k] = v
    except FileNotFoundError:
        pass

# ---------------------------------------------------------------------------
# Logging configurado para arquivo antes de qualquer import
# ---------------------------------------------------------------------------
def _setup_logging() -> None:
    log_file = os.getenv("PROFIT_LOG_FILE",
        r"D:\Projetos\finanalytics_ai_fresh\logs\profit_agent.log")
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

log = logging.getLogger("profit_agent")

# ---------------------------------------------------------------------------
# Tipos ctypes (manual Nelogica)
# ---------------------------------------------------------------------------

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

class TAssetID(Structure):
    _fields_ = [
        ("ticker", c_wchar_p),
        ("bolsa",  c_wchar_p),
        ("feed",   c_int),
    ]

class TConnectorAccountIdentifier(Structure):
    _fields_ = [
        ("Version",      c_ubyte),
        ("BrokerID",     c_int),
        ("AccountID",    c_wchar_p),
        ("SubAccountID", c_wchar_p),
        ("Reserved",     c_int64),
    ]

class TConnectorAccountIdentifierOut(Structure):
    _fields_ = [
        ("Version",           c_ubyte),
        ("BrokerID",          c_int),
        ("AccountID",         c_wchar * 100),
        ("AccountIDLength",   c_int),
        ("SubAccountID",      c_wchar * 100),
        ("SubAccountIDLength",c_int),
        ("Reserved",          c_int64),
    ]

class TConnectorAssetIdentifier(Structure):
    _fields_ = [
        ("Version",  c_ubyte),
        ("Ticker",   c_wchar_p),
        ("Exchange", c_wchar_p),
        ("FeedType", c_ubyte),
    ]

class TConnectorOrderIdentifier(Structure):
    _fields_ = [
        ("Version",      c_ubyte),
        ("LocalOrderID", c_int64),
        ("ClOrderID",    c_wchar_p),
    ]

class TConnectorSendOrder(Structure):
    _fields_ = [
        ("Version",   c_ubyte),
        ("AccountID", TConnectorAccountIdentifier),
        ("AssetID",   TConnectorAssetIdentifier),
        ("Password",  c_wchar_p),
        ("OrderType", c_ubyte),
        ("OrderSide", c_ubyte),
        ("Price",     c_double),
        ("StopPrice", c_double),
        ("Quantity",  c_int64),
        ("MessageID", c_int64),
    ]

class TConnectorChangeOrder(Structure):
    _fields_ = [
        ("Version",   c_ubyte),
        ("AccountID", TConnectorAccountIdentifier),
        ("OrderID",   TConnectorOrderIdentifier),
        ("Password",  c_wchar_p),
        ("Price",     c_double),
        ("StopPrice", c_double),
        ("Quantity",  c_int64),
        ("MessageID", c_int64),
    ]

class TConnectorCancelOrder(Structure):
    _fields_ = [
        ("Version",   c_ubyte),
        ("AccountID", TConnectorAccountIdentifier),
        ("OrderID",   TConnectorOrderIdentifier),
        ("Password",  c_wchar_p),
        ("MessageID", c_int64),
    ]

class TConnectorCancelAllOrders(Structure):
    _fields_ = [
        ("Version",   c_ubyte),
        ("AccountID", TConnectorAccountIdentifier),
        ("Password",  c_wchar_p),
    ]

class TConnectorZeroPosition(Structure):
    _fields_ = [
        ("Version",       c_ubyte),
        ("AccountID",     TConnectorAccountIdentifier),
        ("AssetID",       TConnectorAssetIdentifier),
        ("Password",      c_wchar_p),
        ("Price",         c_double),
        ("PositionType",  c_ubyte),
        ("MessageID",     c_int64),
    ]

class TConnectorTrade(Structure):
    _fields_ = [
        ("Version",     c_ubyte),
        ("TradeDate",   SystemTime),
        ("TradeNumber", c_uint),
        ("Price",       c_double),
        ("Quantity",    c_longlong),
        ("Volume",      c_double),
        ("BuyAgent",    c_int),
        ("SellAgent",   c_int),
        ("TradeType",   c_ubyte),
    ]

class TConnectorPriceGroup(Structure):
    _fields_ = [
        ("Version",         c_ubyte),
        ("Price",           c_double),
        ("Count",           c_uint),
        ("Quantity",        c_long),    # c_long = 32-bit no Windows (conforme manual Nelogica)
        ("PriceGroupFlags", c_uint),
    ]

# ---------------------------------------------------------------------------
# Constantes (manual pag. 13)
# ---------------------------------------------------------------------------
CONN_STATE_LOGIN        = 0
CONN_STATE_ROUTING      = 1
CONN_STATE_MARKET_DATA  = 2
CONN_STATE_MARKET_LOGIN = 3

LOGIN_CONNECTED         = 0
MARKET_CONNECTED        = 4
ACTIVATE_VALID          = 0
ROUTING_BROKER_CONNECTED = 5

ORDER_TYPE_MARKET       = 1
ORDER_TYPE_LIMIT        = 2
ORDER_TYPE_STOP_LIMIT   = 4

ORDER_SIDE_BUY          = 1
ORDER_SIDE_SELL         = 2

POSITION_TYPE_DAYTRADE    = 1
POSITION_TYPE_CONSOLIDATED = 2

PG_IS_THEORIC = 1

# ---------------------------------------------------------------------------
# DB Writer (psycopg2 sincrono — importado APOS DLL conectar)
# ---------------------------------------------------------------------------
class DBWriter:
    """Escreve no TimescaleDB usando psycopg2 sincrono."""

    def __init__(self, dsn: str) -> None:
        self._dsn   = dsn
        self._conn  = None
        self._lock  = threading.Lock()

    def connect(self) -> bool:
        try:
            import psycopg2  # type: ignore
            self._conn = psycopg2.connect(self._dsn)
            self._conn.autocommit = True
            log.info("db.connected dsn=%s", self._dsn.split("@")[-1])
            return True
        except Exception as e:
            log.error("db.connect_failed error=%s", e)
            return False

    def execute(self, sql: str, params: tuple = ()) -> bool:
        if self._conn is None:
            return False
        try:
            with self._lock:
                cur = self._conn.cursor()
                cur.execute(sql, params)
                cur.close()
            return True
        except Exception as e:
            log.warning("db.execute_failed error=%s sql=%.100s", e, sql)
            try:
                self._conn.rollback()
            except Exception:
                pass
            return False

    def upsert_asset(self, data: dict) -> None:
        sql = """
        INSERT INTO profit_assets
            (ticker, exchange, name, description, security_type, security_subtype,
             min_order_qty, max_order_qty, lot_size, min_price_increment,
             contract_multiplier, valid_date, isin, sector, sub_sector, segment, feed_type, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
        ON CONFLICT(ticker, exchange) DO UPDATE SET
            name=EXCLUDED.name, description=EXCLUDED.description,
            security_type=EXCLUDED.security_type, security_subtype=EXCLUDED.security_subtype,
            min_order_qty=EXCLUDED.min_order_qty, max_order_qty=EXCLUDED.max_order_qty,
            lot_size=EXCLUDED.lot_size, min_price_increment=EXCLUDED.min_price_increment,
            contract_multiplier=EXCLUDED.contract_multiplier,
            valid_date=EXCLUDED.valid_date, isin=EXCLUDED.isin,
            sector=EXCLUDED.sector, sub_sector=EXCLUDED.sub_sector,
            segment=EXCLUDED.segment, updated_at=NOW()
        """
        self.execute(sql, (
            data.get("ticker",""), data.get("exchange","B"),
            data.get("name"), data.get("description"),
            data.get("security_type"), data.get("security_subtype"),
            data.get("min_order_qty"), data.get("max_order_qty"),
            data.get("lot_size"), data.get("min_price_increment"),
            data.get("contract_multiplier"), data.get("valid_date"),
            data.get("isin"), data.get("sector"),
            data.get("sub_sector"), data.get("segment"),
            data.get("feed_type", 0),
        ))

    def insert_tick(self, data: dict) -> None:
        sql = """
        INSERT INTO profit_ticks
            (time, ticker, exchange, price, quantity, volume,
             buy_agent, sell_agent, trade_number, trade_type, is_edit)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """
        self.execute(sql, (
            data["time"], data["ticker"], data.get("exchange","B"),
            data["price"], data["quantity"], data.get("volume"),
            data.get("buy_agent"), data.get("sell_agent"),
            data.get("trade_number"), data.get("trade_type"), data.get("is_edit", False),
        ))

    def upsert_daily_bar(self, data: dict) -> None:
        sql = """
        INSERT INTO profit_daily_bars
            (time, ticker, exchange, open, high, low, close, volume, adjust,
             max_limit, min_limit, vol_buyer, vol_seller, qty, trades,
             open_contracts, qty_buyer, qty_seller, neg_buyer, neg_seller)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(time, ticker, exchange) DO UPDATE SET
            open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, close=EXCLUDED.close,
            volume=EXCLUDED.volume, adjust=EXCLUDED.adjust
        """
        self.execute(sql, (
            data["time"], data["ticker"], data.get("exchange","B"),
            data.get("open"), data.get("high"), data.get("low"), data.get("close"),
            data.get("volume"), data.get("adjust"), data.get("max_limit"), data.get("min_limit"),
            data.get("vol_buyer"), data.get("vol_seller"), data.get("qty"), data.get("trades"),
            data.get("open_contracts"), data.get("qty_buyer"), data.get("qty_seller"),
            data.get("neg_buyer"), data.get("neg_seller"),
        ))

    def insert_order(self, data: dict) -> None:
        sql = """
        INSERT INTO profit_orders
            (local_order_id, message_id, broker_id, account_id, sub_account_id,
             env, ticker, exchange, order_type, order_side, price, stop_price,
             quantity, order_status)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,10)
        """
        self.execute(sql, (
            data.get("local_order_id"), data.get("message_id"),
            data["broker_id"], data["account_id"], data.get("sub_account_id"),
            data.get("env","simulation"), data["ticker"], data.get("exchange","B"),
            data["order_type"], data["order_side"],
            data.get("price"), data.get("stop_price"), data["quantity"],
        ))

    def update_agent_status(self, data: dict) -> None:
        sql = """
        UPDATE profit_agent_status SET
            last_heartbeat=%s, is_connected=%s,
            market_connected=%s, routing_connected=%s,
            subscribed_tickers=%s, total_ticks=%s, total_orders=%s
        WHERE id=1
        """
        self.execute(sql, (
            datetime.now(tz=timezone.utc),
            data.get("is_connected", False),
            data.get("market_connected", False),
            data.get("routing_connected", False),
            data.get("subscribed_tickers", []),
            data.get("total_ticks", 0),
            data.get("total_orders", 0),
        ))

    def ensure_tickers_table(self) -> None:
        """Cria tabela de tickers se nao existir."""
        sql = """
        CREATE TABLE IF NOT EXISTS profit_subscribed_tickers (
            ticker   VARCHAR(20)  NOT NULL,
            exchange VARCHAR(10)  NOT NULL DEFAULT 'B',
            active   BOOLEAN      NOT NULL DEFAULT TRUE,
            added_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            notes    TEXT,
            PRIMARY KEY (ticker, exchange)
        )
        """
        self.execute(sql)

    def get_subscribed_tickers(self) -> list:
        """Retorna lista de (ticker, exchange) ativos."""
        if self._conn is None:
            return []
        try:
            with self._lock:
                cur = self._conn.cursor()
                cur.execute(
                    "SELECT ticker, exchange FROM profit_subscribed_tickers"
                    " WHERE active = TRUE ORDER BY ticker"
                )
                rows = cur.fetchall()
                cur.close()
            return [(r[0], r[1]) for r in rows]
        except Exception as e:
            log.warning("db.get_tickers_failed error=%s", e)
            return []

    def add_ticker(self, ticker: str, exchange: str = "B", notes: str = "") -> bool:
        """Insere ou reativa um ticker."""
        sql = """
        INSERT INTO profit_subscribed_tickers (ticker, exchange, active, notes)
        VALUES (%s, %s, TRUE, %s)
        ON CONFLICT (ticker, exchange) DO UPDATE SET active = TRUE, notes = EXCLUDED.notes
        """
        return self.execute(sql, (ticker.upper(), exchange.upper(), notes))

    def remove_ticker(self, ticker: str, exchange: str = "B") -> bool:
        """Desativa um ticker (soft delete)."""
        sql = """
        UPDATE profit_subscribed_tickers SET active = FALSE
        WHERE ticker = %s AND exchange = %s
        """
        return self.execute(sql, (ticker.upper(), exchange.upper()))

    def seed_tickers_from_env(self, tickers: list) -> None:
        """Popula tabela com tickers do .env apenas se estiver vazia."""
        if self._conn is None or not tickers:
            return
        try:
            with self._lock:
                cur = self._conn.cursor()
                cur.execute("SELECT COUNT(*) FROM profit_subscribed_tickers")
                count = cur.fetchone()[0]
                cur.close()
            if count == 0:
                for t in tickers:
                    self.add_ticker(t)
                log.info("db.tickers_seeded_from_env count=%d", len(tickers))
        except Exception as e:
            log.warning("db.seed_tickers_failed error=%s", e)


# ---------------------------------------------------------------------------
# ProfitAgent
# ---------------------------------------------------------------------------
class ProfitAgent:
    """Agente principal — gerencia DLL, callbacks e fila de DB."""

    def __init__(self) -> None:
        self._dll: Optional[WinDLL] = None
        self._db:  Optional[DBWriter] = None
        self._db_queue: queue.Queue = queue.Queue(maxsize=50_000)

        # Estado de conexao
        self._market_connected   = threading.Event()
        self._routing_connected  = threading.Event()
        self._state_lock         = threading.Lock()
        self._login_ok     = False
        self._market_ok    = False
        self._routing_ok   = False
        self._activate_ok  = False

        # Contadores
        self._total_ticks  = 0
        self._total_orders = 0
        self._total_assets = 0

        # Tickers subscritos
        self._subscribed: set[str] = set()

        # Contas descobertas via accountCallback: {broker_name: (broker_id, account_id)}
        self._discovered_accounts: dict = {}
        self._stop_event = threading.Event()

        # Refs de callbacks (evita GC)
        self._callbacks: list = []

        # Config
        self._dll_path    = os.getenv("PROFIT_DLL_PATH",   r"C:\Nelogica\ProfitDLL.dll")
        self._act_key     = os.getenv("PROFIT_ACTIVATION_KEY", "")
        self._username    = os.getenv("PROFIT_USERNAME", "")
        self._password    = os.getenv("PROFIT_PASSWORD", "")
        self._ts_dsn      = os.getenv("PROFIT_TIMESCALE_DSN",
            "postgresql://finanalytics:timescale_secret@localhost:5433/market_data")

        _sim_bid = os.getenv("PROFIT_SIM_BROKER_ID", "0") or "0"
        self._sim_broker  = int(_sim_bid) if _sim_bid.lstrip("-").isdigit() else 0
        self._sim_broker_str = _sim_bid
        self._sim_account = os.getenv("PROFIT_SIM_ACCOUNT_ID", "")
        self._sim_pass    = os.getenv("PROFIT_SIM_ROUTING_PASSWORD", "")

        _prod_bid = os.getenv("PROFIT_PROD_BROKER_ID", "0") or "0"
        self._prod_broker  = int(_prod_bid) if _prod_bid.lstrip("-").isdigit() else 0
        self._prod_broker_str = _prod_bid
        self._prod_account = os.getenv("PROFIT_PROD_ACCOUNT_ID", "")
        self._prod_pass    = os.getenv("PROFIT_PROD_ROUTING_PASSWORD", "")

        raw_tickers = os.getenv("PROFIT_SUBSCRIBE_TICKERS", "")
        self._subscribe_tickers = [
            t.strip().upper() for t in raw_tickers.split(",") if t.strip()
        ]

    # ------------------------------------------------------------------
    # Inicializacao
    # ------------------------------------------------------------------
    def start(self) -> None:
        log.info("profit_agent.starting version=1.0.0")

        # 1. Carrega DLL (antes de qualquer outra coisa)
        log.info("profit_agent.loading_dll path=%s", self._dll_path)
        self._dll = WinDLL(self._dll_path)
        self._setup_dll_restypes()

        # 2. Registra callbacks
        self._register_callbacks()

        # 3. Inicializa com DLLInitializeLogin (Market Data + Roteamento)
        log.info("profit_agent.initializing")
        ret = self._dll.DLLInitializeLogin(
            c_wchar_p(self._act_key),
            c_wchar_p(self._username),
            c_wchar_p(self._password),
            self._callbacks[0],   # state
            None,                  # history
            None,                  # order_change
            self._callbacks[1],   # account
            self._callbacks[2],   # new_trade (V1 compat)
            self._callbacks[3],   # new_daily
            None,                  # price_book
            None,                  # offer_book
            None,                  # history_trade
            self._callbacks[4],   # progress
            self._callbacks[5],   # tiny_book
        )
        if ret != 0:
            log.error("profit_agent.dll_init_failed ret=%d", ret)
            sys.exit(1)

        # Registra callbacks opcionais via Set* (pos-init, conforme exemplo Nelogica)
        self._dll.SetTradeCallbackV2(self._callbacks[6])
        self._dll.SetDailyCallback(self._callbacks[3])
        self._dll.SetAssetListInfoCallbackV2(self._callbacks[7])
        self._dll.SetAssetListCallback(self._callbacks[8])
        self._dll.SetAdjustHistoryCallbackV2(self._callbacks[9])
        self._dll.SetPriceDepthCallback(self._callbacks[10])
        self._dll.SetOrderCallback(self._callbacks[11])
        self._dll.SetTradingMessageResultCallback(self._callbacks[12])
        self._dll.SetBrokerAccountListChangedCallback(self._callbacks[13])

        log.info("profit_agent.dll_initialized")

        # 4. Aguarda conexao (threading.Event — sem asyncio)
        log.info("profit_agent.waiting_connection timeout=60s")
        connected = self._market_connected.wait(timeout=60.0)
        if not connected:
            log.warning("profit_agent.market_timeout continuing_anyway")

        # 5. Inicia DB (APOS DLL conectar)
        log.info("profit_agent.connecting_db")
        self._db = DBWriter(self._ts_dsn)
        if self._db.connect():
            self._db.execute(
                "UPDATE profit_agent_status SET started_at=%s, version=%s WHERE id=1",
                (datetime.now(tz=timezone.utc), "1.0.0"),
            )
            # Garante tabela e migra tickers do .env (apenas se tabela vazia)
            self._db.ensure_tickers_table()
            self._db.seed_tickers_from_env(self._subscribe_tickers)
        else:
            log.warning("profit_agent.db_unavailable continuing_without_persistence")
            self._db = None

        # 6. Inicia worker de DB em thread separada
        db_thread = threading.Thread(target=self._db_worker, daemon=True)
        db_thread.start()

        # 7. Verifica contagem de contas (catalogo chega via SetAssetListInfoCallbackV2)
        # GetAccount() sem args nao existe na DLL — removido (BUG 7)
        try:
            n_accounts = self._dll.GetAccountCount()
            log.info("profit_agent.account_count n=%d", n_accounts)
        except Exception as e:
            log.warning("profit_agent.get_account_count_error e=%s", e)

        # 8. Subscreve tickers - le do banco; fallback para .env se DB indisponivel
        if self._db:
            tickers_to_subscribe = self._db.get_subscribed_tickers()
            log.info("profit_agent.subscribing_from_db count=%d", len(tickers_to_subscribe))
        else:
            tickers_to_subscribe = [(t, "B") for t in self._subscribe_tickers]
            log.info("profit_agent.subscribing_from_env count=%d", len(tickers_to_subscribe))
        for ticker, exchange in tickers_to_subscribe:
            self._subscribe(ticker, exchange)

        # 9. Inicia HTTP server em thread separada
        http_port = int(os.getenv("PROFIT_AGENT_PORT", "8001"))
        http_thread = threading.Thread(
            target=self._start_http, args=(http_port,), daemon=True
        )
        http_thread.start()
        log.info("profit_agent.http_started port=%d", http_port)

        # 10. Heartbeat loop (main thread)
        self._heartbeat_loop()

    def _subscribe(self, ticker: str, exchange: str = "B") -> None:
        key = f"{ticker}:{exchange}"
        if key in self._subscribed:
            return
        ret_t = self._dll.SubscribeTicker(c_wchar_p(ticker), c_wchar_p(exchange))

        # Subscribe no depth para book de precos
        asset_id = TConnectorAssetIdentifier(
            Version=0, Ticker=ticker, Exchange=exchange, FeedType=0
        )
        ret_d = self._dll.SubscribePriceDepth(byref(asset_id))

        if ret_t == 0:
            self._subscribed.add(key)
            log.info("profit_agent.subscribed ticker=%s exchange=%s", ticker, exchange)
        else:
            log.warning("profit_agent.subscribe_failed ticker=%s ret=%d", ticker, ret_t)

    # ------------------------------------------------------------------
    # Configuracao de restypes
    # ------------------------------------------------------------------
    def _setup_dll_restypes(self) -> None:
        dll = self._dll

        # ── Inicializacao ─────────────────────────────────────────────────
        # SEM argtypes — ctypes faz marshal nativo de WINFUNCTYPE corretamente
        # c_void_p como argtype impede extracao do thunk address
        dll.DLLInitializeLogin.restype = c_int
        dll.DLLInitializeMarketLogin.restype = c_int

        dll.DLLFinalize.argtypes = []
        dll.DLLFinalize.restype  = c_int

        # ── Market data — subscricoes ──────────────────────────────────────
        dll.SubscribeTicker.argtypes   = [c_wchar_p, c_wchar_p]
        dll.SubscribeTicker.restype    = c_int
        dll.UnsubscribeTicker.argtypes = [c_wchar_p, c_wchar_p]
        dll.UnsubscribeTicker.restype  = c_int

        dll.SubscribePriceDepth.argtypes = [POINTER(TConnectorAssetIdentifier)]
        dll.SubscribePriceDepth.restype  = c_int

        dll.GetAccountCount.argtypes = []
        dll.GetAccountCount.restype  = c_int

        dll.GetPriceDepthSideCount.argtypes = [POINTER(TConnectorAssetIdentifier), c_ubyte]
        dll.GetPriceDepthSideCount.restype  = c_int

        dll.GetPriceGroup.argtypes = [
            POINTER(TConnectorAssetIdentifier), c_ubyte, c_int,
            POINTER(TConnectorPriceGroup),
        ]
        dll.GetPriceGroup.restype = c_int

        dll.GetTheoreticalValues.argtypes = [
            POINTER(TConnectorAssetIdentifier),
            POINTER(c_double), POINTER(c_int64),
        ]
        dll.GetTheoreticalValues.restype = c_int

        dll.TranslateTrade.argtypes = [c_size_t, POINTER(TConnectorTrade)]
        dll.TranslateTrade.restype  = c_int

        # ── Set*Callback — CRITICO: sem argtypes a DLL recebe ponteiro errado ──
        for _fn in (
            "SetTradeCallbackV2",
            "SetDailyCallback",
            "SetAssetListInfoCallbackV2",
            "SetAssetListCallback",
            "SetAdjustHistoryCallbackV2",
            "SetPriceDepthCallback",
            "SetOrderCallback",
            "SetTradingMessageResultCallback",
            "SetBrokerAccountListChangedCallback",
        ):
            getattr(dll, _fn).restype = None

        # ── Roteamento ────────────────────────────────────────────────────
        dll.SendOrder.argtypes         = [POINTER(TConnectorSendOrder)]
        dll.SendOrder.restype          = c_int64
        dll.SendChangeOrderV2.argtypes = [POINTER(TConnectorChangeOrder)]
        dll.SendChangeOrderV2.restype  = c_int
        dll.SendCancelOrderV2.argtypes = [POINTER(TConnectorCancelOrder)]
        dll.SendCancelOrderV2.restype  = c_int
        dll.SendCancelAllOrdersV2.argtypes = [POINTER(TConnectorCancelAllOrders)]
        dll.SendCancelAllOrdersV2.restype  = c_int
        dll.SendZeroPositionV2.argtypes = [POINTER(TConnectorZeroPosition)]
        dll.SendZeroPositionV2.restype  = c_int64

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------
    def _register_callbacks(self) -> None:
        agent = self

        # 0. State callback — MINIMAL (nenhum I/O)
        @WINFUNCTYPE(None, c_int, c_int)
        def state_cb(conn_type: int, result: int) -> None:
            with agent._state_lock:
                if conn_type == CONN_STATE_LOGIN:
                    agent._login_ok = (result == LOGIN_CONNECTED)
                elif conn_type == CONN_STATE_MARKET_DATA:
                    agent._market_ok = (result == MARKET_CONNECTED)
                    if result == MARKET_CONNECTED:
                        agent._market_connected.set()
                elif conn_type == CONN_STATE_ROUTING:
                    # result==5 = broker conectado (ROUTING_BROKER_CONNECTED)
                    # result==2 = "sem conexao com servidores" (NAO conectado)
                    # result >2 = "sem conexao com corretora"
                    agent._routing_ok = (result == ROUTING_BROKER_CONNECTED)
                    if result == ROUTING_BROKER_CONNECTED:
                        agent._routing_connected.set()
                elif conn_type == CONN_STATE_MARKET_LOGIN:
                    agent._activate_ok = (result == ACTIVATE_VALID)
            agent._db_queue.put_nowait({
                "_type": "state",
                "conn_type": conn_type, "result": result,
            })

        # 1. Account callback
        @WINFUNCTYPE(None, c_int, c_wchar_p, c_wchar_p, c_wchar_p)
        def account_cb(broker_id, broker_name, account_id, owner_name) -> None:
            name = (broker_name or "").upper()
            acc  = (account_id or "").strip()
            log.info("account broker_id=%d broker_name=%s account=%s owner=%s",
                     broker_id, name, acc, owner_name)
            # Guarda pelo nome da corretora E pelo account_id
            agent._discovered_accounts[name] = (broker_id, acc)
            agent._discovered_accounts[acc]  = (broker_id, acc)

        # 2. Trade callback V1 (passado no init como NewTradeCallback)
        @WINFUNCTYPE(None, TAssetID, c_wchar_p, c_uint, c_double, c_double,
                     c_int, c_int, c_int, c_int, c_int)
        def new_trade_cb(asset_id, date, trade_num, price, vol, qty,
                         buy_agent, sell_agent, trade_type, is_edit) -> None:
            pass  # usa TradeCallbackV2

        # 3. Daily callback
        @WINFUNCTYPE(None, POINTER(TAssetID), c_wchar_p,
                     c_double, c_double, c_double, c_double, c_double, c_double,
                     c_double, c_double, c_double, c_double,
                     c_int, c_int, c_int, c_int, c_int, c_int, c_int)
        def daily_cb(asset_id_ptr, date, s_open, s_high, s_low, s_close, s_vol,
                     s_ajuste, s_max_lim, s_min_lim, s_vol_buyer, s_vol_seller,
                     n_qty, n_neg, n_contratos, n_qty_buyer, n_qty_seller,
                     n_neg_buyer, n_neg_seller) -> None:
            asset_id = asset_id_ptr.contents
            log.info("DAILY_RAW ticker=%r date=%r close=%r", asset_id.ticker, date, s_close)
            ticker = asset_id.ticker or ""
            if not ticker:
                return
            try:
                dt = datetime.strptime(date[:10], "%d/%m/%Y").replace(
                    tzinfo=timezone.utc) if date else datetime.now(tz=timezone.utc)
            except Exception:
                dt = datetime.now(tz=timezone.utc)
            agent._db_queue.put_nowait({
                "_type": "daily",
                "time": dt, "ticker": ticker,
                "exchange": asset_id.bolsa or "B",
                "open": s_open, "high": s_high, "low": s_low, "close": s_close,
                "volume": s_vol, "adjust": s_ajuste,
                "max_limit": s_max_lim, "min_limit": s_min_lim,
                "vol_buyer": s_vol_buyer, "vol_seller": s_vol_seller,
                "qty": n_qty, "trades": n_neg, "open_contracts": n_contratos,
                "qty_buyer": n_qty_buyer, "qty_seller": n_qty_seller,
                "neg_buyer": n_neg_buyer, "neg_seller": n_neg_seller,
            })

        # 4. Progress callback
        @WINFUNCTYPE(None, POINTER(TAssetID), c_int)
        def progress_cb(asset_id_ptr, progress) -> None:
            pass  # noop

        # 5. TinyBook callback
        @WINFUNCTYPE(None, POINTER(TAssetID), c_double, c_int, c_int)
        def tiny_book_cb(asset_id_ptr, price, qty, side) -> None:
            pass  # noop

        # 6. Trade callback V2 (SetTradeCallbackV2)
        # CORRIGIDO: POINTER(TConnectorAssetIdentifier) — by-value com c_wchar_p
        # causa ponteiro dangling. A DLL sempre passa por referencia.
        @WINFUNCTYPE(None, TConnectorAssetIdentifier, c_size_t, c_uint)
        def trade_v2_cb(asset_id, p_trade, flags) -> None:
            import sys; print(f"TICK_RAW ticker={asset_id.Ticker!r} p={p_trade}", flush=True, file=sys.stderr)
            if not agent._dll:
                return
            trade = TConnectorTrade(Version=0)
            if not agent._dll.TranslateTrade(c_size_t(p_trade), byref(trade)):
                return
            ticker = asset_id.Ticker or ""
            if not ticker:
                return
            now = datetime.now(tz=timezone.utc)
            agent._total_ticks += 1
            try:
                agent._db_queue.put_nowait({
                    "_type": "tick",
                    "time": now, "ticker": ticker,
                    "exchange": asset_id.Exchange or "B",
                    "price": trade.Price, "quantity": trade.Quantity,
                    "volume": trade.Volume, "buy_agent": trade.BuyAgent,
                    "sell_agent": trade.SellAgent, "trade_number": trade.TradeNumber,
                    "trade_type": trade.TradeType, "is_edit": bool(flags & 1),
                })
            except queue.Full:
                pass  # descarta se fila cheia

        # 7. Asset list info V2 (SetAssetListInfoCallbackV2)
        @WINFUNCTYPE(None, POINTER(TAssetID), c_wchar_p, c_wchar_p,
                     c_int, c_int, c_int, c_int, c_int,
                     c_double, c_double, c_wchar_p, c_wchar_p,
                     c_wchar_p, c_wchar_p, c_wchar_p)
        def asset_info_v2_cb(asset_id_ptr, name, description,
                              min_qty, max_qty, lot, sec_type, sec_subtype,
                              min_incr, contract_mult, valid_date, isin,
                              sector, sub_sector, segment) -> None:
            asset_id = asset_id_ptr.contents
            ticker = asset_id.ticker or ""
            if not ticker:
                return
            agent._total_assets += 1
            # Parse valid_date
            vd = None
            if valid_date:
                try:
                    vd = datetime.strptime(valid_date[:10], "%d/%m/%Y").date()
                except Exception:
                    pass
            try:
                agent._db_queue.put_nowait({
                    "_type": "asset",
                    "ticker": ticker, "exchange": asset_id.bolsa or "B",
                    "name": name, "description": description,
                    "security_type": sec_type, "security_subtype": sec_subtype,
                    "min_order_qty": min_qty, "max_order_qty": max_qty,
                    "lot_size": lot, "min_price_increment": min_incr,
                    "contract_multiplier": contract_mult,
                    "valid_date": vd, "isin": isin,
                    "sector": sector, "sub_sector": sub_sector,
                    "segment": segment, "feed_type": asset_id.feed,
                })
            except queue.Full:
                pass

        # 8. Asset list callback (V1 compat)
        @WINFUNCTYPE(None, POINTER(TAssetID), c_wchar_p)
        def asset_cb(asset_id_ptr, name) -> None:
            pass  # usa V2

        # 9. Adjust history V2
        @WINFUNCTYPE(None, POINTER(TAssetID), c_double, c_wchar_p, c_wchar_p,
                     c_wchar_p, c_wchar_p, c_wchar_p, c_uint, c_double)
        def adjust_v2_cb(asset_id_ptr, value, adj_type, observ,
                          dt_ajuste, dt_delib, dt_pgto, flags, mult) -> None:
            asset_id = asset_id_ptr.contents
            ticker = asset_id.ticker or ""
            if not ticker:
                return
            def parse_date(s):
                if not s:
                    return None
                try:
                    return datetime.strptime(s[:10], "%d/%m/%Y").date()
                except Exception:
                    return None
            try:
                agent._db_queue.put_nowait({
                    "_type": "adjustment",
                    "ticker": ticker, "exchange": asset_id.bolsa or "B",
                    "adjust_date": parse_date(dt_ajuste),
                    "deliberation_date": parse_date(dt_delib),
                    "payment_date": parse_date(dt_pgto),
                    "adjust_type": adj_type, "value": value,
                    "multiplier": mult, "flags": flags,
                    "observation": observ,
                })
            except queue.Full:
                pass

        # 10. Price depth callback
        # CORRIGIDO: POINTER — mesmo motivo do trade_v2_cb.
        # asset_id_ptr passado diretamente para GetPriceGroup/GetTheoreticalValues
        # (ambos esperam POINTER, nao byref de uma copia local).
        @WINFUNCTYPE(None, TConnectorAssetIdentifier, c_ubyte, c_int, c_ubyte)
        def price_depth_cb(asset_id, side, position, update_type) -> None:
            if not agent._dll:
                return
            # update_type 4=FullBook, 1=Edit, 3=Insert, 0=Add
            if update_type in (0, 1, 3, 4):
                pg = TConnectorPriceGroup(Version=0)
                if agent._dll.GetPriceGroup(byref(asset_id), side, position, byref(pg)) != 0:
                    return
                price = pg.Price
                # Preco teorico em leilao
                if pg.PriceGroupFlags & PG_IS_THEORIC:
                    tp = c_double()
                    tq = c_int64()
                    if agent._dll.GetTheoreticalValues(byref(asset_id), byref(tp), byref(tq)) == 0:
                        price = tp.value
                try:
                    agent._db_queue.put_nowait({
                        "_type": "book",
                        "time": datetime.now(tz=timezone.utc),
                        "ticker": asset_id.Ticker or "",
                        "exchange": asset_id.Exchange or "B",
                        "side": side, "position": position,
                        "price": price, "quantity": pg.Quantity,
                        "count": pg.Count,
                        "is_theoric": bool(pg.PriceGroupFlags & PG_IS_THEORIC),
                    })
                except queue.Full:
                    pass

        # 11. Order callback
        @WINFUNCTYPE(None, TConnectorOrderIdentifier)
        def order_cb(order_id) -> None:
            log.info("order_callback local_id=%d cl_ord=%s",
                     order_id.LocalOrderID, order_id.ClOrderID or "")

        # 12. TradingMessageResult callback
        class TConnectorTradingMessageResult(Structure):
            _fields_ = [
                ("Version",       c_ubyte),
                ("BrokerID",      c_int),
                ("OrderID",       TConnectorOrderIdentifier),
                ("MessageID",     c_int64),
                ("ResultCode",    c_ubyte),
                ("Message",       c_wchar_p),
                ("MessageLength", c_int),
            ]

        @WINFUNCTYPE(None, POINTER(TConnectorTradingMessageResult))
        def trading_msg_cb(result_ptr) -> None:
            r = result_ptr.contents
            log.info("trading_msg broker=%d msg_id=%d code=%d msg=%s",
                     r.BrokerID, r.MessageID, r.ResultCode,
                     (r.Message or "")[:80])

        # 13. Broker account list changed
        @WINFUNCTYPE(None, c_int, c_uint)
        def broker_account_cb(broker_id, changed) -> None:
            log.info("broker_account_changed broker=%d changed=%d", broker_id, changed)

        # Guarda todas as refs
        self._callbacks = [
            state_cb, account_cb, new_trade_cb, daily_cb,
            progress_cb, tiny_book_cb, trade_v2_cb, asset_info_v2_cb,
            asset_cb, adjust_v2_cb, price_depth_cb, order_cb,
            trading_msg_cb, broker_account_cb,
        ]

    # ------------------------------------------------------------------
    # DB Worker thread
    # ------------------------------------------------------------------
    def _db_worker(self) -> None:
        log.info("db_worker.started")
        while not self._stop_event.is_set():
            try:
                item = self._db_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if self._db is None:
                continue

            try:
                t = item.get("_type")
                if t == "tick":
                    self._db.insert_tick(item)
                elif t == "daily":
                    self._db.upsert_daily_bar(item)
                elif t == "asset":
                    self._db.upsert_asset(item)
                elif t == "adjustment":
                    sql = """
                    INSERT INTO profit_adjustments
                        (ticker, exchange, adjust_date, deliberation_date, payment_date,
                         adjust_type, value, multiplier, flags, observation)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(ticker, exchange, adjust_date, adjust_type) DO UPDATE SET
                        value=EXCLUDED.value, multiplier=EXCLUDED.multiplier
                    """
                    self._db.execute(sql, (
                        item["ticker"], item.get("exchange","B"),
                        item.get("adjust_date"), item.get("deliberation_date"),
                        item.get("payment_date"), item.get("adjust_type"),
                        item.get("value"), item.get("multiplier"),
                        item.get("flags"), item.get("observation"),
                    ))
                elif t == "book":
                    sql = """
                    INSERT INTO profit_order_book
                        (time, ticker, exchange, side, position, price,
                         quantity, count, is_theoric)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """
                    self._db.execute(sql, (
                        item["time"], item["ticker"], item.get("exchange","B"),
                        item["side"], item["position"], item.get("price"),
                        item.get("quantity"), item.get("count"), item.get("is_theoric",False),
                    ))
                elif t == "state":
                    log.info("state conn_type=%d result=%d",
                             item["conn_type"], item["result"])
            except Exception as e:
                log.warning("db_worker.error type=%s error=%s", item.get("_type"), e)

        log.info("db_worker.stopped")

    # ------------------------------------------------------------------
    # Envio de ordens
    # ------------------------------------------------------------------
    def _get_account(self, env: str) -> tuple[int, str, str, str]:
        """Retorna (broker_id, account_id, sub_account_id, routing_password) por env.
        Resolve broker_id pelo nome da corretora se necessario."""
        if env == "production":
            broker_key = (self._prod_broker_str or "").upper()
            account_key = str(self._prod_account or "")
            routing_pass = self._prod_pass
        else:
            broker_key = (self._sim_broker_str or "").upper()
            account_key = str(self._sim_account or "")
            routing_pass = self._sim_pass

        # Tenta resolver pelo nome da corretora via callback
        if broker_key and broker_key in self._discovered_accounts:
            broker_id, account_id = self._discovered_accounts[broker_key]
            return (broker_id, account_id, "", routing_pass)

        # Tenta resolver pelo account_id
        if account_key and account_key in self._discovered_accounts:
            broker_id, account_id = self._discovered_accounts[account_key]
            return (broker_id, account_id, "", routing_pass)

        # Fallback: usa valores do .env diretamente
        broker_id = self._sim_broker if env != "production" else self._prod_broker
        return (broker_id, account_key, "", routing_pass)

    def send_order(self, params: dict) -> dict:
        """
        Envia uma ordem. params:
          env           : 'simulation' | 'production'
          order_type    : 'market' | 'limit' | 'stop'
          order_side    : 'buy' | 'sell'
          ticker        : str
          exchange      : str (default 'B')
          quantity      : int
          price         : float (obrigatorio para limit/stop; -1 para market)
          stop_price    : float (obrigatorio para stop; -1 caso contrario)
        """
        if not self._dll:
            return {"ok": False, "error": "DLL nao inicializada"}

        env = params.get("env", "simulation")
        broker_id, account_id, sub_id, routing_pass = self._get_account(env)

        if not account_id:
            return {"ok": False, "error": f"Conta {env} nao configurada no .env"}

        type_map = {"market": ORDER_TYPE_MARKET, "limit": ORDER_TYPE_LIMIT,
                    "stop": ORDER_TYPE_STOP_LIMIT}
        side_map = {"buy": ORDER_SIDE_BUY, "sell": ORDER_SIDE_SELL}

        order_type = type_map.get(params.get("order_type","limit").lower(), ORDER_TYPE_LIMIT)
        order_side = side_map.get(params.get("order_side","buy").lower(), ORDER_SIDE_BUY)
        ticker     = params.get("ticker", "")
        exchange   = params.get("exchange", "B")
        qty        = int(params.get("quantity", 0))
        price      = float(params.get("price", -1))
        stop_price = float(params.get("stop_price", -1))

        if not ticker or qty <= 0:
            return {"ok": False, "error": "ticker e quantity sao obrigatorios"}

        order = TConnectorSendOrder(Version=2)
        order.AccountID = TConnectorAccountIdentifier(
            Version=0, BrokerID=broker_id,
            AccountID=account_id, SubAccountID=sub_id, Reserved=0,
        )
        order.AssetID = TConnectorAssetIdentifier(
            Version=0, Ticker=ticker, Exchange=exchange, FeedType=0,
        )
        order.Password  = routing_pass
        order.OrderType = order_type
        order.OrderSide = order_side
        order.Price     = price
        order.StopPrice = stop_price
        order.Quantity  = qty
        order.MessageID = -1

        local_id = self._dll.SendOrder(byref(order))
        if local_id < 0:
            return {"ok": False, "error": f"SendOrder falhou: {local_id}"}

        self._total_orders += 1
        if self._db:
            self._db.insert_order({
                "local_order_id": local_id, "message_id": order.MessageID,
                "broker_id": broker_id, "account_id": account_id,
                "env": env, "ticker": ticker, "exchange": exchange,
                "order_type": order_type, "order_side": order_side,
                "price": price, "stop_price": stop_price, "quantity": qty,
            })

        log.info("order.sent local_id=%d ticker=%s side=%s type=%s qty=%d env=%s",
                 local_id, ticker, order_side, order_type, qty, env)
        return {"ok": True, "local_order_id": local_id, "message_id": order.MessageID}

    def cancel_order(self, params: dict) -> dict:
        if not self._dll:
            return {"ok": False, "error": "DLL nao inicializada"}
        env = params.get("env", "simulation")
        broker_id, account_id, sub_id, routing_pass = self._get_account(env)
        cl_ord_id = params.get("cl_ord_id", "")
        local_id  = int(params.get("local_order_id", -1))

        cancel = TConnectorCancelOrder(Version=1, MessageID=-1)
        cancel.AccountID = TConnectorAccountIdentifier(
            Version=0, BrokerID=broker_id, AccountID=account_id,
            SubAccountID=sub_id, Reserved=0,
        )
        cancel.OrderID = TConnectorOrderIdentifier(
            Version=0, LocalOrderID=local_id, ClOrderID=cl_ord_id,
        )
        cancel.Password = routing_pass
        ret = self._dll.SendCancelOrderV2(byref(cancel))
        return {"ok": ret == 0, "ret": ret}

    def cancel_all_orders(self, params: dict) -> dict:
        if not self._dll:
            return {"ok": False, "error": "DLL nao inicializada"}
        env = params.get("env", "simulation")
        broker_id, account_id, sub_id, routing_pass = self._get_account(env)
        cancel = TConnectorCancelAllOrders(Version=0)
        cancel.AccountID = TConnectorAccountIdentifier(
            Version=0, BrokerID=broker_id, AccountID=account_id,
            SubAccountID=sub_id, Reserved=0,
        )
        cancel.Password = routing_pass
        ret = self._dll.SendCancelAllOrdersV2(byref(cancel))
        return {"ok": ret == 0, "ret": ret}

    def change_order(self, params: dict) -> dict:
        if not self._dll:
            return {"ok": False, "error": "DLL nao inicializada"}
        env = params.get("env", "simulation")
        broker_id, account_id, sub_id, routing_pass = self._get_account(env)
        change = TConnectorChangeOrder(Version=1, MessageID=-1)
        change.AccountID = TConnectorAccountIdentifier(
            Version=0, BrokerID=broker_id, AccountID=account_id,
            SubAccountID=sub_id, Reserved=0,
        )
        change.OrderID = TConnectorOrderIdentifier(
            Version=0,
            LocalOrderID=int(params.get("local_order_id", -1)),
            ClOrderID=params.get("cl_ord_id", ""),
        )
        change.Password  = routing_pass
        change.Price     = float(params.get("price", -1))
        change.StopPrice = float(params.get("stop_price", -1))
        change.Quantity  = int(params.get("quantity", 0))
        ret = self._dll.SendChangeOrderV2(byref(change))
        return {"ok": ret == 0, "ret": ret}

    def zero_position(self, params: dict) -> dict:
        if not self._dll:
            return {"ok": False, "error": "DLL nao inicializada"}
        env = params.get("env", "simulation")
        broker_id, account_id, sub_id, routing_pass = self._get_account(env)
        ticker   = params.get("ticker", "")
        exchange = params.get("exchange", "B")
        pos_type = POSITION_TYPE_DAYTRADE if params.get("daytrade") else POSITION_TYPE_CONSOLIDATED
        zero = TConnectorZeroPosition(Version=2, PositionType=pos_type, MessageID=-1)
        zero.AccountID = TConnectorAccountIdentifier(
            Version=0, BrokerID=broker_id, AccountID=account_id,
            SubAccountID=sub_id, Reserved=0,
        )
        zero.AssetID = TConnectorAssetIdentifier(
            Version=0, Ticker=ticker, Exchange=exchange, FeedType=0,
        )
        zero.Password = routing_pass
        zero.Price    = float(params.get("price", -1))  # -1 = mercado
        ret = self._dll.SendZeroPositionV2(byref(zero))
        return {"ok": ret >= 0, "local_order_id": ret}

    def subscribe_ticker(self, params: dict) -> dict:
        ticker   = params.get("ticker", "").strip().upper()
        exchange = params.get("exchange", "B").strip().upper()
        notes    = params.get("notes", "")
        if not ticker:
            return {"ok": False, "error": "ticker obrigatorio"}
        if self._db:
            self._db.add_ticker(ticker, exchange, notes)
        self._subscribe(ticker, exchange)
        return {"ok": True, "subscribed": list(self._subscribed)}

    def unsubscribe_ticker(self, params: dict) -> dict:
        ticker   = params.get("ticker", "").strip().upper()
        exchange = params.get("exchange", "B").strip().upper()
        if not ticker:
            return {"ok": False, "error": "ticker obrigatorio"}
        if self._db:
            self._db.remove_ticker(ticker, exchange)
        key = ticker + ":" + exchange
        self._subscribed.discard(key)
        if self._dll:
            try:
                self._dll.UnsubscribeTicker(c_wchar_p(ticker), c_wchar_p(exchange))
                log.info("profit_agent.unsubscribed ticker=%s exchange=%s", ticker, exchange)
            except Exception as e:
                log.warning("profit_agent.unsubscribe_error ticker=%s e=%s", ticker, e)
        return {"ok": True, "subscribed": list(self._subscribed)}

    def list_tickers(self) -> dict:
        db_tickers = []
        if self._db:
            db_tickers = [
                {
                    "ticker": t,
                    "exchange": e,
                    "subscribed": (t + ":" + e) in self._subscribed,
                }
                for t, e in self._db.get_subscribed_tickers()
            ]
        return {
            "tickers": db_tickers,
            "subscribed_in_dll": list(self._subscribed),
        }

    def get_status(self) -> dict:
        return {
            "market_connected":   self._market_ok,
            "routing_connected":  self._routing_ok,
            "login_ok":           self._login_ok,
            "activate_ok":        self._activate_ok,
            "subscribed_tickers": list(self._subscribed),
            "total_ticks":        self._total_ticks,
            "total_orders":       self._total_orders,
            "total_assets":       self._total_assets,
            "db_queue_size":      self._db_queue.qsize(),
            "db_connected":       self._db is not None,
        }

    # ------------------------------------------------------------------
    # HTTP Server
    # ------------------------------------------------------------------
    def _start_http(self, port: int) -> None:
        agent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                log.debug("http " + fmt, *args)

            def _send_json(self, data: dict, code: int = 200) -> None:
                body = json.dumps(data).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_body(self) -> dict:
                length = int(self.headers.get("Content-Length", 0))
                if length == 0:
                    return {}
                raw = self.rfile.read(length)
                try:
                    return json.loads(raw)
                except Exception:
                    return {}

            def do_GET(self):
                if self.path == "/status":
                    self._send_json(agent.get_status())
                elif self.path == "/accounts":
                    self._send_json({
                        "simulation": {
                            "broker_id": agent._sim_broker,
                            "account_id": agent._sim_account,
                            "configured": bool(agent._sim_account),
                        },
                        "production": {
                            "broker_id": agent._prod_broker,
                            "account_id": agent._prod_account,
                            "configured": bool(agent._prod_account),
                        },
                    })
                elif self.path == "/tickers":
                    self._send_json(agent.list_tickers())
                elif self.path == "/health":
                    self._send_json({"ok": True})
                else:
                    self._send_json({"error": "not found"}, 404)

            def do_POST(self):
                body = self._read_body()
                if self.path == "/order/send":
                    self._send_json(agent.send_order(body))
                elif self.path == "/order/cancel":
                    self._send_json(agent.cancel_order(body))
                elif self.path == "/order/cancel_all":
                    self._send_json(agent.cancel_all_orders(body))
                elif self.path == "/order/change":
                    self._send_json(agent.change_order(body))
                elif self.path == "/order/zero_position":
                    self._send_json(agent.zero_position(body))
                elif self.path == "/subscribe":
                    self._send_json(agent.subscribe_ticker(body))
                elif self.path == "/tickers/add":
                    self._send_json(agent.subscribe_ticker(body))
                elif self.path == "/tickers/remove":
                    self._send_json(agent.unsubscribe_ticker(body))
                else:
                    self._send_json({"error": "not found"}, 404)

        server = HTTPServer(("127.0.0.1", port), Handler)
        server.serve_forever()

    # ------------------------------------------------------------------
    # Heartbeat loop (main thread)
    # ------------------------------------------------------------------
    def _heartbeat_loop(self) -> None:
        log.info("profit_agent.running")
        while not self._stop_event.is_set():
            time.sleep(30)
            if self._db:
                self._db.update_agent_status(self.get_status())
            log.info("heartbeat ticks=%d orders=%d assets=%d queue=%d",
                     self._total_ticks, self._total_orders,
                     self._total_assets, self._db_queue.qsize())

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    def stop(self) -> None:
        log.info("profit_agent.stopping")
        self._stop_event.set()
        if self._dll:
            try:
                self._dll.DLLFinalize()
            except Exception as e:
                log.warning("dll_finalize_error e=%s", e)
        log.info("profit_agent.stopped")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    # Carrega .env antes de TUDO
    env_candidates = [
        r"D:\Projetos\finanalytics_ai_fresh\.env",
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".env"),
        os.path.join(os.path.dirname(__file__), ".env"),
    ]
    for candidate in env_candidates:
        if os.path.exists(candidate):
            _load_env(candidate)
            break

    _setup_logging()

    if sys.platform != "win32":
        log.error("profit_agent requer Windows (ProfitDLL e WinDLL)")
        sys.exit(1)

    agent = ProfitAgent()

    def _handle_signal(sig, frame):
        log.info("signal_received sig=%d", sig)
        agent.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    agent.start()


if __name__ == "__main__":
    main()
