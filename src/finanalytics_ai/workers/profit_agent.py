

"""

profit_agent.py — Agente standalone ProfitDLL (Nelogica)



Arquitetura intencional: ZERO imports do projeto finanalytics_ai.

Usa apenas stdlib + psycopg2 + python-dotenv para garantir

que nada inicializa Winsock antes da DLL conectar.



Funcionalidades:

  - Conecta via DLLInitializeMarketLogin (Market Data)

  - Roteamento desabilitado neste processo (DLL nao suporta dual-init)

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



class TConnectorTradingMessageResult(Structure):

    """Resultado de uma operacao de roteamento (aceite, rejeicao, fill)."""

    _fields_ = [

        ("Version",       c_ubyte),

        ("BrokerID",      c_int),

        ("OrderID",       TConnectorOrderIdentifier),

        ("MessageID",     c_int64),

        ("ResultCode",    c_ubyte),

        ("Message",       c_wchar_p),

        ("MessageLength", c_int),

    ]



# Mapa de ResultCode para order_status (convencao FIX parcial)

_TRADING_RESULT_STATUS: dict[int, int] = {

    0:  0,   # OK / New

    1:  8,   # Rejected

    2:  2,   # Filled

    3:  1,   # Partial fill

    4:  4,   # Cancelled

    5:  0,   # Changed (aceito)

    6:  4,   # ZeroPosition aceito

}



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



    @property
    def is_connected(self) -> bool:
        """Retorna True se a conexão está ativa."""
        if self._conn is None:
            return False
        try:
            cur = self._conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            return True
        except Exception:
            self._conn = None
            return False

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

    def _ensure_connected(self) -> bool:
        """Garante conexão ativa — reconecta se necessário."""
        if self._conn is not None:
            try:
                # Testa a conexão com query leve
                cur = self._conn.cursor()
                cur.execute("SELECT 1")
                cur.close()
                return True
            except Exception:
                log.warning("db.connection_lost — tentando reconectar...")
                self._conn = None
        # Tenta reconectar até 3 vezes
        import psycopg2  # type: ignore
        for attempt in range(1, 4):
            try:
                self._conn = psycopg2.connect(self._dsn)
                self._conn.autocommit = True
                log.info("db.reconnected attempt=%d dsn=%s",
                         attempt, self._dsn.split("@")[-1])
                return True
            except Exception as e:
                log.warning("db.reconnect_failed attempt=%d error=%s", attempt, e)
                import time as _t
                _t.sleep(attempt * 2)
        log.error("db.reconnect_giving_up — persistência desativada temporariamente")
        return False



    def execute(self, sql: str, params: tuple = ()) -> bool:
        # Garante conexão ativa (reconecta se necessário)
        if not self._ensure_connected():
            return False
        try:
            with self._lock:
                cur = self._conn.cursor()
                cur.execute(sql, params)
                cur.close()
            return True
        except Exception as e:
            log.warning("db.execute_failed error=%s sql=%.100s", e, sql)
            # Marca conexão como inválida para forçar reconexão no próximo execute
            try:
                self._conn.rollback()
            except Exception:
                self._conn = None
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

        """Cria/migra tabela de tickers subscritos."""

        # Cria tabela se não existir

        self.execute("""

        CREATE TABLE IF NOT EXISTS profit_subscribed_tickers (

            ticker          VARCHAR(20)  NOT NULL,

            exchange        VARCHAR(10)  NOT NULL DEFAULT 'B',

            active          BOOLEAN      NOT NULL DEFAULT TRUE,

            subscribe_book  BOOLEAN      NOT NULL DEFAULT FALSE,

            priority        INTEGER      NOT NULL DEFAULT 0,

            notes           TEXT,

            created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

            updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

            PRIMARY KEY (ticker, exchange)

        )

        """)

        # Migração: adiciona colunas novas se não existirem (idempotente)

        migrations = [

            "ALTER TABLE profit_subscribed_tickers ADD COLUMN IF NOT EXISTS subscribe_book BOOLEAN NOT NULL DEFAULT FALSE",

            "ALTER TABLE profit_subscribed_tickers ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 0",

            "ALTER TABLE profit_subscribed_tickers ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",

            "ALTER TABLE profit_subscribed_tickers ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",

            # Renomeia added_at → created_at se existir

            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='profit_subscribed_tickers' AND column_name='added_at') THEN ALTER TABLE profit_subscribed_tickers RENAME COLUMN added_at TO created_at; END IF; END $$",

        ]

        for m in migrations:

            self.execute(m)



    def list_tickers_full(self, only_active: bool = False) -> list:

        """Lista tickers com todos os campos."""

        if self._conn is None:

            return []

        try:

            where = "WHERE active = TRUE" if only_active else ""

            with self._lock:

                cur = self._conn.cursor()

                cur.execute(f"""

                    SELECT ticker, exchange, active, subscribe_book,

                           priority, notes, created_at, updated_at

                    FROM profit_subscribed_tickers

                    {where}

                    ORDER BY priority DESC, ticker

                """)

                cols = [d[0] for d in cur.description]

                rows = cur.fetchall()

                cur.close()

            return [dict(zip(cols, [

                str(v) if hasattr(v, 'isoformat') else v for v in row

            ])) for row in rows]

        except Exception as e:

            log.warning("db.list_tickers_full error=%s", e)

            return []



    def upsert_ticker(self, ticker: str, exchange: str = "B",

                      active: bool = True, subscribe_book: bool = False,

                      priority: int = 0, notes: str = "") -> bool:

        """Insere ou atualiza um ticker."""

        sql = """

        INSERT INTO profit_subscribed_tickers

            (ticker, exchange, active, subscribe_book, priority, notes, updated_at)

        VALUES (%s, %s, %s, %s, %s, %s, NOW())

        ON CONFLICT (ticker, exchange) DO UPDATE SET

            active         = EXCLUDED.active,

            subscribe_book = EXCLUDED.subscribe_book,

            priority       = EXCLUDED.priority,

            notes          = EXCLUDED.notes,

            updated_at     = NOW()

        """

        return self.execute(sql, (

            ticker.upper(), exchange.upper(),

            active, subscribe_book, priority, notes

        ))



    def toggle_ticker(self, ticker: str, exchange: str, active: bool) -> bool:

        """Ativa ou desativa subscrição de um ticker."""

        return self.execute("""

            UPDATE profit_subscribed_tickers

            SET active = %s, updated_at = NOW()

            WHERE ticker = %s AND exchange = %s

        """, (active, ticker.upper(), exchange.upper()))



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

                # Detecta exchange pelo sufixo (ex: WINFUT:F → exchange=F)

                for t in tickers:

                    parts = t.split(":")

                    tkr = parts[0].strip().upper()

                    exch = parts[1].strip().upper() if len(parts) > 1 else "B"

                    is_future = exch == "F" or tkr.endswith("FUT")

                    self.upsert_ticker(

                        ticker=tkr, exchange=exch,

                        active=True,

                        subscribe_book=False,

                        priority=10 if is_future else 5,

                        notes="Seeded from .env",

                    )

                log.info("db.tickers_seeded_from_env count=%d", len(tickers))

        except Exception as e:

            log.warning("db.seed_tickers_failed error=%s", e)



    def ensure_history_tickers_table(self) -> None:

        """Cria tabela de configuração de coleta de histórico."""

        sql = """

        CREATE TABLE IF NOT EXISTS profit_history_tickers (

            ticker              VARCHAR(20)  NOT NULL,

            exchange            VARCHAR(5)   NOT NULL,

            active              BOOLEAN      NOT NULL DEFAULT TRUE,

            collect_from        TIMESTAMPTZ  NOT NULL DEFAULT '2026-01-01 00:00:00+00',

            last_collected_at   TIMESTAMPTZ  NULL,

            last_collected_from TIMESTAMPTZ  NULL,

            last_collected_to   TIMESTAMPTZ  NULL,

            last_tick_count     INTEGER      NULL,

            notes               TEXT,

            created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

            updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

            PRIMARY KEY (ticker, exchange)

        )

        """

        self.execute(sql)



    def get_active_history_tickers(self) -> list:

        """Retorna [(ticker, exchange, collect_from)] dos ativos com active=True."""

        if self._conn is None:

            return []

        try:

            with self._lock:

                cur = self._conn.cursor()

                cur.execute("""

                    SELECT ticker, exchange, collect_from

                    FROM profit_history_tickers

                    WHERE active = TRUE

                    ORDER BY ticker

                """)

                rows = cur.fetchall()

                cur.close()

            return [(r[0], r[1], r[2]) for r in rows]

        except Exception as e:

            log.warning("db.get_active_history_tickers error=%s", e)

            return []



    def list_history_tickers(self) -> list:

        """Retorna todos os tickers (ativos e inativos) como lista de dicts."""

        if self._conn is None:

            return []

        try:

            with self._lock:

                cur = self._conn.cursor()

                cur.execute("""

                    SELECT ticker, exchange, active, collect_from,

                           last_collected_at, last_collected_from,

                           last_collected_to, last_tick_count, notes,

                           created_at, updated_at

                    FROM profit_history_tickers

                    ORDER BY ticker, exchange

                """)

                cols = [d[0] for d in cur.description]

                rows = cur.fetchall()

                cur.close()

            return [dict(zip(cols, [

                str(v) if hasattr(v, 'isoformat') else v for v in row

            ])) for row in rows]

        except Exception as e:

            log.warning("db.list_history_tickers error=%s", e)

            return []



    def upsert_history_ticker(self, ticker: str, exchange: str,

                               active: bool = True,

                               collect_from: str = "2026-01-01 00:00:00",

                               notes: str = "") -> bool:

        """Insere ou atualiza configuração de um ticker para coleta."""

        sql = """

        INSERT INTO profit_history_tickers

            (ticker, exchange, active, collect_from, notes, updated_at)

        VALUES (%s, %s, %s, %s, %s, NOW())

        ON CONFLICT (ticker, exchange) DO UPDATE SET

            active       = EXCLUDED.active,

            collect_from = EXCLUDED.collect_from,

            notes        = EXCLUDED.notes,

            updated_at   = NOW()

        """

        return self.execute(sql, (

            ticker.upper(), exchange.upper(), active, collect_from, notes

        ))



    def toggle_history_ticker(self, ticker: str, exchange: str,

                               active: bool) -> bool:

        """Ativa ou desativa a coleta de um ticker."""

        sql = """

        UPDATE profit_history_tickers

        SET active = %s, updated_at = NOW()

        WHERE ticker = %s AND exchange = %s

        """

        return self.execute(sql, (active, ticker.upper(), exchange.upper()))



    def update_history_ticker_collected(

        self, ticker: str, exchange: str,

        dt_start: str, dt_end: str, tick_count: int

    ) -> bool:

        """Atualiza metadados da última coleta bem-sucedida."""

        sql = """

        UPDATE profit_history_tickers SET

            last_collected_at   = NOW(),

            last_collected_from = %s,

            last_collected_to   = %s,

            last_tick_count     = %s,

            updated_at          = NOW()

        WHERE ticker = %s AND exchange = %s

        """

        return self.execute(sql, (_parse(dt_start), _parse(dt_end), tick_count,

                                   ticker.upper(), exchange.upper()))





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

        self._book: dict = {}

        self._sse_clients: list = []

        self._sse_lock = __import__('threading').Lock()



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



        # 2. NOOP_PATTERN_APPLIED

        # Noops simples para DLLInitializeLogin — padrao identico ao

        # 02_test_state_callback.py e 05_test_trade_v2.py que conectaram.

        # Assinatura minima: c_void_p como primeiro arg (nao Structures).

        # V2 callbacks (trade, daily, assets) registrados via Set* apos init.

        from ctypes import WINFUNCTYPE as _WF, c_int as _ci, c_double as _cd, c_void_p as _cv



        @_WF(None, _ci, _ci)

        def _state_cb_init(conn_type: int, result: int) -> None:

            # MINIMAL — sem lock, sem queue durante init (diagnostico prova que

            # with self._state_lock bloqueia result=4 de ser entregue)

            if conn_type == CONN_STATE_MARKET_DATA:

                self._market_ok = (result == MARKET_CONNECTED)

                if result == MARKET_CONNECTED:

                    self._market_connected.set()

            elif conn_type == CONN_STATE_LOGIN:

                self._login_ok = (result == LOGIN_CONNECTED)

            elif conn_type == CONN_STATE_MARKET_LOGIN:

                self._activate_ok = (result == ACTIVATE_VALID)

            elif conn_type == CONN_STATE_ROUTING:

                self._routing_ok = (result == ROUTING_BROKER_CONNECTED)



        # Callbacks V1 REAIS para DLLInitializeLogin — padrao do teste 11 que funcionou.

        # CRITICO: a DLL precisa de callbacks reais (nao noops) para completar

        # a conexao de market data e entregar result=4 (MARKET_CONNECTED).

        # Os callbacks V2 (SetTradeCallbackV2 etc.) registrados antes continuam

        # sendo os receptores principais — estes V1 apenas satisfazem a DLL na init.

        from ctypes import c_char as _cc, c_wchar_p as _cwp, c_uint as _cu

        import queue as _queue



        @_WF(None, _cv, _cwp, _cu, _cd, _cd, _ci, _ci, _ci, _ci, _cc)

        def _trade_v1_init(asset_ptr, date, trade_num, price, vol, qty,

                           buy_agent, sell_agent, trade_type, edit):

            if not asset_ptr:

                return

            try:

                import ctypes as _ct2

                asset_id = _ct2.cast(asset_ptr, _ct2.POINTER(TAssetID)).contents

                ticker = asset_id.ticker or ""

                if not ticker:

                    return

                from datetime import datetime, timezone

                self._total_ticks += 1

                self._db_queue.put_nowait({

                    "_type": "tick",

                    "time": datetime.now(tz=timezone.utc),

                    "ticker": ticker,

                    "exchange": asset_id.bolsa or "B",

                    "price": price, "quantity": qty,

                    "volume": vol, "buy_agent": buy_agent,

                    "sell_agent": sell_agent,

                    "trade_number": trade_num,

                    "trade_type": trade_type,

                    "is_edit": bool(edit),

                })

                if self._total_ticks <= 5:

                    log.info("TICK_V1 ticker=%s price=%s qty=%s", ticker, price, qty)

            except Exception:

                pass



        @_WF(None, _cv, _cwp,

             _cd, _cd, _cd, _cd, _cd, _cd,

             _cd, _cd, _cd, _cd,

             _ci, _ci, _ci, _ci, _ci, _ci, _ci)

        def _daily_v1_init(asset_ptr, date, s_open, s_high, s_low, s_close,

                           s_vol, s_ajuste, s_max_lim, s_min_lim,

                           s_vol_buyer, s_vol_seller,

                           n_qty, n_neg, n_contratos, n_qty_buyer, n_qty_seller,

                           n_neg_buyer, n_neg_seller):

            pass  # daily V2 cuida dos dados reais



        _noop_progress = _WF(None, _cv, _ci)(lambda p, v: None)

        _noop_tiny     = _WF(None, _cv, _cd, _ci, _ci)(lambda *a: None)



        @_WF(None, _ci, c_wchar_p, c_wchar_p, c_wchar_p)

        def _account_cb_init(bid, bname, aid, owner):

            name = (bname or '').upper()

            acc  = (aid  or '').strip()

            log.info('account broker_id=%d broker_name=%s account=%s owner=%s',

                     bid, name, acc, owner)

            self._discovered_accounts[name] = (bid, acc)

            self._discovered_accounts[acc]  = (bid, acc)



        # Guarda refs contra GC

        self._init_refs = [

            _state_cb_init, _trade_v1_init, _daily_v1_init,

            _noop_progress, _noop_tiny, _account_cb_init,

        ]



        # 3. Configura restypes ANTES de qualquer chamada

        self._setup_dll_restypes()





        # 5. DLLInitializeLogin com noops V1 — padrao dos testes que funcionaram

        log.info('profit_agent.initializing_market_data')

        ret_md = self._dll.DLLInitializeLogin(

            c_wchar_p(self._act_key),

            c_wchar_p(self._username),

            c_wchar_p(self._password),

            _state_cb_init,   # state

            None,              # history

            None,              # order_change

            _account_cb_init, # account

            _trade_v1_init,   # new_trade V1 REAL (necessario para result=4)

            _daily_v1_init,   # new_daily V1 REAL (necessario para result=4)

            None,              # price_book

            None,              # offer_book

            None,              # history_trade

            _noop_progress,   # progress

            _noop_tiny,       # tiny_book

        )

        if ret_md != 0:

            log.error('profit_agent.dll_init_failed ret=%d', ret_md)

            sys.exit(1)



        log.info('profit_agent.dll_initialized market_ret=%d', ret_md)



        # 4. Aguarda conexao (threading.Event — sem asyncio)

        log.info("profit_agent.waiting_connection timeout=180s")

        connected = self._market_connected.wait(timeout=180.0)

        if not connected:

            log.warning("profit_agent.market_timeout continuing_anyway")





        # 5. Registra callbacks V2 APOS market connected (padrao teste 11)

        self._post_connect_setup()



        # 6. Inicia DB (APOS DLL conectar)

        log.info("profit_agent.connecting_db")

        self._db = DBWriter(self._ts_dsn)

        if self._db.connect():

            self._db.execute(

                "UPDATE profit_agent_status SET started_at=%s, version=%s WHERE id=1",

                (datetime.now(tz=timezone.utc), "1.0.0"),

            )

            # Garante tabela e migra tickers do .env (apenas se tabela vazia)

            self._db.ensure_tickers_table()

            self._db.ensure_history_tickers_table()

            # Seed padrão: WINFUT e WDOFUT (futuros, exchange=F)

            self._db.upsert_history_ticker("WINFUT", "F",

                active=True, collect_from="2026-01-01 00:00:00",

                notes="Mini Ibovespa Futuro")

            self._db.upsert_history_ticker("WDOFUT", "F",

                active=True, collect_from="2026-01-01 00:00:00",

                notes="Mini Dólar Futuro")

            self._db.ensure_history_tickers_table()

            # Seed padrão: WINFUT e WDOFUT (futuros, exchange=F)

            self._db.upsert_history_ticker("WINFUT", "F",

                active=True, collect_from="2026-01-01 00:00:00",

                notes="Mini Ibovespa Futuro")

            self._db.upsert_history_ticker("WDOFUT", "F",

                active=True, collect_from="2026-01-01 00:00:00",

                notes="Mini Dólar Futuro")

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



    def _post_connect_setup(self) -> None:

        """

        Registra Set*Callback V2 APOS market connected.

        Padrao validado pelo teste 11: Set*Callback antes do DLLInitializeLogin

        impede result=4 de ser entregue.

        """

        if not self._dll:

            return

        cbs = getattr(self, "_callbacks", [])

        if len(cbs) < 7:

            log.info("profit_agent._post_connect_setup calling _register_callbacks")

            self._register_callbacks()

            cbs = self._callbacks



        # Indices na lista self._callbacks (definida em _register_callbacks):

        # [0]=state, [1]=account, [2]=trade_v1, [3]=daily, [4]=progress,

        # [5]=tiny, [6]=trade_v2, [7]=asset_info_v2, [8]=asset, [9]=adjust_v2,

        # [10]=price_depth, [11]=order, [12]=trading_msg, [13]=broker_account

        setters = [

            ("SetTradeCallbackV2",              6),

            ("SetDailyCallback",                3),

            ("SetAssetListInfoCallbackV2",       7),

            ("SetAssetListCallback",             8),

            ("SetAdjustHistoryCallbackV2",       9),

            ("SetPriceDepthCallback",           10),

            ("SetOrderCallback",                11),

            ("SetTradingMessageResultCallback", 12),

            ("SetBrokerAccountListChangedCallback", 13),

        ]

        for fn_name, cb_idx in setters:

            try:

                fn = getattr(self._dll, fn_name, None)

                if fn and cb_idx < len(cbs):

                    fn(cbs[cb_idx])

                    log.info("profit_agent.%s registered", fn_name)

            except Exception as exc:

                log.warning("profit_agent.%s failed e=%s", fn_name, exc)



        log.info("profit_agent.v2_callbacks_registered")



    def _subscribe(self, ticker: str, exchange: str = "B") -> None:

        key = f"{ticker}:{exchange}"

        if key in self._subscribed:

            return

        ret_t = self._dll.SubscribeTicker(c_wchar_p(ticker), c_wchar_p(exchange))



        # SubscribePriceDepth - habilitado apos implementacao do price_depth_cb

        conn_id = TConnectorAssetIdentifier(

            Version=0, Ticker=ticker, Exchange=exchange, FeedType=c_ubyte(0),

        )

        ret_d = self._dll.SubscribePriceDepth(byref(conn_id))

        if ret_d != 0:

            log.warning("profit_agent.subscribe_depth_failed ticker=%s ret=%d", ticker, ret_d)



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

        # ── History (adicionado pelo patch) ──────────────────────────────

        self._dll.GetHistoryTrades.argtypes  = [c_wchar_p, c_wchar_p, c_wchar_p, c_wchar_p]

        self._dll.GetHistoryTrades.restype   = c_int

        self._dll.TranslateTrade.argtypes    = [c_size_t, POINTER(TConnectorTrade)]

        self._dll.TranslateTrade.restype     = c_int

        self._dll.SetHistoryTradeCallbackV2.restype = None



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



        # 2. Trade callback V1 - principal receptor de ticks (testado e funcionando)

        # c_void_p: TAssetIDRec com c_wchar_p passado como ponteiro oculto em Python 64-bit

        @WINFUNCTYPE(None, c_void_p, c_wchar_p, c_uint, c_double, c_double,

                     c_int, c_int, c_int, c_int, c_int)

        def new_trade_cb(asset_ptr, date, trade_num, price, vol, qty,

                         buy_agent, sell_agent, trade_type, is_edit) -> None:

            if not asset_ptr:

                return

            asset_id = ctypes.cast(asset_ptr, POINTER(TAssetID)).contents

            ticker = asset_id.ticker or ""

            if not ticker:

                return

            agent._total_ticks += 1

            now = datetime.now(tz=timezone.utc)

            try:

                agent._db_queue.put_nowait({

                    "_type": "tick",

                    "time": now, "ticker": ticker,

                    "exchange": asset_id.bolsa or "B",

                    "price": price, "quantity": qty,

                    "volume": vol, "buy_agent": buy_agent,

                    "sell_agent": sell_agent, "trade_number": trade_num,

                    "trade_type": trade_type,

                    "is_edit": bool(is_edit),

                })

            except queue.Full:

                pass



        # 3. Daily callback

        # c_void_p: POINTER(TAssetID) deve ser tratado como c_void_p em 64-bit

        @WINFUNCTYPE(None, c_void_p, c_wchar_p,

                     c_double, c_double, c_double, c_double, c_double, c_double,

                     c_double, c_double, c_double, c_double,

                     c_int, c_int, c_int, c_int, c_int, c_int, c_int)

        def daily_cb(asset_ptr, date, s_open, s_high, s_low, s_close, s_vol,

                     s_ajuste, s_max_lim, s_min_lim, s_vol_buyer, s_vol_seller,

                     n_qty, n_neg, n_contratos, n_qty_buyer, n_qty_seller,

                     n_neg_buyer, n_neg_seller) -> None:

            if not asset_ptr:

                return

            asset_id = ctypes.cast(asset_ptr, POINTER(TAssetID)).contents

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

        @WINFUNCTYPE(None, c_void_p, c_int)

        def progress_cb(asset_ptr, progress) -> None:

            pass  # noop



        # 5. TinyBook callback - top of book (nivel 1)

        @WINFUNCTYPE(None, c_void_p, c_double, c_int, c_int)

        def tiny_book_cb(asset_ptr, price, qty, side) -> None:

            if not asset_ptr:

                return

            asset_id = ctypes.cast(asset_ptr, POINTER(TAssetID)).contents

            ticker = asset_id.ticker or ""

            if not ticker:

                return

            side_key = "bids" if side == 0 else "asks"

            if ticker not in agent._book:

                agent._book[ticker] = {"bids": {}, "asks": {}}

            agent._book[ticker][side_key][1] = {

                "price": price, "quantity": qty, "count": 1, "is_theoric": False

            }



        # 6. Trade callback V2 (SetTradeCallbackV2)

        # CORRIGIDO: POINTER(TConnectorAssetIdentifier) — by-value com c_wchar_p

        # causa ponteiro dangling. A DLL sempre passa por referencia.

        @WINFUNCTYPE(None, TConnectorAssetIdentifier, c_size_t, c_uint)

        def trade_v2_cb(asset_id, p_trade, flags) -> None:

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



            if agent._sse_clients:

                import json as _j

                _e = _j.dumps({

                    'ticker': ticker, 'price': trade.Price,

                    'quantity': trade.Quantity, 'volume': trade.Volume,

                    'time': datetime.now(tz=timezone.utc).isoformat(),

                })

                with agent._sse_lock:

                    _dead = [q for q in agent._sse_clients

                             if not _try_sse_put(q, _e)]

                    for q in _dead:

                        agent._sse_clients.remove(q)



        # 7. Asset list info V2 (SetAssetListInfoCallbackV2)

        # c_void_p: POINTER(TAssetID) como c_void_p em 64-bit

        @WINFUNCTYPE(None, c_void_p, c_wchar_p, c_wchar_p,

                     c_int, c_int, c_int, c_int, c_int,

                     c_double, c_double, c_wchar_p, c_wchar_p,

                     c_wchar_p, c_wchar_p, c_wchar_p)

        def asset_info_v2_cb(asset_ptr, name, description,

                              min_qty, max_qty, lot, sec_type, sec_subtype,

                              min_incr, contract_mult, valid_date, isin,

                              sector, sub_sector, segment) -> None:

            if not asset_ptr:

                return

            asset_id = ctypes.cast(asset_ptr, POINTER(TAssetID)).contents

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

        @WINFUNCTYPE(None, c_void_p, c_wchar_p)

        def asset_cb(asset_ptr, name) -> None:

            pass  # usa V2



        # 9. Adjust history V2

        # c_void_p: POINTER(TAssetID) como c_void_p em 64-bit

        @WINFUNCTYPE(None, c_void_p, c_double, c_wchar_p, c_wchar_p,

                     c_wchar_p, c_wchar_p, c_wchar_p, c_uint, c_double)

        def adjust_v2_cb(asset_ptr, value, adj_type, observ,

                          dt_ajuste, dt_delib, dt_pgto, flags, mult) -> None:

            if not asset_ptr:

                return

            asset_id = ctypes.cast(asset_ptr, POINTER(TAssetID)).contents

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



        # 10. Price depth callback - book completo (5 niveis bid + ask)

        @WINFUNCTYPE(None, c_void_p, c_ubyte, c_int, c_ubyte)

        def price_depth_cb(asset_ptr, side, position, update_type) -> None:

            if not asset_ptr or not agent._dll:

                return

            return  # TODO: fix TAssetID cast para price_depth

            asset_id = ctypes.cast(asset_ptr, POINTER(TAssetID)).contents

            ticker = asset_id.ticker or ""

            if not ticker:

                return



            # Constroi TConnectorAssetIdentifier para GetPriceGroup

            conn_id = TConnectorAssetIdentifier(

                Version=0,

                Ticker=ticker,

                Exchange=asset_id.bolsa or "B",

                FeedType=c_ubyte(asset_id.feed if asset_id.feed else 0),

            )

            pg = TConnectorPriceGroup(Version=0)

            ret = agent._dll.GetPriceGroup(

                byref(conn_id), c_ubyte(side), c_int(position), byref(pg)

            )

            if ret != 0:

                return



            is_theoric = bool(pg.PriceGroupFlags & PG_IS_THEORIC)

            side_key = "bids" if side == 0 else "asks"



            # Atualiza book em memoria

            if ticker not in agent._book:

                agent._book[ticker] = {"bids": {}, "asks": {}}

            agent._book[ticker][side_key][position] = {

                "price": pg.Price,

                "quantity": pg.Quantity,

                "count": pg.Count,

                "is_theoric": is_theoric,

            }



            # Persiste no TimescaleDB via fila

            try:

                agent._db_queue.put_nowait({

                    "_type": "book",

                    "time": datetime.now(tz=timezone.utc),

                    "ticker": ticker,

                    "exchange": asset_id.bolsa or "B",

                    "side": int(side),

                    "position": position,

                    "price": pg.Price,

                    "quantity": pg.Quantity,

                    "count": pg.Count,

                    "is_theoric": is_theoric,

                })

            except queue.Full:

                pass



        # 11. Order callback - confirma recebimento e registra cl_ord_id

        @WINFUNCTYPE(None, TConnectorOrderIdentifier)

        def order_cb(order_id) -> None:

            local_id = order_id.LocalOrderID

            cl_ord   = order_id.ClOrderID or ""

            log.info("order_callback local_id=%d cl_ord=%s", local_id, cl_ord)

            try:

                agent._db_queue.put_nowait({

                    "_type": "order_update",

                    "local_order_id": local_id,

                    "cl_ord_id": cl_ord,

                    "order_status": 0,   # new - corretora confirmou

                })

            except queue.Full:

                pass



        # 12. TradingMessageResult callback - resultado de roteamento

        @WINFUNCTYPE(None, POINTER(TConnectorTradingMessageResult))

        def trading_msg_cb(result_ptr) -> None:

            r        = result_ptr.contents

            code     = r.ResultCode

            msg_text = (r.Message or "")[:200]

            status   = _TRADING_RESULT_STATUS.get(code, 3)

            log.info("trading_msg broker=%d msg_id=%d code=%d status=%d msg=%s",

                     r.BrokerID, r.MessageID, code, status, msg_text[:80])

            try:

                agent._db_queue.put_nowait({

                    "_type": "trading_result",

                    "local_order_id": r.OrderID.LocalOrderID,

                    "cl_ord_id":      r.OrderID.ClOrderID or "",

                    "message_id":     r.MessageID,

                    "broker_id":      r.BrokerID,

                    "result_code":    code,

                    "order_status":   status,

                    "message":        msg_text if code != 0 else None,

                })

            except queue.Full:

                pass



        # 13. Broker account list changed

        @WINFUNCTYPE(None, c_int, c_uint)

        def broker_account_cb(broker_id, changed) -> None:

            log.info("broker_account_changed broker=%d changed=%d", broker_id, changed)



        # Set*Callback V2 registrados aqui como refs mas NAO chamados ainda.

        # Serao ativados via _post_connect_setup() APOS result=4 (MARKET_CONNECTED).

        # O teste 11 prova que chamar Set*Callback ANTES do DLLInitializeLogin

        # impede result=4 de chegar.

        # Guarda todas as refs (CRITICO: manter em memoria para evitar GC)

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

                elif t == "order_update":

                    self._db.execute(

                        """UPDATE profit_orders SET

                               cl_ord_id    = COALESCE(%s, cl_ord_id),

                               order_status = LEAST(order_status, %s),

                               updated_at   = NOW()

                           WHERE local_order_id = %s""",

                        (item.get("cl_ord_id") or None,

                         item.get("order_status", 0),

                         item["local_order_id"]),

                    )

                elif t == "trading_result":

                    code   = item.get("result_code", 0)

                    status = item.get("order_status", 0)

                    msg    = item.get("message")

                    self._db.execute(

                        """UPDATE profit_orders SET

                               order_status  = %s,

                               cl_ord_id     = COALESCE(%s, cl_ord_id),

                               error_message = CASE WHEN %s IS NOT NULL THEN %s

                                                    ELSE error_message END,

                               updated_at    = NOW()

                           WHERE local_order_id = %s

                              OR (message_id IS NOT NULL AND message_id = %s)""",

                        (status,

                         item.get("cl_ord_id") or None,

                         msg, msg,

                         item.get("local_order_id"),

                         item.get("message_id")),

                    )

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

        Envio de ordens requer DLLInitializeLogin (roteamento).

        profit_agent usa DLLInitializeMarketLogin (market data only).

        Para ordens, use profit_market_worker.py em processo separado.

        """

        return {

            "ok": False,

            "error": (

                "Roteamento nao disponivel neste processo. "

                "profit_agent usa DLLInitializeMarketLogin (market data only). "

                "Para ordens, use profit_market_worker.py."

            ),

        }



    def _send_order_legacy(self, params: dict) -> dict:

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



    def list_orders(self, ticker: str = "", status: str = "", env: str = "", limit: int = 100) -> dict:

        """Retorna ordens com filtros opcionais."""

        if self._db is None or self._db._conn is None:

            return {"orders": [], "error": "DB indisponivel"}

        try:

            conditions = []

            params: list = []

            if ticker:

                conditions.append("ticker = %s")

                params.append(ticker.upper())

            if status.isdigit():

                conditions.append("order_status = %s")

                params.append(int(status))

            if env in ("simulation", "production"):

                conditions.append("env = %s")

                params.append(env)

            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

            params.append(limit)

            sql = f"SELECT * FROM profit_orders {where} ORDER BY created_at DESC LIMIT %s"

            with self._db._lock:

                cur = self._db._conn.cursor()

                cur.execute(sql, params)

                cols = [d[0] for d in cur.description]

                rows = [dict(zip(cols, row)) for row in cur.fetchall()]

                cur.close()

            for r in rows:

                for k, v in r.items():

                    if hasattr(v, "isoformat"):

                        r[k] = v.isoformat()

            return {"orders": rows, "total": len(rows)}

        except Exception as exc:

            log.warning("list_orders.error error=%s", exc)

            return {"orders": [], "error": str(exc)}



    def query_ticks(self, ticker: str, limit: int = 100) -> dict:

        if self._db is None or self._db._conn is None:

            return {'ticks': [], 'error': 'DB indisponivel'}

        try:

            sql = ('SELECT time,ticker,exchange,price,quantity,volume,'

                   'buy_agent,sell_agent,trade_number,trade_type,is_edit '

                   'FROM profit_ticks WHERE ticker=%s ORDER BY time DESC LIMIT %s')

            with self._db._lock:

                cur = self._db._conn.cursor()

                cur.execute(sql, (ticker.upper(), limit))

                cols = [d[0] for d in cur.description]

                rows = [dict(zip(cols, r)) for r in cur.fetchall()]

                cur.close()

            for r in rows:

                if hasattr(r.get('time'), 'isoformat'): r['time'] = r['time'].isoformat()

            return {'ticker': ticker.upper(), 'ticks': rows, 'total': len(rows)}

        except Exception as e:

            return {'ticks': [], 'error': str(e)}



    def query_assets(self, search: str='', sector: str='',

                     sec_type: int=0, limit: int=200) -> dict:

        if self._db is None or self._db._conn is None:

            return {'assets': [], 'error': 'DB indisponivel'}

        try:

            conds, params = [], []

            if search:

                conds.append('(ticker ILIKE %s OR name ILIKE %s OR isin ILIKE %s)')

                s = '%' + search.upper() + '%'

                params += [s, s, s]

            if sector:

                conds.append('sector ILIKE %s'); params.append('%' + sector + '%')

            if sec_type:

                conds.append('security_type=%s'); params.append(sec_type)

            where = ('WHERE ' + ' AND '.join(conds)) if conds else ''

            params.append(limit)

            sql = (f'SELECT ticker,exchange,name,description,security_type,'

                   f'lot_size,min_price_increment,isin,sector,sub_sector,segment '

                   f'FROM profit_assets {where} ORDER BY ticker LIMIT %s')

            with self._db._lock:

                cur = self._db._conn.cursor()

                cur.execute(sql, params)

                cols = [d[0] for d in cur.description]

                rows = [dict(zip(cols, r)) for r in cur.fetchall()]

                cur.close()

            return {'assets': rows, 'total': len(rows)}

        except Exception as e:

            return {'assets': [], 'error': str(e)}



    def query_daily_summary(self) -> dict:

        if self._db is None or self._db._conn is None:

            return {'summary': [], 'error': 'DB indisponivel'}

        try:

            tickers = [t for t, _ in self._db.get_subscribed_tickers()]

            if not tickers: return {'summary': [], 'note': 'sem tickers'}

            ph = ','.join(['%s'] * len(tickers))

            sql = (f'SELECT DISTINCT ON (ticker) ticker,exchange,time,'

                   f'open,high,low,close,volume,adjust,qty,trades '

                   f'FROM profit_daily_bars WHERE ticker IN ({ph}) '

                   f'ORDER BY ticker,time DESC')

            with self._db._lock:

                cur = self._db._conn.cursor()

                cur.execute(sql, tickers)

                cols = [d[0] for d in cur.description]

                rows = [dict(zip(cols, r)) for r in cur.fetchall()]

                cur.close()

            for r in rows:

                if hasattr(r.get('time'), 'isoformat'): r['time'] = r['time'].isoformat()

                for k in ('open','high','low','close','volume','adjust'):

                    if r.get(k) is not None: r[k] = float(r[k])

            return {'summary': rows}

        except Exception as e:

            return {'summary': [], 'error': str(e)}



    def get_positions(self, env: str = "simulation") -> dict:

        """Posicao liquida por ticker: soma fills positivos (buy) e negativos (sell)."""

        if self._db is None or self._db._conn is None:

            return {"positions": [], "error": "DB indisponivel"}

        try:

            sql = """

                SELECT ticker, exchange,

                    SUM(CASE WHEN order_side = 1 THEN filled_qty

                             WHEN order_side = 2 THEN -filled_qty ELSE 0 END) AS net_qty,

                    SUM(CASE WHEN order_side = 1 THEN filled_qty * COALESCE(avg_fill_price,0)

                             WHEN order_side = 2 THEN -filled_qty * COALESCE(avg_fill_price,0)

                             ELSE 0 END) AS financial_exposure

                FROM profit_orders

                WHERE env = %s AND order_status IN (1, 2)

                GROUP BY ticker, exchange

                HAVING SUM(CASE WHEN order_side = 1 THEN filled_qty

                                WHEN order_side = 2 THEN -filled_qty ELSE 0 END) != 0

                ORDER BY ticker

            """

            with self._db._lock:

                cur = self._db._conn.cursor()

                cur.execute(sql, (env,))

                cols = [d[0] for d in cur.description]

                rows = [dict(zip(cols, row)) for row in cur.fetchall()]

                cur.close()

            for r in rows:

                for k, v in r.items():

                    if hasattr(v, "__float__"):

                        r[k] = float(v) if v is not None else None

            return {"positions": rows, "env": env}

        except Exception as exc:

            log.warning("get_positions.error error=%s", exc)

            return {"positions": [], "error": str(exc)}



    def list_book(self, ticker: str = "") -> dict:

        """Retorna snapshot atual do book em memoria."""

        if ticker:

            book_data = self._book.get(ticker.upper())

            if not book_data:

                return {"ticker": ticker, "bids": [], "asks": [], "error": "sem dados"}

            def _side(side_dict):

                return [

                    {"position": pos, **data}

                    for pos, data in sorted(side_dict.items())

                ]

            return {

                "ticker": ticker.upper(),

                "bids": _side(book_data.get("bids", {})),

                "asks": _side(book_data.get("asks", {})),

            }

        # Todos os tickers

        result = {}

        for t, book_data in self._book.items():

            def _side(sd):

                return [{"position": p, **d} for p, d in sorted(sd.items())]

            result[t] = {

                "bids": _side(book_data.get("bids", {})),

                "asks": _side(book_data.get("asks", {})),

            }

        return {"book": result, "tickers": list(result.keys())}



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

            "db_connected":       self._db is not None and self._db.is_connected,

        }



    # ------------------------------------------------------------------

    # HTTP Server

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------

    # Coleta de histórico — intercepta V1 + SetSerieProgressCallback

    # ------------------------------------------------------------------

    def collect_history(self, body: dict) -> dict:

        """

        POST /collect_history

        Body: {"ticker":"WINFUT","exchange":"B",

               "dt_start":"09/04/2026 09:00:00",

               "dt_end":"09/04/2026 18:00:00",

               "timeout":180}



        DIAGNÓSTICO: DLL com V1 callback no pos 8 roteia histórico via

        SetTradeCallback (V1), com fim sinalizado por SetSerieProgressCallback

        nProgress=100 — NÃO via SetHistoryTradeCallbackV2/TC_LAST_PACKET.



        Use range máximo de 1 dia (range maior → NL_INVALID_ARGS).

        """

        if not self._market_connected.is_set():

            return {"error": "market_not_connected — aguarde conexao"}



        ticker   = str(body.get("ticker",   "WINFUT")).strip().split(":")[0]

        exchange = str(body.get("exchange", "B"))

        dt_start = str(body.get("dt_start", "09/04/2026 09:00:00"))

        dt_end   = str(body.get("dt_end",   "09/04/2026 18:00:00"))

        timeout  = int(body.get("timeout",  180))



        ERR = {

            0:           "NL_OK",

            -2147483647: "NL_INTERNAL_ERROR",

            -2147483646: "NL_NOT_INITIALIZED",

            -2147483645: "NL_INVALID_ARGS",

            -2147483644: "NL_WAITING_SERVER",

        }

        NL_OK          = 0

        TC_LAST_PACKET = 0x02

        TC_IS_EDIT     = 0x01



        ticks = []

        done  = threading.Event()



        # ── Configura restypes ────────────────────────────────────────────────

        try:

            self._dll.GetHistoryTrades.argtypes  = [c_wchar_p, c_wchar_p,

                                                     c_wchar_p, c_wchar_p]

            self._dll.GetHistoryTrades.restype   = c_int

            self._dll.TranslateTrade.argtypes    = [c_size_t,

                                                     POINTER(TConnectorTrade)]

            self._dll.TranslateTrade.restype     = c_int

            self._dll.SetHistoryTradeCallbackV2.restype = None

            self._dll.SetTradeCallbackV2.restype        = None

            self._dll.SetTradeCallback.restype          = None

            self._dll.SetSerieProgressCallback.restype  = None

            self._dll.SetEnabledHistOrder.argtypes      = [c_int]

            self._dll.SetEnabledHistOrder.restype       = None

        except Exception as e:

            log.warning("collect_history setup_error e=%s", e)



        # ── Callback V2 (SetHistoryTradeCallbackV2 / SetTradeCallbackV2) ──────

        @WINFUNCTYPE(None, TConnectorAssetIdentifier, c_size_t, c_uint)

        def _cb_v2(asset_id, p_trade, flags):

            is_last = bool(flags & TC_LAST_PACKET)

            if not bool(flags & TC_IS_EDIT) and p_trade:

                trade = TConnectorTrade(Version=0)

                if (self._dll.TranslateTrade(p_trade, byref(trade)) == NL_OK

                        and trade.Price > 0):

                    st = trade.TradeDate

                    try:

                        td = datetime(st.wYear, st.wMonth, st.wDay,

                                      st.wHour, st.wMinute, st.wSecond,

                                      tzinfo=timezone.utc)

                    except ValueError:

                        td = datetime(2000, 1, 1, tzinfo=timezone.utc)

                    ticks.append({

                        "src": "v2",

                        "ticker":       asset_id.Ticker or ticker,

                        "trade_date":   td.isoformat(),

                        "trade_number": int(trade.TradeNumber),

                        "price":        trade.Price / 100.0,

                        "quantity":     int(trade.Quantity),

                        "volume":       trade.Volume / 100.0,

                        "trade_type":   int(trade.TradeType),

                        "buy_agent":    int(trade.BuyAgent),

                        "sell_agent":   int(trade.SellAgent),

                    })

                    if len(ticks) % 1000 == 0:

                        log.info("collect_history v2 ticks=%d", len(ticks))

            if is_last:

                log.info("collect_history v2 TC_LAST_PACKET total=%d", len(ticks))

                done.set()



        # ── Callback V1 (SetTradeCallback — sobrepõe pos 8 DLLInitializeLogin)

        # Assinatura TNewTradeCallback (V1):

        # (asset: TAssetID*, date: wchar_p, trade_num: uint, price: double,

        #  vol: double, qty: int, buy: int, sell: int, type: int, edit: char)

        @WINFUNCTYPE(None, c_void_p, c_wchar_p, c_uint, c_double,

                     c_double, c_int, c_int, c_int, c_int, c_char)

        def _cb_v1(asset_ptr, date_str, trade_num, price, vol, qty,

                   buy_agent, sell_agent, trade_type, edit):

            if not asset_ptr or price <= 0:

                return

            try:

                import ctypes as _ct

                asset = _ct.cast(asset_ptr, _ct.POINTER(TAssetID)).contents

                ticker_v1 = asset.ticker or ticker

                # Parse "DD/MM/YYYY HH:mm:SS.ZZZ"

                if date_str and len(date_str) >= 19:

                    try:

                        td = datetime(

                            int(date_str[6:10]),   # year

                            int(date_str[3:5]),    # month

                            int(date_str[0:2]),    # day

                            int(date_str[11:13]),  # hour

                            int(date_str[14:16]),  # minute

                            int(date_str[17:19]),  # second

                            tzinfo=timezone.utc,

                        )

                    except Exception:

                        td = datetime(2000, 1, 1, tzinfo=timezone.utc)

                else:

                    td = datetime(2000, 1, 1, tzinfo=timezone.utc)

                ticks.append({

                    "src":          "v1",

                    "ticker":       ticker_v1,

                    "trade_date":   td.isoformat(),

                    "trade_number": int(trade_num),

                    "price":        price,

                    "quantity":     int(qty),

                    "volume":       vol,

                    "trade_type":   int(trade_type),

                    "buy_agent":    int(buy_agent),

                    "sell_agent":   int(sell_agent),

                })

                if len(ticks) % 1000 == 0:

                    log.info("collect_history v1 ticks=%d", len(ticks))

                # Também encaminha para o pipeline normal (real-time)

                self._total_ticks += 1

                self._db_queue.put_nowait({

                    "_type": "tick",

                    "time": datetime.now(tz=timezone.utc),

                    "ticker": ticker_v1,

                    "exchange": asset.bolsa or "B",

                    "price": price, "quantity": qty,

                    "volume": vol, "buy_agent": buy_agent,

                    "sell_agent": sell_agent,

                    "trade_number": trade_num,

                    "trade_type": trade_type,

                    "is_edit": bool(edit),

                })

            except Exception as e:

                log.debug("collect_history v1 error e=%s", e)



        # ── Progress callback (SetSerieProgressCallback — fim do histórico V1)

        @WINFUNCTYPE(None, TAssetID, c_int)

        def _progress_cb(asset_id, progress):

            log.info("collect_history progress ticker=%s pct=%d",

                     asset_id.ticker or ticker, progress)

            if progress >= 100:

                log.info("collect_history progress=100 → done total=%d", len(ticks))

                done.set()



        # Guarda refs contra GC

        self._hist_cb_v2_ref      = _cb_v2

        self._hist_cb_v1_ref      = _cb_v1

        self._hist_progress_ref   = _progress_cb



        # ── Guarda callbacks originais ────────────────────────────────────────

        orig_trade_v2 = None

        try:

            cbs = getattr(self, '_callbacks', [])

            if len(cbs) > 6:

                orig_trade_v2 = cbs[6]

        except Exception:

            pass

        orig_init_refs = getattr(self, '_init_refs', [])

        orig_v1 = orig_init_refs[0] if orig_init_refs else None



        # ── SetEnabledHistOrder(1) ────────────────────────────────────────────

        try:

            self._dll.SetEnabledHistOrder(c_int(1))

            log.info("collect_history SetEnabledHistOrder(1) OK")

        except Exception as e:

            log.warning("collect_history SetEnabledHistOrder e=%s", e)



        # ── Registra callbacks ────────────────────────────────────────────────

        # V2 (por garantia)

        self._dll.SetHistoryTradeCallbackV2(_cb_v2)

        log.info("collect_history SetHistoryTradeCallbackV2 OK")

        if orig_trade_v2:

            self._dll.SetTradeCallbackV2(_cb_v2)

            log.info("collect_history SetTradeCallbackV2 substituído")



        # V1 — intercepta pos 8 (KEY: é aqui que o DLL entrega histórico)

        self._dll.SetTradeCallback(_cb_v1)

        log.info("collect_history SetTradeCallback(V1) substituído")



        # Progress — detecta fim do histórico V1

        self._dll.SetSerieProgressCallback(_progress_cb)

        log.info("collect_history SetSerieProgressCallback OK")



        # ── GetHistoryTrades ──────────────────────────────────────────────────

        log.info("collect_history GetHistoryTrades ticker=%s %s→%s",

                 ticker, dt_start, dt_end)

        ret = self._dll.GetHistoryTrades(

            c_wchar_p(ticker), c_wchar_p(exchange),

            c_wchar_p(dt_start), c_wchar_p(dt_end),

        )

        ret_name = ERR.get(ret, f"UNKNOWN({ret})")

        log.info("collect_history GetHistoryTrades ret=%d (%s)", ret, ret_name)



        if ret != 0:

            self._restore_callbacks(orig_trade_v2, orig_v1)

            return {"error": f"GetHistoryTrades: {ret_name}", "ret": ret}



        # ── Aguarda TC_LAST_PACKET ou nProgress=100 ───────────────────────────

        received = done.wait(timeout=timeout)

        if not received:

            log.warning("collect_history TIMEOUT ticks=%d", len(ticks))



        # ── Restaura callbacks ────────────────────────────────────────────────

        self._restore_callbacks(orig_trade_v2, orig_v1)



        # ── Persiste em batch (executemany — muito mais rápido) ──────────────
        inserted = 0
        if ticks:
            import os as _os3, psycopg2 as _pg3
            from datetime import datetime as _dtt3

            def _parse_trade_dt(s: str):
                s = s.strip()
                for _fmt in ("%d/%m/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                             "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                    try:
                        return _dtt3.strptime(s, _fmt)
                    except ValueError:
                        pass
                return _dtt3.fromisoformat(s)

            _dsn3 = _os3.getenv(
                "PROFIT_TIMESCALE_DSN",
                "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
            )
            UPSERT3 = """
                INSERT INTO market_history_trades
                    (ticker, trade_date, trade_number, price, quantity, volume,
                     trade_type, buy_agent, sell_agent)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, trade_date, trade_number) DO NOTHING
            """
            rows3 = []
            for _t in ticks:
                try:
                    _td = _parse_trade_dt(_t["trade_date"])
                    rows3.append((
                        _t["ticker"], _td, _t["trade_number"],
                        _t["price"], _t["quantity"], _t["volume"],
                        _t["trade_type"], _t["buy_agent"], _t["sell_agent"]
                    ))
                except Exception as _pe:
                    log.warning("collect_history parse_date_error date=%s e=%s",
                                _t.get("trade_date"), _pe)

            if rows3:
                try:
                    _conn3 = _pg3.connect(_dsn3)
                    _conn3.autocommit = False
                    _cur3  = _conn3.cursor()
                    CHUNK3 = 5000
                    for _i in range(0, len(rows3), CHUNK3):
                        _chunk = rows3[_i:_i+CHUNK3]
                        _cur3.executemany(UPSERT3, _chunk)
                        inserted += len(_chunk)
                        log.info("collect_history batch %d/%d",
                                 min(_i+CHUNK3, len(rows3)), len(rows3))
                    _conn3.commit()
                    _cur3.close()
                    _conn3.close()
                    log.info("collect_history persistido inserted=%d", inserted)
                except Exception as _ie:
                    log.error("collect_history batch_insert_error e=%s", _ie)
                    try:
                        _conn3.rollback()
                        _conn3.close()
                    except Exception:
                        pass
                    inserted = 0
        # Remove campo 'src' dos resultados finais

        clean_ticks = [{k: v for k, v in t.items() if k != 'src'} for t in ticks]

        return {

            "status":   "ok" if received else "timeout",

            "ticks":    len(clean_ticks),

            "inserted": inserted,

            "v1_count": sum(1 for t in ticks if t.get("src") == "v1"),

            "v2_count": sum(1 for t in ticks if t.get("src") == "v2"),

            "first":    clean_ticks[0]  if clean_ticks else None,

            "last":     clean_ticks[-1] if clean_ticks else None,

        }



    def _restore_callbacks(self, orig_trade_v2, orig_v1) -> None:

        """Restaura callbacks originais após collect_history."""

        try:

            if orig_trade_v2:

                self._dll.SetTradeCallbackV2(orig_trade_v2)

                log.info("collect_history SetTradeCallbackV2 restaurado")

        except Exception as e:

            log.warning("collect_history restaurar_v2 e=%s", e)

        try:

            # Restaura V1 original via SetTradeCallbackV2 ou mantém noop

            # (o V1 original estava em _init_refs[2] = _trade_v1_init)

            orig_v1_real = None

            init_refs = getattr(self, '_init_refs', [])

            if len(init_refs) > 1:

                orig_v1_real = init_refs[1]  # _trade_v1_init

            if orig_v1_real:

                self._dll.SetTradeCallback(orig_v1_real)

                log.info("collect_history SetTradeCallback(V1) restaurado")

        except Exception as e:

            log.warning("collect_history restaurar_v1 e=%s", e)

        try:

            # Restaura progress noop

            orig_progress = None

            init_refs = getattr(self, '_init_refs', [])

            if len(init_refs) > 0:

                # _init_refs = [state_cb, trade_v1, daily_v1, progress, tiny, account]

                if len(init_refs) > 3:

                    orig_progress = init_refs[3]  # _noop_progress

            if orig_progress:

                self._dll.SetSerieProgressCallback(orig_progress)

                log.info("collect_history SetSerieProgressCallback restaurado")

        except Exception as e:

            log.warning("collect_history restaurar_progress e=%s", e)



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

                    full = agent._db.list_tickers_full() if agent._db else []

                    self._send_json({"tickers": full, "count": len(full)})

                elif self.path == "/tickers/active":

                    active = agent._db.list_tickers_full(only_active=True) if agent._db else []

                    self._send_json({"tickers": active, "count": len(active)})

                elif self.path == "/health":

                    self._send_json({"ok": True})

                elif self.path == "/history/tickers":

                    tickers = agent._db.list_history_tickers() if agent._db else []

                    self._send_json({"tickers": tickers, "count": len(tickers)})

                elif self.path == "/orders":

                    from urllib.parse import urlparse, parse_qs

                    qs = parse_qs(urlparse(self.path).query)

                    self._send_json(agent.list_orders(

                        ticker=qs.get("ticker",[""])[0],

                        status=qs.get("status",[""])[0],

                        env=qs.get("env",[""])[0],

                        limit=int(qs.get("limit",["100"])[0]),

                    ))

                elif self.path.startswith("/positions"):

                    from urllib.parse import urlparse, parse_qs

                    qs2 = parse_qs(urlparse(self.path).query)

                    self._send_json(agent.get_positions(qs2.get("env",["simulation"])[0]))

                elif self.path.startswith('/ticks/'):

                    from urllib.parse import urlparse, parse_qs as _pqs

                    _p = urlparse(self.path)

                    _tkr = _p.path.split('/ticks/',1)[-1].upper()

                    _ql = int(_pqs(_p.query).get('limit',['100'])[0])

                    self._send_json(agent.query_ticks(_tkr, _ql))

                elif self.path.startswith('/assets/'):

                    _at = self.path.split('/assets/',1)[-1].upper()

                    _ar = agent.query_assets(search=_at, limit=1)

                    self._send_json(_ar['assets'][0] if _ar['assets'] else {'error':'nao encontrado'})

                elif self.path.startswith('/assets'):

                    from urllib.parse import urlparse, parse_qs as _pqs2

                    _aq = _pqs2(urlparse(self.path).query)

                    self._send_json(agent.query_assets(

                        search=_aq.get('search',[''])[0],

                        sector=_aq.get('sector',[''])[0],

                        sec_type=int(_aq.get('type',['0'])[0]),

                        limit=int(_aq.get('limit',['200'])[0]),

                    ))

                elif self.path == '/summary':

                    self._send_json(agent.query_daily_summary())

                elif self.path == '/stream/ticks':

                    import queue as _qmod

                    self.send_response(200)

                    self.send_header('Content-Type','text/event-stream')

                    self.send_header('Cache-Control','no-cache')

                    self.send_header('Connection','keep-alive')

                    self.end_headers()

                    _cq = _qmod.Queue(maxsize=500)

                    with agent._sse_lock:

                        agent._sse_clients.append(_cq)

                    try:

                        while True:

                            try:

                                _d = _cq.get(timeout=15)

                                self.wfile.write(('data: ' + _d + '\n\n').encode())

                                self.wfile.flush()

                            except _qmod.Empty:

                                self.wfile.write(b': heartbeat\n\n')

                                self.wfile.flush()

                    except Exception:

                        pass

                    finally:

                        with agent._sse_lock:

                            try: agent._sse_clients.remove(_cq)

                            except ValueError: pass

                    return

                elif self.path == "/book":



                    self._send_json(agent.list_book())

                elif self.path.startswith("/book/"):

                    tkr = self.path.split("/book/", 1)[-1].upper()

                    self._send_json(agent.list_book(tkr))

                else:

                    self._send_json({"error": "not found"}, 404)



            def do_POST(self):

                body = self._read_body()

                if self.path == "/order/send":

                    self._send_json(agent._send_order_legacy(body))

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

                    # Body: {"ticker":"WINFUT","exchange":"F","active":true,

                    #        "subscribe_book":false,"priority":10,"notes":"..."}

                    if not agent._db:

                        self._send_json({"error": "db_unavailable"}, 503)

                    else:

                        tkr  = body.get("ticker","").upper()

                        exch = body.get("exchange","B").upper()

                        ok = agent._db.upsert_ticker(

                            ticker=tkr, exchange=exch,

                            active=bool(body.get("active", True)),

                            subscribe_book=bool(body.get("subscribe_book", False)),

                            priority=int(body.get("priority", 0)),

                            notes=body.get("notes",""),

                        )

                        # Subscreve em tempo real se active=True

                        if ok and body.get("active", True):

                            agent._subscribe(tkr, exch)

                        self._send_json({"ok": ok, "ticker": tkr, "exchange": exch})

                elif self.path == "/tickers/remove":

                    self._send_json(agent.unsubscribe_ticker(body))

                elif self.path == "/tickers/toggle":

                    if not agent._db:

                        self._send_json({"error": "db_unavailable"}, 503)

                    else:

                        _tkr  = body.get("ticker","").upper()

                        _exch = body.get("exchange","B").upper()

                        _act  = bool(body.get("active", True))

                        _ok   = agent._db.toggle_ticker(_tkr, _exch, _act)

                        if _ok and _act:

                            agent._subscribe(_tkr, _exch)

                        self._send_json({"ok": _ok, "ticker": _tkr, "active": _act})

                elif self.path == "/collect_history":

                    self._send_json(agent.collect_history(body))

                elif self.path == "/history/tickers/add":

                    # Body: {"ticker":"WINFUT","exchange":"F","active":true,

                    #        "collect_from":"2026-01-01 09:00:00","notes":"..."}

                    if not agent._db:

                        self._send_json({"error": "db_unavailable"}, 503)

                    else:

                        ok = agent._db.upsert_history_ticker(

                            body.get("ticker","").upper(),

                            body.get("exchange","B").upper(),

                            bool(body.get("active", True)),

                            body.get("collect_from", "2026-01-01 00:00:00"),

                            body.get("notes", ""),

                        )

                        self._send_json({"ok": ok})

                elif self.path == "/history/tickers/toggle":

                    # Body: {"ticker":"WINFUT","exchange":"F","active":false}

                    if not agent._db:

                        self._send_json({"error": "db_unavailable"}, 503)

                    else:

                        ok = agent._db.toggle_history_ticker(

                            body.get("ticker","").upper(),

                            body.get("exchange","B").upper(),

                            bool(body.get("active", True)),

                        )

                        self._send_json({"ok": ok})

                elif self.path == "/history/collect_all":

                    # Coleta todos os ativos active=True da tabela

                    # Body opcional: {"timeout": 300}

                    if not agent._db:

                        self._send_json({"error": "db_unavailable"}, 503)

                        return

                    active_tickers = agent._db.get_active_history_tickers()

                    if not active_tickers:

                        self._send_json({"error": "no_active_tickers"})

                        return

                    timeout_each = int(body.get("timeout", 180))

                    results = []

                    for tkr, exch, collect_from in active_tickers:

                        from datetime import datetime, timedelta, timezone as _tz

                        # Usa last_collected_to como dt_start se disponível,

                        # senão usa collect_from

                        dt_start = body.get("dt_start",

                            collect_from.strftime("%d/%m/%Y 09:00:00")

                            if hasattr(collect_from, "strftime")

                            else str(collect_from)[:10].replace("-", "/")

                        )

                        dt_end = body.get("dt_end",

                            datetime.now(_tz.utc).strftime("%d/%m/%Y 18:00:00")

                        )

                        r = agent.collect_history({

                            "ticker": tkr, "exchange": exch,

                            "dt_start": dt_start, "dt_end": dt_end,

                            "timeout": timeout_each,

                        })

                        results.append({"ticker": tkr, "exchange": exch, **r})

                    self._send_json({"results": results, "count": len(results)})

                else:

                    self._send_json({"error": "not found"}, 404)



        from http.server import ThreadingHTTPServer

        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)

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





