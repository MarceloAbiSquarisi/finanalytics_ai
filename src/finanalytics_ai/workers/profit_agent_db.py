"""
DB layer do profit_agent — extraido de profit_agent.py em 01/mai/2026.

Pure SQL via psycopg2 sincrono. Zero dependencias de ctypes ou DLL — extrair
DBWriter pra modulo proprio reduz profit_agent.py em ~770 linhas e isola
a camada de persistencia.

Decisoes:
  - psycopg2 importado dinamicamente nos metodos (preserva ordem original
    onde DBWriter so' importava psycopg2 APOS DLL conectar — Winsock).
  - Logger proprio "profit_agent.db" (heranca do logger principal).
  - autocommit=True (paridade com profit_agent.py original).
  - Lock interno protege _conn em modo multithread (ProfitAgent tem multiplas
    threads que escrevem: callback DLL, HTTP server, watchdogs).
  - reconnect com 3 tentativas + backoff progressivo.

Uso:
  from finanalytics_ai.workers.profit_agent_db import DBWriter
  db = DBWriter(dsn)
  db.connect()
  db.insert_tick({...})
"""

from __future__ import annotations

from datetime import UTC, datetime
import logging
import threading
import time

log = logging.getLogger("profit_agent.db")


class DBWriter:
    """Escreve no TimescaleDB usando psycopg2 sincrono."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn = None
        self._lock = threading.Lock()

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
        except Exception as exc:
            # V1 fix (Sprint V1, 21/abr): logar disconnect detectado.
            # Throttle: 1 log a cada 60s para nao spammar quando DB cair.
            now = time.time()
            last = getattr(self, "_last_disconnect_log", 0)
            if now - last > 60:
                log.warning("db.is_connected.failed reason=%s", exc)
                self._last_disconnect_log = now
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
                log.info("db.reconnected attempt=%d dsn=%s", attempt, self._dsn.split("@")[-1])
                return True
            except Exception as e:
                log.warning("db.reconnect_failed attempt=%d error=%s", attempt, e)
                time.sleep(attempt * 2)
        log.error("db.reconnect_giving_up — persistência desativada temporariamente")
        return False

    def execute(self, sql: str, params: tuple = ()) -> bool:
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
            try:
                self._conn.rollback()
            except Exception:
                self._conn = None
            return False

    def fetch_one(self, sql: str, params: tuple = ()) -> tuple | None:
        """Executa SELECT e retorna primeira linha (ou None)."""
        if not self._ensure_connected():
            return None
        try:
            with self._lock:
                cur = self._conn.cursor()
                cur.execute(sql, params)
                row = cur.fetchone() if cur.description else None
                cur.close()
            return row
        except Exception as e:
            log.warning("db.fetch_one_failed error=%s sql=%.100s", e, sql)
            try:
                self._conn.rollback()
            except Exception:
                self._conn = None
            raise

    def fetch_all(self, sql: str, params: tuple = ()) -> list[tuple]:
        """Executa SELECT e retorna todas as linhas como list[tuple]."""
        if not self._ensure_connected():
            return []
        try:
            with self._lock:
                cur = self._conn.cursor()
                cur.execute(sql, params)
                rows = cur.fetchall() if cur.description else []
                cur.close()
            return list(rows)
        except Exception as e:
            log.warning("db.fetch_all_failed error=%s sql=%.100s", e, sql)
            try:
                self._conn.rollback()
            except Exception:
                self._conn = None
            raise

    # ── Domain DAO methods ──────────────────────────────────────────────────

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
        self.execute(
            sql,
            (
                data.get("ticker", ""),
                data.get("exchange", "B"),
                data.get("name"),
                data.get("description"),
                data.get("security_type"),
                data.get("security_subtype"),
                data.get("min_order_qty"),
                data.get("max_order_qty"),
                data.get("lot_size"),
                data.get("min_price_increment"),
                data.get("contract_multiplier"),
                data.get("valid_date"),
                data.get("isin"),
                data.get("sector"),
                data.get("sub_sector"),
                data.get("segment"),
                data.get("feed_type", 0),
            ),
        )

    def insert_tick(self, data: dict) -> None:
        sql = """
        INSERT INTO profit_ticks
            (time, ticker, exchange, price, quantity, volume,
             buy_agent, sell_agent, trade_number, trade_type, is_edit)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """
        self.execute(
            sql,
            (
                data["time"],
                data["ticker"],
                data.get("exchange", "B"),
                data["price"],
                data["quantity"],
                data.get("volume"),
                data.get("buy_agent"),
                data.get("sell_agent"),
                data.get("trade_number"),
                data.get("trade_type"),
                data.get("is_edit", False),
            ),
        )

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
        self.execute(
            sql,
            (
                data["time"],
                data["ticker"],
                data.get("exchange", "B"),
                data.get("open"),
                data.get("high"),
                data.get("low"),
                data.get("close"),
                data.get("volume"),
                data.get("adjust"),
                data.get("max_limit"),
                data.get("min_limit"),
                data.get("vol_buyer"),
                data.get("vol_seller"),
                data.get("qty"),
                data.get("trades"),
                data.get("open_contracts"),
                data.get("qty_buyer"),
                data.get("qty_seller"),
                data.get("neg_buyer"),
                data.get("neg_seller"),
            ),
        )

    def insert_order(self, data: dict) -> None:
        sql = """
        INSERT INTO profit_orders
            (local_order_id, message_id, cl_ord_id, broker_id, account_id, sub_account_id,
             env, ticker, exchange, order_type, order_side, price, stop_price,
             quantity, order_status, user_account_id, portfolio_id, is_daytrade,
             strategy_id, notes, validity_type, validity_date, source)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,10,%s,%s,%s,%s,%s,%s,%s,%s)
        """
        self.execute(
            sql,
            (
                data.get("local_order_id"),
                data.get("message_id"),
                data.get("cl_ord_id"),  # C5: client_order_id deterministico do engine
                data["broker_id"],
                data["account_id"],
                data.get("sub_account_id"),
                data.get("env", "simulation"),
                data["ticker"],
                data.get("exchange", "B"),
                data["order_type"],
                data["order_side"],
                data.get("price"),
                data.get("stop_price"),
                data["quantity"],
                data.get("user_account_id"),
                data.get("portfolio_id"),
                data.get("is_daytrade", False),
                data.get("strategy_id"),
                data.get("notes"),
                data.get("validity_type", "GTC"),
                data.get("validity_date"),
                data.get("source"),  # C5: 'trading_engine' p/ ordens do robo
            ),
        )

    def update_agent_status(self, data: dict) -> None:
        sql = """
        UPDATE profit_agent_status SET
            last_heartbeat=%s, is_connected=%s,
            market_connected=%s, routing_connected=%s,
            subscribed_tickers=%s, total_ticks=%s, total_orders=%s
        WHERE id=1
        """
        self.execute(
            sql,
            (
                datetime.now(tz=UTC),
                data.get("is_connected", False),
                data.get("market_connected", False),
                data.get("routing_connected", False),
                data.get("subscribed_tickers", []),
                data.get("total_ticks", 0),
                data.get("total_orders", 0),
            ),
        )

    # ── Subscribed tickers (live feed) ──────────────────────────────────────

    def ensure_tickers_table(self) -> None:
        """Cria/migra tabela de tickers subscritos."""
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

        migrations = [
            "ALTER TABLE profit_subscribed_tickers ADD COLUMN IF NOT EXISTS subscribe_book BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE profit_subscribed_tickers ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE profit_subscribed_tickers ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE profit_subscribed_tickers ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
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
            return [
                dict(zip(cols, [str(v) if hasattr(v, "isoformat") else v for v in row]))
                for row in rows
            ]
        except Exception as e:
            log.warning("db.list_tickers_full error=%s", e)
            return []

    def upsert_ticker(
        self,
        ticker: str,
        exchange: str = "B",
        active: bool = True,
        subscribe_book: bool = False,
        priority: int = 0,
        notes: str = "",
    ) -> bool:
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
        return self.execute(
            sql, (ticker.upper(), exchange.upper(), active, subscribe_book, priority, notes)
        )

    def toggle_ticker(self, ticker: str, exchange: str, active: bool) -> bool:
        """Ativa ou desativa subscrição de um ticker."""
        return self.execute(
            """
            UPDATE profit_subscribed_tickers
            SET active = %s, updated_at = NOW()
            WHERE ticker = %s AND exchange = %s
            """,
            (active, ticker.upper(), exchange.upper()),
        )

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
                    parts = t.split(":")
                    tkr = parts[0].strip().upper()
                    exch = parts[1].strip().upper() if len(parts) > 1 else "B"
                    is_future = exch == "F" or tkr.endswith("FUT")
                    self.upsert_ticker(
                        ticker=tkr,
                        exchange=exch,
                        active=True,
                        subscribe_book=False,
                        priority=10 if is_future else 5,
                        notes="Seeded from .env",
                    )
                log.info("db.tickers_seeded_from_env count=%d", len(tickers))
        except Exception as e:
            log.warning("db.seed_tickers_failed error=%s", e)

    # ── History tickers (backfill config) ───────────────────────────────────

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
            return [
                dict(zip(cols, [str(v) if hasattr(v, "isoformat") else v for v in row]))
                for row in rows
            ]
        except Exception as e:
            log.warning("db.list_history_tickers error=%s", e)
            return []

    def upsert_history_ticker(
        self,
        ticker: str,
        exchange: str,
        active: bool = True,
        collect_from: str = "2026-01-01 00:00:00",
        notes: str = "",
    ) -> bool:
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
        return self.execute(sql, (ticker.upper(), exchange.upper(), active, collect_from, notes))

    def toggle_history_ticker(self, ticker: str, exchange: str, active: bool) -> bool:
        """Ativa ou desativa a coleta de um ticker."""
        sql = """
        UPDATE profit_history_tickers
        SET active = %s, updated_at = NOW()
        WHERE ticker = %s AND exchange = %s
        """
        return self.execute(sql, (active, ticker.upper(), exchange.upper()))

    def update_history_ticker_collected(
        self, ticker: str, exchange: str, dt_start: str, dt_end: str, tick_count: int
    ) -> bool:
        """Atualiza metadados da última coleta bem-sucedida.

        dt_start/dt_end: ISO timestamps; Postgres TIMESTAMPTZ aceita string direto.
        """
        sql = """
        UPDATE profit_history_tickers SET
            last_collected_at   = NOW(),
            last_collected_from = %s,
            last_collected_to   = %s,
            last_tick_count     = %s,
            updated_at          = NOW()
        WHERE ticker = %s AND exchange = %s
        """
        return self.execute(
            sql, (dt_start, dt_end, tick_count, ticker.upper(), exchange.upper())
        )
