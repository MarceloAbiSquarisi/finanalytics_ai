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
from ctypes import (
    POINTER,
    WINFUNCTYPE,
    Structure,
    WinDLL,
    byref,
    c_bool,
    c_char,
    c_double,
    c_int,
    c_int64,
    c_long,
    c_longlong,
    c_size_t,
    c_ubyte,
    c_uint,
    c_ushort,
    c_void_p,
    c_wchar,
    c_wchar_p,
)
from datetime import UTC, date, datetime
from http.server import BaseHTTPRequestHandler
import json
import logging
import logging.handlers
import os
from pathlib import Path
import queue
import signal
import sys
import threading
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------

# Dotenv (sem pydantic-settings — stdlib pura para nao ativar Winsock)

# ---------------------------------------------------------------------------


def _load_env(path: str) -> None:
    # .env do projeto sobrescreve env do sistema/NSSM (igual python-dotenv com override=True).
    # Antes: `if k not in os.environ` deixava NSSM service herdar valores stale do shell
    # de install (caso 29/abr: PROFIT_SIM_BROKER_ID=15011 antigo persistia mesmo com .env=32003).
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()

                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)

                    k = k.strip()

                    v = v.strip().strip('"').strip("'")

                    os.environ[k] = v

    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------

# Logging configurado para arquivo antes de qualquer import

# ---------------------------------------------------------------------------


def _setup_logging() -> None:

    log_file = os.getenv(
        "PROFIT_LOG_FILE", r"D:\Projetos\finanalytics_ai_fresh\logs\profit_agent.log"
    )

    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    # RotatingFileHandler: 10MB por arquivo, 10 backups (~110MB max).
    # Antes era FileHandler puro -> arquivo crescia indefinidamente
    # (observado 666MB em 28-29/abr).
    rotating = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            rotating,
            logging.StreamHandler(sys.stdout),
        ],
    )


log = logging.getLogger("profit_agent")


def _hard_exit(code: int = 0) -> None:
    """Mata processo + threads C nativas (DLL ConnectorThread).

    os._exit(0) deixa ConnectorThread vivo em background, criando "zombie pair"
    (parent+child relaunched pelo NSSM, ambos bind na mesma porta). TerminateProcess
    via Windows API encerra processo INTEIRO incluindo threads nativas, evitando
    duplicidade de listeners em :8002.

    Sessão 30/abr (I4): log antes/depois com GetLastError quando TerminateProcess
    falha. Antes era silent — `/agent/restart` aparecia OK no proxy mas processo
    continuava vivo (validado: PID 116820 sobreviveu a 2 chamadas de /restart).
    Causa raiz hipotética: serviço NSSM rodando como Local System e a thread
    Python sem privilege "Process Termination" sobre o próprio handle — embora
    GetCurrentProcess retorne pseudo-handle, TerminateProcess pode falhar com
    ERROR_ACCESS_DENIED (5) em ACL stricta.
    """
    pid = os.getpid()
    if os.name == "nt":
        try:
            import ctypes as _ct
            from ctypes import wintypes as _wt

            kernel32 = _ct.windll.kernel32
            kernel32.GetCurrentProcess.restype = _wt.HANDLE
            kernel32.TerminateProcess.argtypes = [_wt.HANDLE, _wt.UINT]
            kernel32.TerminateProcess.restype = _wt.BOOL
            kernel32.GetLastError.restype = _wt.DWORD
            log.warning("hard_exit.attempt pid=%d code=%d", pid, code)
            handle = kernel32.GetCurrentProcess()
            ok = kernel32.TerminateProcess(handle, code)
            if not ok:
                err = kernel32.GetLastError()
                log.error(
                    "hard_exit.terminate_failed pid=%d ret=%d last_error=%d "
                    "fallback=os._exit (likely will leave DLL ConnectorThread "
                    "alive — Restart-Service manual required)",
                    pid,
                    ok,
                    err,
                )
            # Se TerminateProcess teve sucesso, não voltamos daqui; se falhou,
            # cai em os._exit logado (NSSM ainda pode reiniciar mesmo zombie).
        except Exception as exc:
            log.exception("hard_exit.exception pid=%d error=%s", pid, exc)
        os._exit(code)
    else:
        log.warning("hard_exit.posix pid=%d code=%d", pid, code)
        os._exit(code)


def _kill_zombie_agents(self_pid: int, port: int) -> int:
    """Tenta matar zombies com filtros conservadores para evitar matar o próprio
    parent NSSM e causar loop infinito de restart.

    Heurística:
      1. Skip se port já está livre (não há ninguém pra matar).
      2. Skip se único listener é o próprio self_pid.
      3. Para cada zombie candidato, valida que NÃO é processo Python recente
         (PID com start time < 5s = pode ser nosso parent NSSM gerando agent novo).
      4. Mata apenas se passar todos os filtros.
    """
    if os.name != "nt":
        return 0
    try:
        import subprocess as _sp

        result = _sp.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        zombie_pids = set()
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 5 and "LISTENING" in line and f":{port}" in parts[1]:
                pid = int(parts[-1])
                if pid != self_pid and pid > 0:
                    zombie_pids.add(pid)
        if not zombie_pids:
            return 0
        # Conservador: apenas detecta e log; não mata.
        # Em prod, um único listener basta — port bind do agent novo falha
        # naturalmente se outro estiver vivo, NSSM tenta de novo.
        log.warning(
            "profit_agent.zombie_detected pids=%s (skip kill — port bind decide)",
            sorted(zombie_pids),
        )
        return 0
    except Exception as exc:
        log.warning("profit_agent.zombie_scan_failed err=%s", exc)
        return 0


# ---------------------------------------------------------------------------

# Tipos ctypes (manual Nelogica)

# ---------------------------------------------------------------------------


# Tipos ctypes movidos pra profit_agent_types.py em 01/mai/2026.
# Re-export preserva API publica (profit_agent.TConnectorOrder etc. continuam funcionando).
# Sessão 30/abr: validators puros movidos para profit_agent_validators.py
# (CI Linux pode testar sem importar ctypes.WINFUNCTYPE Windows-only).
from finanalytics_ai.infrastructure.market_data.kafka_producer import (  # noqa: E402
    MarketDataProducer,
)
from finanalytics_ai.workers.profit_agent_types import (  # noqa: E402, F401
    _TRADING_RESULT_STATUS,
    SystemTime,
    TAssetID,
    TConnectorAccountIdentifier,
    TConnectorAccountIdentifierOut,
    TConnectorAssetIdentifier,
    TConnectorCancelAllOrders,
    TConnectorCancelOrder,
    TConnectorChangeOrder,
    TConnectorEnumerateOrdersProc,
    TConnectorOrder,
    TConnectorOrderIdentifier,
    TConnectorPriceGroup,
    TConnectorSendOrder,
    TConnectorTrade,
    TConnectorTradingAccountPosition,
    TConnectorTradingMessageResult,
    TConnectorZeroPosition,
)
from finanalytics_ai.workers.profit_agent_validators import (  # noqa: E402
    compute_trading_result_match,
    trail_should_immediate_trigger,
    validate_attach_oco_params as _validate_attach_oco_params,
)

# ---------------------------------------------------------------------------

# Constantes (manual pag. 13)

# ---------------------------------------------------------------------------

CONN_STATE_LOGIN = 0

CONN_STATE_ROUTING = 1

CONN_STATE_MARKET_DATA = 2

CONN_STATE_MARKET_LOGIN = 3


LOGIN_CONNECTED = 0

MARKET_CONNECTED = 4

ACTIVATE_VALID = 0

ROUTING_BROKER_CONNECTED = 5


ORDER_TYPE_MARKET = 1

ORDER_TYPE_LIMIT = 2

ORDER_TYPE_STOP_LIMIT = 4


ORDER_SIDE_BUY = 1

ORDER_SIDE_SELL = 2


POSITION_TYPE_DAYTRADE = 1

POSITION_TYPE_CONSOLIDATED = 2


PG_IS_THEORIC = 1


# Códigos CME-style de mês usados nos contratos futuros B3.
# Aplicado em WDO (mensal) e WIN (bimestre par).
MONTH_CODE = {
    1: "F",
    2: "G",
    3: "H",
    4: "J",
    5: "K",
    6: "M",
    7: "N",
    8: "Q",
    9: "U",
    10: "V",
    11: "X",
    12: "Z",
}

# Aliases genéricos de futuros que precisam ser resolvidos para o contrato vigente
# antes de chamar SubscribeTicker/SendOrder.
# Mensal: vencimento todos os meses (WDO/DOL/BGI/OZ usam any month code).
# Bimestre par: vencimento G/J/M/Q/V/Z (WIN/IND).
# Específico: CCM (Milho) tem meses específicos (G/H/K/N/U/X) — não 100% mas próximo.
FUTURES_ALIASES = {
    "WDOFUT",
    "WINFUT",
    "DOLFUT",
    "INDFUT",
    "BGIFUT",
    "OZMFUT",
    "CCMFUT",
}
FUTURES_BIMESTER_EVEN = {"WINFUT", "INDFUT"}  # G/J/M/Q/V/Z (mes par)
FUTURES_MONTHLY = {"WDOFUT", "DOLFUT", "BGIFUT", "OZMFUT"}  # qualquer mes
FUTURES_CCM_MONTHS = {1, 3, 5, 7, 9, 11}  # CCM Milho (F/H/K/N/U/X)


# ---------------------------------------------------------------------------

# DB Writer (psycopg2 sincrono — importado APOS DLL conectar)

# ---------------------------------------------------------------------------


# DBWriter movido pra profit_agent_db.py em 01/mai/2026 (sessao limpeza).
# Re-export pra preservar API original (profit_agent.DBWriter ainda funciona).
from finanalytics_ai.workers.profit_agent_db import DBWriter  # noqa: E402, F401

# ---------------------------------------------------------------------------


class ProfitAgent:
    """Agente principal — gerencia DLL, callbacks e fila de DB."""

    def __init__(self) -> None:

        self._dll: WinDLL | None = None

        self._db: DBWriter | None = None

        self._db_queue: queue.Queue = queue.Queue(maxsize=50_000)

        # Diário hook: timeframe enviado pelo dashboard por local_order_id +
        # set de IDs já notificados (idempotência local; backend tem UNIQUE)
        self._tf_by_local_id: dict[int, str | None] = {}
        self._diary_notified: set[int] = set()
        # B.18 (P4-aware): cache de último status visto por local_order_id, alimentado
        # pelo `get_positions_dll` (loop 500ms). Permite detectar transição
        # (qualquer)→FILLED e disparar `_maybe_dispatch_diary` sem depender do
        # callback antigo (que agora só recebe OrderIdentifier — P4 fix).
        self._last_seen_status: dict[int, int] = {}
        self._diary_url = os.getenv(
            "PROFIT_DIARY_HOOK_URL", "http://localhost:8000/api/v1/diario/from_fill"
        )
        self._diary_user_id = os.getenv("PROFIT_DIARY_USER_ID", "user-demo")

        # Estado de conexao

        self._market_connected = threading.Event()

        self._routing_connected = threading.Event()

        self._state_lock = threading.Lock()

        self._login_ok = False

        self._market_ok = False

        self._routing_ok = False

        self._activate_ok = False

        # P1 (28/abr): auto-retry para "Cliente não logado" (status=204).
        # Padrão observado em log Delphi: rejeição broker → reconnect → retry → success.
        # Mapeia local_id → {params, attempts}; max 3 tentativas (1 original + 2 retries).
        self._retry_params: dict[int, dict] = {}
        # Fallback: r.OrderID.LocalOrderID em trading_msg_cb pode vir 0 por struct
        # mismatch dependendo do code. Mapeamos message_id -> local_id ao enviar
        # (message_id é confiavelmente populado).
        self._msg_id_to_local: dict[int, int] = {}
        self._retry_lock = threading.Lock()

        # Contadores

        self._total_ticks = 0
        # D6 (24/abr): DLL retorna 0 em SubscribeTicker mesmo para ticker inexistente
        # — precisamos rastrear ticks recebidos para validar. Chave "TICKER:EXCHANGE".
        self._last_tick_at: dict[str, datetime] = {}

        self._total_orders = 0

        self._total_assets = 0

        # Métricas Prometheus (/metrics endpoint)
        self._total_probes = 0
        self._total_contaminations = 0
        self._probe_duration_sum_s = 0.0
        self._probe_duration_count = 0
        self._probes_lock = threading.Lock()

        self._book: dict = {}
        # OCO Phase C (26/abr): cache last price por ticker — alimentado por new_trade_cb
        self._last_prices: dict[str, float] = {}

        self._sse_clients: list = []

        self._sse_lock = __import__("threading").Lock()

        # Tickers subscritos

        self._subscribed: set[str] = set()

        # Contas descobertas via accountCallback: {broker_name: (broker_id, account_id)}

        self._discovered_accounts: dict = {}

        self._stop_event = threading.Event()

        # P9 mitigation: watch_pending_orders registry
        # Quando _send_order_legacy aceita ordem, adiciona aqui.
        # _watch_pending_orders_loop varre, atualiza DB com status final via
        # EnumerateAllOrders, marca orphan se DLL nao enumera mais e DB stuck.
        self._pending_orders: dict[int, dict] = {}
        self._pending_lock = threading.RLock()

        # Producer Kafka (C1 - market_data.ticks.v1). Lazy init via
        # PROFIT_KAFKA_BOOTSTRAP — sem essa env, fica desabilitado (noop).
        # Backward compatible: instalacoes que ainda nao querem Kafka nao precisam mudar nada.
        self._kafka_producer = MarketDataProducer.from_env()

        # Refs de callbacks (evita GC)

        self._callbacks: list = []

        # Config

        self._dll_path = os.getenv("PROFIT_DLL_PATH", r"C:\Nelogica\ProfitDLL.dll")

        self._act_key = os.getenv("PROFIT_ACTIVATION_KEY", "")

        self._username = os.getenv("PROFIT_USERNAME", "")

        self._password = os.getenv("PROFIT_PASSWORD", "")

        self._ts_dsn = os.getenv(
            "PROFIT_TIMESCALE_DSN",
            "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
        )

        _sim_bid = os.getenv("PROFIT_SIM_BROKER_ID", "0") or "0"

        self._sim_broker = int(_sim_bid) if _sim_bid.lstrip("-").isdigit() else 0

        self._sim_broker_str = _sim_bid

        self._sim_account = os.getenv("PROFIT_SIM_ACCOUNT_ID", "")

        self._sim_pass = os.getenv("PROFIT_SIM_ROUTING_PASSWORD", "")

        _prod_bid = os.getenv("PROFIT_PROD_BROKER_ID", "0") or "0"

        self._prod_broker = int(_prod_bid) if _prod_bid.lstrip("-").isdigit() else 0

        self._prod_broker_str = _prod_bid

        self._prod_account = os.getenv("PROFIT_PROD_ACCOUNT_ID", "")

        self._prod_pass = os.getenv("PROFIT_PROD_ROUTING_PASSWORD", "")

        raw_tickers = os.getenv("PROFIT_SUBSCRIBE_TICKERS", "")

        self._subscribe_tickers = [t.strip().upper() for t in raw_tickers.split(",") if t.strip()]

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

        from ctypes import WINFUNCTYPE as _WF, c_double as _cd, c_int as _ci, c_void_p as _cv

        @_WF(None, _ci, _ci)
        def _state_cb_init(conn_type: int, result: int) -> None:

            # MINIMAL — sem lock, sem queue durante init (diagnostico prova que

            # with self._state_lock bloqueia result=4 de ser entregue)

            if conn_type == CONN_STATE_MARKET_DATA:
                self._market_ok = result == MARKET_CONNECTED

                if result == MARKET_CONNECTED:
                    self._market_connected.set()

            elif conn_type == CONN_STATE_LOGIN:
                self._login_ok = result == LOGIN_CONNECTED

            elif conn_type == CONN_STATE_MARKET_LOGIN:
                self._activate_ok = result == ACTIVATE_VALID

            elif conn_type == CONN_STATE_ROUTING:
                self._routing_ok = result == ROUTING_BROKER_CONNECTED

        # Callbacks V1 REAIS para DLLInitializeLogin — padrao do teste 11 que funcionou.

        # CRITICO: a DLL precisa de callbacks reais (nao noops) para completar

        # a conexao de market data e entregar result=4 (MARKET_CONNECTED).

        # Os callbacks V2 (SetTradeCallbackV2 etc.) registrados antes continuam

        # sendo os receptores principais — estes V1 apenas satisfazem a DLL na init.

        from ctypes import c_char as _cc, c_uint as _cu, c_wchar_p as _cwp

        @_WF(None, _cv, _cwp, _cu, _cd, _cd, _ci, _ci, _ci, _ci, _cc)
        def _trade_v1_init(
            asset_ptr, date, trade_num, price, vol, qty, buy_agent, sell_agent, trade_type, edit
        ):

            if not asset_ptr:
                return

            try:
                import ctypes as _ct2

                asset_id = _ct2.cast(asset_ptr, _ct2.POINTER(TAssetID)).contents

                ticker = asset_id.ticker or ""

                if not ticker:
                    return

                from datetime import datetime

                self._total_ticks += 1
                _exch_legacy = asset_id.bolsa or "B"
                self._last_tick_at[f"{ticker}:{_exch_legacy}"] = datetime.now(tz=UTC)

                self._db_queue.put_nowait(
                    {
                        "_type": "tick",
                        "time": datetime.now(tz=UTC),
                        "ticker": ticker,
                        "exchange": _exch_legacy,
                        "price": price,
                        "quantity": qty,
                        "volume": vol,
                        "buy_agent": buy_agent,
                        "sell_agent": sell_agent,
                        "trade_number": trade_num,
                        "trade_type": trade_type,
                        "is_edit": bool(edit),
                    }
                )

                if self._total_ticks <= 5:
                    log.info("TICK_V1 ticker=%s price=%s qty=%s", ticker, price, qty)

            except Exception as exc:
                # V1 fix (Sprint V1, 21/abr): warn em vez de pass silencioso.
                # Throttle: 1 log por 1000 ticks com erro para evitar spam.
                self._tick_v1_errors = getattr(self, "_tick_v1_errors", 0) + 1
                if self._tick_v1_errors % 1000 == 1:
                    log.warning("TICK_V1 callback error (count=%d): %s", self._tick_v1_errors, exc)

        @_WF(
            None,
            _cv,
            _cwp,
            _cd,
            _cd,
            _cd,
            _cd,
            _cd,
            _cd,
            _cd,
            _cd,
            _cd,
            _cd,
            _ci,
            _ci,
            _ci,
            _ci,
            _ci,
            _ci,
            _ci,
        )
        def _daily_v1_init(
            asset_ptr,
            date,
            s_open,
            s_high,
            s_low,
            s_close,
            s_vol,
            s_ajuste,
            s_max_lim,
            s_min_lim,
            s_vol_buyer,
            s_vol_seller,
            n_qty,
            n_neg,
            n_contratos,
            n_qty_buyer,
            n_qty_seller,
            n_neg_buyer,
            n_neg_seller,
        ):

            pass  # daily V2 cuida dos dados reais

        _noop_progress = _WF(None, _cv, _ci)(lambda p, v: None)

        _noop_tiny = _WF(None, _cv, _cd, _ci, _ci)(lambda *a: None)

        @_WF(None, _ci, c_wchar_p, c_wchar_p, c_wchar_p)
        def _account_cb_init(bid, bname, aid, owner):

            name = (bname or "").upper()

            acc = (aid or "").strip()

            log.info(
                "account broker_id=%d broker_name=%s account=%s owner=%s", bid, name, acc, owner
            )

            self._discovered_accounts[name] = (bid, acc)

            self._discovered_accounts[acc] = (bid, acc)

        # Guarda refs contra GC

        self._init_refs = [
            _state_cb_init,
            _trade_v1_init,
            _daily_v1_init,
            _noop_progress,
            _noop_tiny,
            _account_cb_init,
        ]

        # 3. Configura restypes ANTES de qualquer chamada

        self._setup_dll_restypes()

        # 5. DLLInitializeLogin com noops V1 — padrao dos testes que funcionaram

        log.info("profit_agent.initializing_market_data")

        ret_md = self._dll.DLLInitializeLogin(
            c_wchar_p(self._act_key),
            c_wchar_p(self._username),
            c_wchar_p(self._password),
            _state_cb_init,  # state
            None,  # history
            None,  # order_change
            _account_cb_init,  # account
            _trade_v1_init,  # new_trade V1 REAL (necessario para result=4)
            _daily_v1_init,  # new_daily V1 REAL (necessario para result=4)
            None,  # price_book
            None,  # offer_book
            None,  # history_trade
            _noop_progress,  # progress
            _noop_tiny,  # tiny_book
        )

        if ret_md != 0:
            log.error("profit_agent.dll_init_failed ret=%d", ret_md)

            sys.exit(1)

        log.info("profit_agent.dll_initialized market_ret=%d", ret_md)

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
                (datetime.now(tz=UTC), "1.0.0"),
            )

            # Garante tabela e migra tickers do .env (apenas se tabela vazia)

            self._db.ensure_tickers_table()

            self._db.ensure_history_tickers_table()

            # Seed padrão: WINFUT e WDOFUT (futuros, exchange=F)

            self._db.upsert_history_ticker(
                "WINFUT",
                "F",
                active=True,
                collect_from="2026-01-01 00:00:00",
                notes="Mini Ibovespa Futuro",
            )

            self._db.upsert_history_ticker(
                "WDOFUT",
                "F",
                active=True,
                collect_from="2026-01-01 00:00:00",
                notes="Mini Dólar Futuro",
            )

            self._db.ensure_history_tickers_table()

            # Seed padrão: WINFUT e WDOFUT (futuros, exchange=F)

            self._db.upsert_history_ticker(
                "WINFUT",
                "F",
                active=True,
                collect_from="2026-01-01 00:00:00",
                notes="Mini Ibovespa Futuro",
            )

            self._db.upsert_history_ticker(
                "WDOFUT",
                "F",
                active=True,
                collect_from="2026-01-01 00:00:00",
                notes="Mini Dólar Futuro",
            )

            self._db.seed_tickers_from_env(self._subscribe_tickers)

        else:
            # NAO setar self._db = None — mantem o DBWriter instanciado para permitir
            # reconect lazy via _ensure_connected() nos handlers. Persistencia fica
            # desativada temporariamente, reativa automaticamente quando DB voltar.
            log.warning("profit_agent.db_initial_connect_failed continuing_with_lazy_reconnect")

        # 6. Inicia worker de DB em thread separada

        db_thread = threading.Thread(target=self._db_worker, daemon=True)

        db_thread.start()

        # OCO monitor thread — auto-cancela perna oposta quando uma executa
        self._oco_pairs = {}
        _oco_thread = threading.Thread(target=self._oco_monitor_loop, daemon=True)
        _oco_thread.start()
        log.info("profit_agent.oco_monitor_started")

        # P10 fix: reload pares OCO legacy do DB (sem isso, restart deixava
        # SL orfao porque _oco_pairs in-memory zerava). Roda antes do monitor
        # processar primeiro tick.
        try:
            n_legacy = self._load_oco_legacy_pairs_from_db()
            if n_legacy:
                log.info("profit_agent.oco_legacy_pairs_loaded n=%d", n_legacy)
        except Exception as exc:
            log.warning("profit_agent.oco_legacy_load_failed err=%s", exc)

        # OCO multi-level (Phase A) — groups com N levels e parent attach
        self._oco_groups = {}
        self._order_to_group = {}
        # Phase D: recarrega groups awaiting/active/partial do DB ANTES da thread subir
        try:
            n_loaded = self._load_oco_state_from_db()
            log.info("profit_agent.oco_groups_loaded n=%d", n_loaded)
        except Exception as exc:
            log.warning("profit_agent.oco_load_failed err=%s (continuando vazio)", exc)
        _oco_grp_thread = threading.Thread(target=self._oco_groups_monitor_loop, daemon=True)
        _oco_grp_thread.start()
        log.info("profit_agent.oco_groups_monitor_started")

        # OCO Phase C (Trailing) — thread separada @ 1s pra ratchet de SL
        _oco_trail_thread = threading.Thread(target=self._trail_monitor_loop, daemon=True)
        _oco_trail_thread.start()
        log.info("profit_agent.oco_trail_monitor_started")

        # Watch pending orders — mitigação P9: detecta status final de ordens
        # mesmo quando callback de status nao chega (broker degradado).
        # Sessão 30/abr: pre-popula _pending_orders com ordens em status pendente
        # nas últimas N horas. Antes, restart do agent perdia o registry e órfãs
        # ficavam fora do watch até cleanup_stale_pending_orders_job (1×/dia).
        self._load_pending_orders_from_db()
        _watch_thread = threading.Thread(target=self._watch_pending_orders_loop, daemon=True)
        _watch_thread.start()
        log.info("profit_agent.watch_pending_orders_started")

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

        http_thread = threading.Thread(target=self._start_http, args=(http_port,), daemon=True)

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
            ("SetTradeCallbackV2", 6),
            ("SetDailyCallback", 3),
            ("SetAssetListInfoCallbackV2", 7),
            ("SetAssetListCallback", 8),
            ("SetAdjustHistoryCallbackV2", 9),
            ("SetPriceDepthCallback", 10),
            ("SetOrderCallback", 11),
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

    def _resolve_active_contract(self, ticker: str, exchange: str = "F") -> str:
        """Resolve alias de futuros (WDOFUT/WINFUT) para código vigente.

        Algoritmo determinístico baseado na data atual:
          - WDO (mensal): front month = mês corrente + 1 (ex: hoje=29/abr → WDOK26).
            Após o roll (1º útil do mês), reflete o novo front naturalmente.
          - WIN (bimestre par G/J/M/Q/V/Z): próximo mês par >= today.month, com
            avanço se today.day > 15 (vencimento ~ quarta próxima do dia 15).

        Self-healing: gera 3 candidatos sequenciais. Retorna o primeiro que está
        em self._subscribed (cobre edge cases de roll). Se nenhum subscrito,
        retorna o primeiro candidato (caller decide se subscreve ou rejeita).

        Tickers fora de FUTURES_ALIASES (ex: PETR4, WDOK26 já específico)
        passam direto sem alteração.
        """
        if ticker not in FUTURES_ALIASES:
            return ticker

        today = date.today()
        yy = today.year % 100
        candidates: list[str] = []

        # Mensais (todos os meses): WDO/DOL/BGI/OZM
        if ticker in FUTURES_MONTHLY:
            prefix = ticker[:3]  # WDO / DOL / BGI / OZM (3 chars)
            for offset in (1, 2, 3):
                m = today.month + offset
                y = yy
                while m > 12:
                    m -= 12
                    y += 1
                candidates.append(f"{prefix}{MONTH_CODE[m]}{y:02d}")

        # Bimestre par G/J/M/Q/V/Z: WIN/IND
        elif ticker in FUTURES_BIMESTER_EVEN:
            prefix = ticker[:3]  # WIN / IND
            m = today.month
            if m % 2 != 0:
                m += 1
            elif today.day > 15:
                m += 2
            for _ in range(3):
                y = yy
                cur_m = m
                while cur_m > 12:
                    cur_m -= 12
                    y += 1
                candidates.append(f"{prefix}{MONTH_CODE[cur_m]}{y:02d}")
                m += 2

        # CCM (Milho): meses F/H/K/N/U/X (jan/mar/mai/jul/set/nov)
        elif ticker == "CCMFUT":
            m = today.month
            # avanca pra proximo mes valido CCM (impar)
            while m % 2 == 0 or m not in FUTURES_CCM_MONTHS:
                m += 1
                if m > 12:
                    m = 1
                    yy += 1
            for _ in range(3):
                y = yy
                cur_m = m
                if cur_m > 12:
                    cur_m -= 12
                    y += 1
                candidates.append(f"CCM{MONTH_CODE[cur_m]}{y:02d}")
                # próximo mês CCM (~+2)
                m += 2
                while m not in FUTURES_CCM_MONTHS and m <= 12:
                    m += 1
                if m > 12:
                    m -= 12
                    yy += 1

        for c in candidates:
            if f"{c}:{exchange}" in self._subscribed:
                return c
        return candidates[0] if candidates else ticker

    def _subscribe(self, ticker: str, exchange: str = "B") -> tuple[bool, int]:
        """Subscreve ticker na DLL. Retorna (sucesso, ret_code_dll).

        Resolve alias de futuros (WDOFUT/WINFUT) para o contrato vigente
        (ex: WDOK26) antes de chamar SubscribeTicker — DLL exige código
        vigente. self._subscribed registra AMBAS as keys (alias + vigente)
        pra que validações em _send_order_legacy funcionem com qualquer
        forma e downstream legado (queries por WDOFUT) continue funcionando.

        ret_code_dll != 0 tipicamente indica:
          - ticker inexistente no feed
          - licenca nao permite (limite de subscricoes atingido)
          - mercado especifico nao liberado
        """
        original = ticker
        ticker = self._resolve_active_contract(ticker, exchange)
        if ticker != original:
            log.info("subscribe.alias_resolved alias=%s contract=%s", original, ticker)

        key = f"{ticker}:{exchange}"
        alias_key = f"{original}:{exchange}"

        if key in self._subscribed:
            self._subscribed.add(alias_key)  # garante alias registrado tambem
            return True, 0  # ja subscrito — idempotente

        ret_t = self._dll.SubscribeTicker(c_wchar_p(ticker), c_wchar_p(exchange))

        # SubscribePriceDepth - habilitado apos implementacao do price_depth_cb

        conn_id = TConnectorAssetIdentifier(
            Version=0,
            Ticker=ticker,
            Exchange=exchange,
            FeedType=c_ubyte(0),
        )

        ret_d = self._dll.SubscribePriceDepth(byref(conn_id))

        if ret_d != 0:
            log.warning("profit_agent.subscribe_depth_failed ticker=%s ret=%d", ticker, ret_d)

        if ret_t == 0:
            self._subscribed.add(key)
            self._subscribed.add(alias_key)  # alias resolve pro mesmo contrato
            log.info(
                "profit_agent.subscribed ticker=%s exchange=%s alias=%s", ticker, exchange, original
            )
            return True, 0

        log.warning("profit_agent.subscribe_failed ticker=%s ret=%d", ticker, ret_t)
        return False, ret_t

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

        dll.DLLFinalize.restype = c_int

        # ── Market data — subscricoes ──────────────────────────────────────

        dll.SubscribeTicker.argtypes = [c_wchar_p, c_wchar_p]

        dll.SubscribeTicker.restype = c_int

        dll.UnsubscribeTicker.argtypes = [c_wchar_p, c_wchar_p]

        dll.UnsubscribeTicker.restype = c_int

        dll.SubscribePriceDepth.argtypes = [POINTER(TConnectorAssetIdentifier)]

        dll.SubscribePriceDepth.restype = c_int

        dll.GetAccountCount.argtypes = []

        dll.GetAccountCount.restype = c_int

        dll.GetPriceDepthSideCount.argtypes = [POINTER(TConnectorAssetIdentifier), c_ubyte]

        dll.GetPriceDepthSideCount.restype = c_int

        dll.GetPriceGroup.argtypes = [
            POINTER(TConnectorAssetIdentifier),
            c_ubyte,
            c_int,
            POINTER(TConnectorPriceGroup),
        ]

        dll.GetPriceGroup.restype = c_int

        dll.GetTheoreticalValues.argtypes = [
            POINTER(TConnectorAssetIdentifier),
            POINTER(c_double),
            POINTER(c_int64),
        ]

        dll.GetTheoreticalValues.restype = c_int

        dll.TranslateTrade.argtypes = [c_size_t, POINTER(TConnectorTrade)]

        dll.TranslateTrade.restype = c_int

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

        dll.SendOrder.argtypes = [POINTER(TConnectorSendOrder)]

        dll.SendOrder.restype = c_int64

        dll.SendChangeOrderV2.argtypes = [POINTER(TConnectorChangeOrder)]

        dll.SendChangeOrderV2.restype = c_int

        dll.SendCancelOrderV2.argtypes = [POINTER(TConnectorCancelOrder)]

        dll.SendCancelOrderV2.restype = c_int

        dll.SendCancelAllOrdersV2.argtypes = [POINTER(TConnectorCancelAllOrders)]

        dll.SendCancelAllOrdersV2.restype = c_int

        dll.SendZeroPositionV2.argtypes = [POINTER(TConnectorZeroPosition)]

        dll.SendZeroPositionV2.restype = c_int64

        # ------------------------------------------------------------------

        # Callbacks

        # ------------------------------------------------------------------

        # ── History (adicionado pelo patch) ──────────────────────────────

        self._dll.GetHistoryTrades.argtypes = [c_wchar_p, c_wchar_p, c_wchar_p, c_wchar_p]

        self._dll.GetHistoryTrades.restype = c_int

        self._dll.TranslateTrade.argtypes = [c_size_t, POINTER(TConnectorTrade)]

        self._dll.TranslateTrade.restype = c_int

        self._dll.SetHistoryTradeCallbackV2.restype = None

    def _register_callbacks(self) -> None:

        agent = self

        # 0. State callback — MINIMAL (nenhum I/O)

        @WINFUNCTYPE(None, c_int, c_int)
        def state_cb(conn_type: int, result: int) -> None:

            with agent._state_lock:
                if conn_type == CONN_STATE_LOGIN:
                    agent._login_ok = result == LOGIN_CONNECTED

                elif conn_type == CONN_STATE_MARKET_DATA:
                    agent._market_ok = result == MARKET_CONNECTED

                    if result == MARKET_CONNECTED:
                        agent._market_connected.set()

                elif conn_type == CONN_STATE_ROUTING:
                    # result==5 = broker conectado (ROUTING_BROKER_CONNECTED)

                    # result==2 = "sem conexao com servidores" (NAO conectado)

                    # result >2 = "sem conexao com corretora"

                    agent._routing_ok = result == ROUTING_BROKER_CONNECTED

                    if result == ROUTING_BROKER_CONNECTED:
                        agent._routing_connected.set()

                elif conn_type == CONN_STATE_MARKET_LOGIN:
                    agent._activate_ok = result == ACTIVATE_VALID

            agent._db_queue.put_nowait(
                {
                    "_type": "state",
                    "conn_type": conn_type,
                    "result": result,
                }
            )

        # 1. Account callback

        @WINFUNCTYPE(None, c_int, c_wchar_p, c_wchar_p, c_wchar_p)
        def account_cb(broker_id, broker_name, account_id, owner_name) -> None:

            name = (broker_name or "").upper()

            acc = (account_id or "").strip()

            log.info(
                "account broker_id=%d broker_name=%s account=%s owner=%s",
                broker_id,
                name,
                acc,
                owner_name,
            )

            # Guarda pelo nome da corretora E pelo account_id

            agent._discovered_accounts[name] = (broker_id, acc)

            agent._discovered_accounts[acc] = (broker_id, acc)

        # 2. Trade callback V1 - principal receptor de ticks (testado e funcionando)

        # c_void_p: TAssetIDRec com c_wchar_p passado como ponteiro oculto em Python 64-bit

        @WINFUNCTYPE(
            None, c_void_p, c_wchar_p, c_uint, c_double, c_double, c_int, c_int, c_int, c_int, c_int
        )
        def new_trade_cb(
            asset_ptr, date, trade_num, price, vol, qty, buy_agent, sell_agent, trade_type, is_edit
        ) -> None:

            if not asset_ptr:
                return

            asset_id = ctypes.cast(asset_ptr, POINTER(TAssetID)).contents

            ticker = asset_id.ticker or ""

            if not ticker:
                return

            agent._total_ticks += 1

            now = datetime.now(tz=UTC)
            agent._last_tick_at[f"{ticker}:{asset_id.bolsa or 'B'}"] = now
            # OCO Phase C (26/abr): cache last price em memória pra _trail_monitor_loop
            agent._last_prices[ticker] = float(price)

            try:
                agent._db_queue.put_nowait(
                    {
                        "_type": "tick",
                        "time": now,
                        "ticker": ticker,
                        "exchange": asset_id.bolsa or "B",
                        "price": price,
                        "quantity": qty,
                        "volume": vol,
                        "buy_agent": buy_agent,
                        "sell_agent": sell_agent,
                        "trade_number": trade_num,
                        "trade_type": trade_type,
                        "is_edit": bool(is_edit),
                    }
                )

            except queue.Full:
                pass

        # 3. Daily callback

        # c_void_p: POINTER(TAssetID) deve ser tratado como c_void_p em 64-bit

        @WINFUNCTYPE(
            None,
            c_void_p,
            c_wchar_p,
            c_double,
            c_double,
            c_double,
            c_double,
            c_double,
            c_double,
            c_double,
            c_double,
            c_double,
            c_double,
            c_int,
            c_int,
            c_int,
            c_int,
            c_int,
            c_int,
            c_int,
        )
        def daily_cb(
            asset_ptr,
            date,
            s_open,
            s_high,
            s_low,
            s_close,
            s_vol,
            s_ajuste,
            s_max_lim,
            s_min_lim,
            s_vol_buyer,
            s_vol_seller,
            n_qty,
            n_neg,
            n_contratos,
            n_qty_buyer,
            n_qty_seller,
            n_neg_buyer,
            n_neg_seller,
        ) -> None:

            if not asset_ptr:
                return

            asset_id = ctypes.cast(asset_ptr, POINTER(TAssetID)).contents

            log.info("DAILY_RAW ticker=%r date=%r close=%r", asset_id.ticker, date, s_close)

            ticker = asset_id.ticker or ""

            if not ticker:
                return

            try:
                dt = (
                    datetime.strptime(date[:10], "%d/%m/%Y").replace(tzinfo=UTC)
                    if date
                    else datetime.now(tz=UTC)
                )

            except Exception:
                dt = datetime.now(tz=UTC)

            agent._db_queue.put_nowait(
                {
                    "_type": "daily",
                    "time": dt,
                    "ticker": ticker,
                    "exchange": asset_id.bolsa or "B",
                    "open": s_open,
                    "high": s_high,
                    "low": s_low,
                    "close": s_close,
                    "volume": s_vol,
                    "adjust": s_ajuste,
                    "max_limit": s_max_lim,
                    "min_limit": s_min_lim,
                    "vol_buyer": s_vol_buyer,
                    "vol_seller": s_vol_seller,
                    "qty": n_qty,
                    "trades": n_neg,
                    "open_contracts": n_contratos,
                    "qty_buyer": n_qty_buyer,
                    "qty_seller": n_qty_seller,
                    "neg_buyer": n_neg_buyer,
                    "neg_seller": n_neg_seller,
                }
            )

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
                "price": price,
                "quantity": qty,
                "count": 1,
                "is_theoric": False,
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

            now = datetime.now(tz=UTC)

            agent._total_ticks += 1
            agent._last_tick_at[f"{ticker}:{asset_id.Exchange or 'B'}"] = now

            try:
                agent._db_queue.put_nowait(
                    {
                        "_type": "tick",
                        "time": now,
                        "ticker": ticker,
                        "exchange": asset_id.Exchange or "B",
                        "price": trade.Price,
                        "quantity": trade.Quantity,
                        "volume": trade.Volume,
                        "buy_agent": trade.BuyAgent,
                        "sell_agent": trade.SellAgent,
                        "trade_number": trade.TradeNumber,
                        "trade_type": trade.TradeType,
                        "is_edit": bool(flags & 1),
                    }
                )

            except queue.Full:
                pass  # descarta se fila cheia

            if agent._sse_clients:
                import json as _j

                _e = _j.dumps(
                    {
                        "ticker": ticker,
                        "price": trade.Price,
                        "quantity": trade.Quantity,
                        "volume": trade.Volume,
                        "time": datetime.now(tz=UTC).isoformat(),
                    }
                )

                with agent._sse_lock:
                    _dead = [q for q in agent._sse_clients if not _try_sse_put(q, _e)]

                    for q in _dead:
                        agent._sse_clients.remove(q)

        # 7. Asset list info V2 (SetAssetListInfoCallbackV2)

        # c_void_p: POINTER(TAssetID) como c_void_p em 64-bit

        @WINFUNCTYPE(
            None,
            c_void_p,
            c_wchar_p,
            c_wchar_p,
            c_int,
            c_int,
            c_int,
            c_int,
            c_int,
            c_double,
            c_double,
            c_wchar_p,
            c_wchar_p,
            c_wchar_p,
            c_wchar_p,
            c_wchar_p,
        )
        def asset_info_v2_cb(
            asset_ptr,
            name,
            description,
            min_qty,
            max_qty,
            lot,
            sec_type,
            sec_subtype,
            min_incr,
            contract_mult,
            valid_date,
            isin,
            sector,
            sub_sector,
            segment,
        ) -> None:

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

                except Exception as exc:
                    # V1 fix (Sprint V1, 21/abr): debug em vez de pass.
                    # Throttle: 1 log por 100 erros para evitar spam de
                    # asset com formato de data inesperado.
                    agent._asset_date_errors = getattr(agent, "_asset_date_errors", 0) + 1
                    if agent._asset_date_errors % 100 == 1:
                        log.debug(
                            "asset valid_date parse error (count=%d, raw=%r): %s",
                            agent._asset_date_errors,
                            valid_date,
                            exc,
                        )

            try:
                agent._db_queue.put_nowait(
                    {
                        "_type": "asset",
                        "ticker": ticker,
                        "exchange": asset_id.bolsa or "B",
                        "name": name,
                        "description": description,
                        "security_type": sec_type,
                        "security_subtype": sec_subtype,
                        "min_order_qty": min_qty,
                        "max_order_qty": max_qty,
                        "lot_size": lot,
                        "min_price_increment": min_incr,
                        "contract_multiplier": contract_mult,
                        "valid_date": vd,
                        "isin": isin,
                        "sector": sector,
                        "sub_sector": sub_sector,
                        "segment": segment,
                        "feed_type": asset_id.feed,
                    }
                )

            except queue.Full:
                pass

        # 8. Asset list callback (V1 compat)

        @WINFUNCTYPE(None, c_void_p, c_wchar_p)
        def asset_cb(asset_ptr, name) -> None:

            pass  # usa V2

        # 9. Adjust history V2

        # c_void_p: POINTER(TAssetID) como c_void_p em 64-bit

        @WINFUNCTYPE(
            None,
            c_void_p,
            c_double,
            c_wchar_p,
            c_wchar_p,
            c_wchar_p,
            c_wchar_p,
            c_wchar_p,
            c_uint,
            c_double,
        )
        def adjust_v2_cb(
            asset_ptr, value, adj_type, observ, dt_ajuste, dt_delib, dt_pgto, flags, mult
        ) -> None:

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
                agent._db_queue.put_nowait(
                    {
                        "_type": "adjustment",
                        "ticker": ticker,
                        "exchange": asset_id.bolsa or "B",
                        "adjust_date": parse_date(dt_ajuste),
                        "deliberation_date": parse_date(dt_delib),
                        "payment_date": parse_date(dt_pgto),
                        "adjust_type": adj_type,
                        "value": value,
                        "multiplier": mult,
                        "flags": flags,
                        "observation": observ,
                    }
                )

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
                agent._db_queue.put_nowait(
                    {
                        "_type": "book",
                        "time": datetime.now(tz=UTC),
                        "ticker": ticker,
                        "exchange": asset_id.bolsa or "B",
                        "side": int(side),
                        "position": position,
                        "price": pg.Price,
                        "quantity": pg.Quantity,
                        "count": pg.Count,
                        "is_theoric": is_theoric,
                    }
                )

            except queue.Full:
                pass

        # 11. Order callback — assinatura Delphi correta (P4 fix 28/abr):
        #   procedure(const a_OrderID: TConnectorOrderIdentifier); stdcall;
        # Recebe APENAS o identifier (24 bytes), não a TConnectorOrder completa.
        # Antes a struct era POINTER(TConnectorOrder) (152 bytes) → callback lia
        # 128 bytes de garbage além dos 24 válidos, causando ticker corrupted
        # (䱐Ǆ etc) e UnicodeEncodeError no log.
        #
        # Status/ticker/qty completos virão via _reconcile_orders_dll (polling
        # EnumerateAllOrders no scheduler a cada 5min, ou imediato via _check_levels_fill).
        #
        # Sessão 30/abr — P9 fix definitivo NÃO viável via callback:
        # tentamos avaliar redução de latência abaixo dos 5s do _watch_pending_orders_loop
        # mas a DLL não fornece status final via callback (order_cb só dá identifier;
        # trading_msg_cb só dá estágios de roteamento — Accepted/Rejected, nunca
        # FILLED/CANCELED). O teto técnico é polling. A combinação:
        #   - _watch_pending_orders_loop @5s (resolve em 10-20s)
        #   - _load_pending_orders_from_db no boot (cobre restart)
        #   - cleanup_stale_pending_orders_job @23h BRT (sweep 24h+)
        # é o estado-da-arte que a DLL permite. Não tentar callback-based fix.

        @WINFUNCTYPE(None, POINTER(TConnectorOrderIdentifier))
        def order_cb(oid_ptr) -> None:

            try:
                if not oid_ptr:
                    return
                oid = oid_ptr.contents
                local_id = oid.LocalOrderID
                cl_ord = (oid.ClOrderID or "").strip()

                # Throttle log: só 1 a cada 100 callbacks (alguns brokers spammam)
                agent._order_cb_count = getattr(agent, "_order_cb_count", 0) + 1
                agent._last_order_cb_at = time.time()
                if agent._order_cb_count % 100 == 1:
                    log.info(
                        "order_callback local_id=%d cl_ord=%s (count=%d)",
                        local_id,
                        cl_ord,
                        agent._order_cb_count,
                    )

                # Update incremental no DB: cl_ord_id (caso ainda NULL — bug P2 mitigation).
                # Status/qty serão atualizados pelo reconcile_loop via EnumerateAllOrders.
                if local_id > 0 and cl_ord:
                    agent._db_queue.put_nowait(
                        {
                            "_type": "order_cl_ord_update",
                            "local_order_id": local_id,
                            "cl_ord_id": cl_ord,
                        }
                    )

            except queue.Full:
                pass

            except Exception as exc:
                log.warning("order_callback error: %s", exc)

        # 12. TradingMessageResult callback - resultado de roteamento

        @WINFUNCTYPE(None, POINTER(TConnectorTradingMessageResult))
        def trading_msg_cb(result_ptr) -> None:

            r = result_ptr.contents

            code = r.ResultCode

            msg_text = (r.Message or "")[:200]

            status = _TRADING_RESULT_STATUS.get(code, 3)

            log.info(
                "trading_msg broker=%d msg_id=%d code=%d status=%d msg=%s",
                r.BrokerID,
                r.MessageID,
                code,
                status,
                msg_text[:80],
            )

            # P2-futuros fix (sessão 30/abr): r.OrderID.LocalOrderID vem 0 em
            # códigos como code=5 (RejectedHades, "Ordem inválida"). Sem isso,
            # WHERE local_order_id = 0 OR cl_ord_id = '' não acha row e
            # status=10 fica stuck até reconcile_loop pegar (5min, só 10-18h BRT).
            # Fallback via _msg_id_to_local (populado em _send_order_legacy).
            resolved_local_id = r.OrderID.LocalOrderID
            if resolved_local_id <= 0:
                resolved_local_id = agent._msg_id_to_local.get(r.MessageID, 0)

            try:
                agent._db_queue.put_nowait(
                    {
                        "_type": "trading_result",
                        "local_order_id": resolved_local_id,
                        "cl_ord_id": r.OrderID.ClOrderID or "",
                        "message_id": r.MessageID,
                        "broker_id": r.BrokerID,
                        "result_code": code,
                        "order_status": status,
                        "message": msg_text if code != 0 else None,
                    }
                )

            except queue.Full:
                pass

            # P1 (28/abr): trigger retry quando broker rejeita por blip de auth.
            # OrderCallback recebe dados corrompidos (struct layout); TradingMessage
            # é mais confiavel mas r.OrderID.LocalOrderID as vezes vem 0 — usamos
            # fallback via _msg_id_to_local mapeado em _send_order_legacy.
            # Match: code=3 (rejeicao) AND msg contem "Cliente n"/"logado".
            if code == 3 and ("Cliente n" in msg_text or "logado" in msg_text.lower()):
                rejected_id = r.OrderID.LocalOrderID
                if rejected_id <= 0:
                    rejected_id = agent._msg_id_to_local.get(r.MessageID, 0)
                if rejected_id > 0:
                    t = threading.Timer(5.0, agent._retry_rejected_order, args=(rejected_id,))
                    t.daemon = True
                    t.start()
                    log.info(
                        "retry_scheduled local_id=%d msg_id=%d delay=5s reason=broker_auth_blip",
                        rejected_id,
                        r.MessageID,
                    )
                else:
                    log.warning(
                        "retry_skipped no_local_id msg_id=%d (struct: %d, fallback miss)",
                        r.MessageID,
                        r.OrderID.LocalOrderID,
                    )

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
            state_cb,
            account_cb,
            new_trade_cb,
            daily_cb,
            progress_cb,
            tiny_book_cb,
            trade_v2_cb,
            asset_info_v2_cb,
            asset_cb,
            adjust_v2_cb,
            price_depth_cb,
            order_cb,
            trading_msg_cb,
            broker_account_cb,
        ]

    # ------------------------------------------------------------------

    # Kafka helpers (C1 - market_data.ticks.v1)

    # ------------------------------------------------------------------

    @staticmethod
    def _trade_type_to_aggressor(trade_type: int | None) -> int:
        """ProfitDLL TradeType -> aggressor int (1=BUY, -1=SELL, 0=unknown).

        Convencao Nelogica (manual ProfitDLL):
          1 = trade aggressor at buy
          2 = trade aggressor at sell
          outros (auction, RLP, cross) -> 0 -> mapeado p/ enum null no Avro.
        """
        if trade_type == 1:
            return 1
        if trade_type == 2:
            return -1
        return 0

    def _publish_tick_kafka(self, item: dict) -> None:
        """Publica tick em Kafka topic `market_data.ticks.v1`.

        Errors swallowed por design — esse hot path roda dentro do db_worker
        thread e NAO pode bloquear o ingest TimescaleDB. Falhas Kafka viram
        log.warning sem afetar persistencia local.
        """
        if not self._kafka_producer.enabled:
            return
        try:
            ts_us = int(item["time"].timestamp() * 1_000_000)
            self._kafka_producer.publish_tick(
                symbol=item["ticker"],
                ts_us=ts_us,
                price=item["price"],
                volume=int(item["quantity"]),
                aggressor=self._trade_type_to_aggressor(item.get("trade_type")),
            )
        except Exception as exc:
            log.warning(
                "kafka.publish_failed ticker=%s err=%s",
                item.get("ticker"),
                exc,
            )

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
                    # C1: publica em Kafka topic market_data.ticks.v1 paralelo
                    # ao insert TimescaleDB. Noop quando producer desabilitado.
                    self._publish_tick_kafka(item)

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

                    self._db.execute(
                        sql,
                        (
                            item["ticker"],
                            item.get("exchange", "B"),
                            item.get("adjust_date"),
                            item.get("deliberation_date"),
                            item.get("payment_date"),
                            item.get("adjust_type"),
                            item.get("value"),
                            item.get("multiplier"),
                            item.get("flags"),
                            item.get("observation"),
                        ),
                    )

                elif t == "book":
                    sql = """

                    INSERT INTO profit_order_book

                        (time, ticker, exchange, side, position, price,

                         quantity, count, is_theoric)

                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)

                    """

                    self._db.execute(
                        sql,
                        (
                            item["time"],
                            item["ticker"],
                            item.get("exchange", "B"),
                            item["side"],
                            item["position"],
                            item.get("price"),
                            item.get("quantity"),
                            item.get("count"),
                            item.get("is_theoric", False),
                        ),
                    )

                elif t == "order_update":
                    self._db.execute(
                        """UPDATE profit_orders SET

                               cl_ord_id    = COALESCE(%s, cl_ord_id),

                               order_status = %s,

                               traded_qty   = COALESCE(%s, traded_qty),

                               leaves_qty   = COALESCE(%s, leaves_qty),

                               avg_price    = COALESCE(%s, avg_price),


                               updated_at   = NOW()

                           WHERE local_order_id = %s""",
                        (
                            item.get("cl_ord_id") or None,
                            item.get("order_status", 0),
                            item.get("traded_qty") or None,
                            item.get("leaves_qty") or None,
                            item.get("avg_price") or None,
                            item["local_order_id"],
                        ),
                    )

                    # Hook diário: status FILLED (2) com avg_price → cria entry
                    self._maybe_dispatch_diary(item)

                elif t == "trading_result":
                    code = item.get("result_code", 0)
                    status = item.get("order_status", 0)
                    msg = item.get("message")
                    local_id = item.get("local_order_id") or 0
                    cl_ord = item.get("cl_ord_id") or None
                    msg_id = item.get("message_id") or 0

                    # P2-futuros fix (01/mai): tentativa de match com message_id
                    # como fallback quando local_id e cl_ord vêm zerados (ex:
                    # broker rejeita futuro com code=5 + struct callback corrompida
                    # + post-restart sem _msg_id_to_local mapping).
                    match = compute_trading_result_match(local_id, cl_ord, msg_id)
                    if match is None:
                        log.warning(
                            "trading_result_skip msg_id=%d code=%d status=%d "
                            "no_local_id no_cl_ord_id no_message_id",
                            msg_id,
                            code,
                            status,
                        )
                        continue

                    where_sql, where_params = match
                    _is_rejection = status == 8  # RejectedBroker/Market/etc
                    sql = (
                        "UPDATE profit_orders SET "
                        "order_status = CASE WHEN %s THEN 8 ELSE order_status END, "
                        "cl_ord_id = COALESCE(%s, cl_ord_id), "
                        "error_message = CASE WHEN %s IS NOT NULL THEN %s "
                        "ELSE error_message END, "
                        "updated_at = NOW() "
                        f"WHERE {where_sql}"
                    )
                    self._db.execute(
                        sql,
                        (_is_rejection, cl_ord, msg, msg, *where_params),
                    )

                elif t == "state":
                    log.info("state conn_type=%d result=%d", item["conn_type"], item["result"])

                elif t == "order_cl_ord_update":
                    # P4 fix (28/abr): callback agora só recebe OrderIdentifier
                    # (24 bytes). Preenchemos cl_ord_id incremental para mitigar P2
                    # (envio inicial gravava NULL, reconcile UPDATE WHERE cl_ord_id
                    # ficava 0 rows).
                    self._db.execute(
                        "UPDATE profit_orders SET cl_ord_id = %s, updated_at = NOW() "
                        "WHERE local_order_id = %s AND cl_ord_id IS NULL",
                        (item["cl_ord_id"], item["local_order_id"]),
                    )

            except Exception as e:
                log.warning("db_worker.error type=%s error=%s", item.get("_type"), e)

        log.info("db_worker.stopped")

    # ------------------------------------------------------------------
    # Diário hook: trade FILLED → cria entry pré-preenchida
    # ------------------------------------------------------------------

    def _maybe_dispatch_diary(self, item: dict) -> None:
        """Se ordem virou FILLED com avg_price, dispara POST /diario/from_fill.

        Idempotente local (set _diary_notified) + idempotente backend (UNIQUE
        em external_order_id). Roda em thread daemon para não bloquear
        o db_worker.
        """
        try:
            status = int(item.get("order_status", 0))
            if status != 2:  # 2 = Filled
                return
            local_id = int(item.get("local_order_id") or -1)
            if local_id < 0 or local_id in self._diary_notified:
                return
            avg = item.get("avg_price")
            qty = item.get("traded_qty")
            if not avg or not qty:
                return

            # Busca ticker, side e source em profit_orders (acabou de ser atualizado).
            # C5: source='trading_engine' significa que a ordem veio do robo autonomo,
            # que mantem journal proprio em trading_engine_orders.trade_journal — pular
            # o hook evita duplicacao na unified VIEW.
            row = None
            if self._db:
                try:
                    cur = self._db._conn.cursor()  # noqa: SLF001
                    cur.execute(
                        "SELECT ticker, order_side, source FROM profit_orders WHERE local_order_id = %s",
                        (local_id,),
                    )
                    row = cur.fetchone()
                    cur.close()
                except Exception as exc:
                    log.warning("diary.lookup_failed local_id=%d err=%s", local_id, exc)
                    return
            if not row:
                return
            source = row[2] if len(row) > 2 else None
            if source == "trading_engine":
                # Marca notified mesmo assim — evita re-checagem em ticks futuros do
                # mesmo local_id (callback DLL pode disparar varias vezes).
                self._diary_notified.add(local_id)
                log.info(
                    "diary.suppressed_engine_origin local_id=%d ticker=%s",
                    local_id,
                    row[0],
                )
                return
            # row[1] é order_side smallint (1=Buy, 2=Sell). Aceita int ou str
            # (compat retro: alguns paths antigos passavam "buy"/"sell").
            ticker = row[0]
            side_raw = row[1] if row[1] is not None else 1
            if isinstance(side_raw, int):
                direction = "BUY" if side_raw == ORDER_SIDE_BUY else "SELL"
            else:
                direction = "BUY" if str(side_raw).lower().startswith("b") else "SELL"

            self._diary_notified.add(local_id)
            payload = {
                "external_order_id": str(local_id),
                "ticker": ticker,
                "direction": direction,
                "entry_date": datetime.now(UTC).isoformat(),
                "entry_price": float(avg),
                "quantity": float(qty),
                "timeframe": self._tf_by_local_id.get(local_id),
                "user_id": self._diary_user_id,
            }
            threading.Thread(target=self._post_diary, args=(payload,), daemon=True).start()
        except Exception as exc:
            log.warning("diary.dispatch_error err=%s", exc)

    def _post_diary(self, payload: dict) -> None:
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self._diary_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                log.info(
                    "diary.posted ext_id=%s status=%d body=%s",
                    payload["external_order_id"],
                    resp.status,
                    body[:120],
                )
        except urllib.error.HTTPError as exc:
            log.warning(
                "diary.post_http_error ext_id=%s code=%d body=%s",
                payload["external_order_id"],
                exc.code,
                exc.read()[:120],
            )
        except Exception as exc:
            log.warning(
                "diary.post_error ext_id=%s err=%s",
                payload["external_order_id"],
                exc,
            )

    # ------------------------------------------------------------------

    # Envio de ordens

    # ------------------------------------------------------------------

    def _get_account(self, env: str, params: dict | None = None) -> tuple[int, str, str, str]:
        """Retorna (broker_id, account_id, sub_account_id, routing_password) por env.

        Se params contiver _account_broker_id (injetado pelo proxy), usa direto.
        Caso contrario, resolve pelo nome da corretora ou env vars (fallback)."""

        # Credenciais injetadas pelo proxy (conta ativa do banco)
        if params and params.get("_account_broker_id"):
            broker_id = int(params["_account_broker_id"])
            account_id = str(params.get("_account_id", ""))
            sub_id = str(params.get("_sub_account_id", ""))
            routing_pass = str(params.get("_routing_password", ""))
            return (broker_id, account_id, sub_id, routing_pass)

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

        broker_id, account_id, sub_id, routing_pass = self._get_account(env, params)

        if not account_id:
            return {"ok": False, "error": f"Conta {env} nao configurada no .env"}

        type_map = {
            "market": ORDER_TYPE_MARKET,
            "limit": ORDER_TYPE_LIMIT,
            "stop": ORDER_TYPE_STOP_LIMIT,
        }

        side_map = {"buy": ORDER_SIDE_BUY, "sell": ORDER_SIDE_SELL}

        order_type = type_map.get(params.get("order_type", "limit").lower(), ORDER_TYPE_LIMIT)

        order_side = side_map.get(params.get("order_side", "buy").lower(), ORDER_SIDE_BUY)

        ticker = params.get("ticker", "")

        exchange = params.get("exchange", "B")

        qty = int(params.get("quantity", 0))

        price = float(params.get("price", -1))

        stop_price = float(params.get("stop_price", -1))

        if not ticker or qty <= 0:
            return {"ok": False, "error": "ticker e quantity sao obrigatorios"}

        # Resolve alias de futuros (WDOFUT/WINFUT → contrato vigente).
        # DLL aceita o alias para market data mas rejeita ("Ordem inválida")
        # quando usado em SendOrder — broker exige o código mensal específico.
        original_ticker = ticker
        ticker = self._resolve_active_contract(ticker, exchange)
        if ticker != original_ticker:
            log.info(
                "order.alias_resolved alias=%s contract=%s exchange=%s",
                original_ticker,
                ticker,
                exchange,
            )
            params["ticker"] = ticker  # propaga pro DB insert + retry mapping

        # Valida subscrição: broker rejeita ordens em tickers não assinados via
        # SubscribeTicker. self._subscribed é populado em _subscribe quando DLL
        # retorna ret=0. Sem essa validação, ordens "Ordem inválida" silenciosas.
        sub_key = f"{ticker}:{exchange}"
        if sub_key not in self._subscribed:
            err = (
                f"ticker {ticker} (alias={original_ticker}) nao esta subscrito "
                f"em SubscribeTicker. Use POST /subscribe/{ticker}?exchange={exchange} "
                f"primeiro, ou adicione em profit_subscribed_tickers."
            )
            log.warning("order.rejected_not_subscribed ticker=%s exchange=%s", ticker, exchange)
            return {"ok": False, "error": err}

        order = TConnectorSendOrder(Version=2)

        order.AccountID = TConnectorAccountIdentifier(
            Version=0,
            BrokerID=broker_id,
            AccountID=account_id,
            SubAccountID=sub_id,
            Reserved=0,
        )

        order.AssetID = TConnectorAssetIdentifier(
            Version=0,
            Ticker=ticker,
            Exchange=exchange,
            FeedType=0,
        )

        order.Password = routing_pass

        order.OrderType = order_type

        order.OrderSide = order_side

        order.Price = price

        order.StopPrice = stop_price

        order.Quantity = qty

        order.MessageID = -1

        local_id = self._dll.SendOrder(byref(order))

        if local_id < 0:
            return {"ok": False, "error": f"SendOrder falhou: {local_id}"}

        self._total_orders += 1

        # Diário hook: guarda timeframe do gráfico ativo (enviado pelo dashboard)
        # para enriquecer entry no diário quando ordem for FILLED.
        tf_in = params.get("timeframe")
        if tf_in:
            self._tf_by_local_id[local_id] = str(tf_in)

        if not params.get("user_account_id"):
            params["user_account_id"] = f"{env}:{broker_id}:{account_id}"

        if self._db:
            self._db.insert_order(
                {
                    "local_order_id": local_id,
                    "message_id": order.MessageID,
                    # C5 (handshake trading-engine): aceita `_client_order_id` do body
                    # como cl_ord_id (string deterministica). Engine envia, agent persiste,
                    # callback DLL preserva (UPDATE so escreve quando cl_ord_id IS NULL).
                    "cl_ord_id": params.get("_client_order_id") or None,
                    "broker_id": broker_id,
                    "account_id": account_id,
                    "env": env,
                    "ticker": ticker,
                    "exchange": exchange,
                    "order_type": order_type,
                    "order_side": order_side,
                    "price": price,
                    "stop_price": stop_price,
                    "quantity": qty,
                    "user_account_id": params.get("user_account_id"),
                    "portfolio_id": params.get("portfolio_id"),
                    "is_daytrade": params.get("is_daytrade", False),
                    "strategy_id": params.get("strategy_id"),
                    "notes": params.get("notes"),
                    # Time In Force: GTC (default) ou GTD com validity_date ISO datetime
                    "validity_type": (params.get("validity_type") or "GTC").upper(),
                    "validity_date": params.get("validity_date") or None,
                    # C5: 'trading_engine' suprime _maybe_dispatch_diary (engine tem journal proprio)
                    "source": params.get("_source") or None,
                }
            )

        log.info(
            "order.sent local_id=%d ticker=%s side=%s type=%s qty=%d env=%s",
            local_id,
            ticker,
            order_side,
            order_type,
            qty,
            env,
        )

        # P1: salva params para potencial retry em caso de status=204
        # (broker rejeita com "Cliente não logado"). _retry_rejected_order
        # pode atualizar attempts depois se este send for um retry herdado.
        # Também mapeia message_id -> local_id para fallback no trading_msg_cb.
        with self._retry_lock:
            self._retry_params[local_id] = {
                "params": {k: v for k, v in params.items() if not k.startswith("_")},
                "attempts": 1,
                "ticker": ticker,
            }
            if order.MessageID > 0:
                self._msg_id_to_local[order.MessageID] = local_id

        # P9 mitigation: registra ordem para _watch_pending_orders_loop polling
        with self._pending_lock:
            self._pending_orders[local_id] = {
                "ts_sent": time.time(),
                "ticker": ticker,
                "env": params.get("env", "simulation"),
            }

        # C5: ecoa cl_ord_id quando o engine envia `_client_order_id`. Permite
        # ao engine fechar reconcile {client_order_id -> local_order_id} sem
        # segunda tabela de mapping.
        cl_ord_echo = params.get("_client_order_id")
        resp = {"ok": True, "local_order_id": local_id, "message_id": order.MessageID}
        if cl_ord_echo:
            resp["cl_ord_id"] = cl_ord_echo
        return resp

    def _retry_rejected_order(self, old_local_id: int) -> None:
        """P1 (28/abr): re-envia ordem rejeitada pelo broker com 'Cliente não logado'
        (OrderStatus=204). Padrão validado no log Delphi: broker derruba subconnection,
        reconecta, ordem é reenviada com novo local_id e fillou normalmente.

        Aguarda routing reconectar (até 30s) antes de re-enviar. Max 3 attempts total.
        """
        with self._retry_lock:
            entry = self._retry_params.get(old_local_id)
            if entry and not entry.get("retry_started"):
                entry["retry_started"] = True
            else:
                # Sem entry OU já em retry — skip (idempotente: trading_msg pode disparar 2x)
                return
        attempts = entry.get("attempts", 1)
        if attempts >= 3:
            log.warning("retry_aborted local_id=%d max_attempts=%d", old_local_id, attempts)
            return
        # Aguarda routing reconectar (até 30s — pattern Delphi: ~5-10s tipico)
        deadline = time.time() + 30.0
        while time.time() < deadline and not self._routing_ok:
            time.sleep(1.0)
        if not self._routing_ok:
            log.warning("retry_aborted local_id=%d routing_offline_30s", old_local_id)
            return
        # Re-enviar — _send_order_legacy criará novo entry em _retry_params com attempts=1
        # então sobrescrevemos para acumular o counter herdado.
        params = entry["params"].copy()
        log.info("retry_attempt old_local_id=%d attempt=%d", old_local_id, attempts + 1)
        res = self._send_order_legacy(params)
        if res.get("ok"):
            new_id = res["local_order_id"]
            with self._retry_lock:
                new_entry = self._retry_params.get(new_id, {})
                new_entry["attempts"] = attempts + 1
                new_entry["retry_of"] = old_local_id
                self._retry_params[new_id] = new_entry
            log.info(
                "retry_dispatched old=%d new=%d attempts=%d",
                old_local_id,
                new_id,
                attempts + 1,
            )
            if self._db:
                try:
                    self._db.execute(
                        "UPDATE profit_orders SET notes=COALESCE(notes,'') || %s,"
                        " updated_at=NOW() WHERE local_order_id=%s",
                        (f" rejected_204 (retried as {new_id})", old_local_id),
                    )
                except Exception as e:
                    log.warning("retry_db_update_failed: %s", e)
        else:
            log.warning("retry_send_failed old=%d err=%s", old_local_id, res.get("error"))

    def cancel_order(self, params: dict) -> dict:

        if not self._dll:
            return {"ok": False, "error": "DLL nao inicializada"}

        env = params.get("env", "simulation")

        broker_id, account_id, sub_id, routing_pass = self._get_account(env, params)

        cl_ord_id = params.get("cl_ord_id", "")

        local_id = int(params.get("local_order_id", -1))

        cancel = TConnectorCancelOrder(Version=1, MessageID=-1)

        cancel.AccountID = TConnectorAccountIdentifier(
            Version=0,
            BrokerID=broker_id,
            AccountID=account_id,
            SubAccountID=sub_id,
            Reserved=0,
        )

        cancel.OrderID = TConnectorOrderIdentifier(
            Version=0,
            LocalOrderID=local_id,
            ClOrderID=cl_ord_id,
        )

        cancel.Password = routing_pass

        ret = self._dll.SendCancelOrderV2(byref(cancel))

        return {"ok": ret == 0, "ret": ret}

    def cancel_all_orders(self, params: dict) -> dict:

        if not self._dll:
            return {"ok": False, "error": "DLL nao inicializada"}

        env = params.get("env", "simulation")

        broker_id, account_id, sub_id, routing_pass = self._get_account(env, params)

        cancel = TConnectorCancelAllOrders(Version=0)

        cancel.AccountID = TConnectorAccountIdentifier(
            Version=0,
            BrokerID=broker_id,
            AccountID=account_id,
            SubAccountID=sub_id,
            Reserved=0,
        )

        cancel.Password = routing_pass

        ret = self._dll.SendCancelAllOrdersV2(byref(cancel))

        return {"ok": ret == 0, "ret": ret}

    def change_order(self, params: dict) -> dict:

        if not self._dll:
            return {"ok": False, "error": "DLL nao inicializada"}

        env = params.get("env", "simulation")

        broker_id, account_id, sub_id, routing_pass = self._get_account(env, params)

        change = TConnectorChangeOrder(Version=1, MessageID=-1)

        change.AccountID = TConnectorAccountIdentifier(
            Version=0,
            BrokerID=broker_id,
            AccountID=account_id,
            SubAccountID=sub_id,
            Reserved=0,
        )

        change.OrderID = TConnectorOrderIdentifier(
            Version=0,
            LocalOrderID=int(params.get("local_order_id", -1)),
            ClOrderID=params.get("cl_ord_id", ""),
        )

        change.Password = routing_pass

        change.Price = float(params.get("price", -1))

        change.StopPrice = float(params.get("stop_price", -1))

        change.Quantity = int(params.get("quantity", 0))

        ret = self._dll.SendChangeOrderV2(byref(change))

        return {"ok": ret == 0, "ret": ret}

    def zero_position(self, params: dict) -> dict:

        if not self._dll:
            return {"ok": False, "error": "DLL nao inicializada"}

        env = params.get("env", "simulation")

        broker_id, account_id, sub_id, routing_pass = self._get_account(env, params)

        ticker = params.get("ticker", "")

        exchange = params.get("exchange", "B")

        pos_type = POSITION_TYPE_DAYTRADE if params.get("daytrade") else POSITION_TYPE_CONSOLIDATED

        zero = TConnectorZeroPosition(Version=2, PositionType=pos_type, MessageID=-1)

        zero.AccountID = TConnectorAccountIdentifier(
            Version=0,
            BrokerID=broker_id,
            AccountID=account_id,
            SubAccountID=sub_id,
            Reserved=0,
        )

        zero.AssetID = TConnectorAssetIdentifier(
            Version=0,
            Ticker=ticker,
            Exchange=exchange,
            FeedType=0,
        )

        zero.Password = routing_pass

        zero.Price = float(params.get("price", -1))  # -1 = mercado

        ret = self._dll.SendZeroPositionV2(byref(zero))

        return {"ok": ret >= 0, "local_order_id": ret}

    def subscribe_ticker(self, params: dict) -> dict:

        ticker = params.get("ticker", "").strip().upper()

        exchange = params.get("exchange", "B").strip().upper()

        notes = params.get("notes", "")

        if not ticker:
            return {"ok": False, "error": "ticker obrigatorio"}

        if self._db:
            self._db.add_ticker(ticker, exchange, notes)

        self._subscribe(ticker, exchange)

        return {"ok": True, "subscribed": list(self._subscribed)}

    def unsubscribe_ticker(self, params: dict) -> dict:

        ticker = params.get("ticker", "").strip().upper()

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

    def list_orders(
        self, ticker: str = "", status: str = "", env: str = "", limit: int = 100
    ) -> dict:
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
            return {"ticks": [], "error": "DB indisponivel"}

        try:
            sql = (
                "SELECT time,ticker,exchange,price,quantity,volume,"
                "buy_agent,sell_agent,trade_number,trade_type,is_edit "
                "FROM profit_ticks WHERE ticker=%s ORDER BY time DESC LIMIT %s"
            )

            with self._db._lock:
                cur = self._db._conn.cursor()

                cur.execute(sql, (ticker.upper(), limit))

                cols = [d[0] for d in cur.description]

                rows = [dict(zip(cols, r)) for r in cur.fetchall()]

                cur.close()

            for r in rows:
                if hasattr(r.get("time"), "isoformat"):
                    r["time"] = r["time"].isoformat()

            return {"ticker": ticker.upper(), "ticks": rows, "total": len(rows)}

        except Exception as e:
            return {"ticks": [], "error": str(e)}

    def query_assets(
        self, search: str = "", sector: str = "", sec_type: int = 0, limit: int = 200
    ) -> dict:

        if self._db is None or self._db._conn is None:
            return {"assets": [], "error": "DB indisponivel"}

        try:
            conds, params = [], []

            if search:
                conds.append("(ticker ILIKE %s OR name ILIKE %s OR isin ILIKE %s)")

                s = "%" + search.upper() + "%"

                params += [s, s, s]

            if sector:
                conds.append("sector ILIKE %s")
                params.append("%" + sector + "%")

            if sec_type:
                conds.append("security_type=%s")
                params.append(sec_type)

            where = ("WHERE " + " AND ".join(conds)) if conds else ""

            params.append(limit)

            sql = (
                f"SELECT ticker,exchange,name,description,security_type,"
                f"lot_size,min_price_increment,isin,sector,sub_sector,segment "
                f"FROM profit_assets {where} ORDER BY ticker LIMIT %s"
            )

            with self._db._lock:
                cur = self._db._conn.cursor()

                cur.execute(sql, params)

                cols = [d[0] for d in cur.description]

                rows = [dict(zip(cols, r)) for r in cur.fetchall()]

                cur.close()

            return {"assets": rows, "total": len(rows)}

        except Exception as e:
            return {"assets": [], "error": str(e)}

    def query_daily_summary(self) -> dict:

        if self._db is None or self._db._conn is None:
            return {"summary": [], "error": "DB indisponivel"}

        try:
            tickers = [t for t, _ in self._db.get_subscribed_tickers()]

            if not tickers:
                return {"summary": [], "note": "sem tickers"}

            ph = ",".join(["%s"] * len(tickers))

            sql = (
                f"SELECT DISTINCT ON (ticker) ticker,exchange,time,"
                f"open,high,low,close,volume,adjust,qty,trades "
                f"FROM profit_daily_bars WHERE ticker IN ({ph}) "
                f"ORDER BY ticker,time DESC"
            )

            with self._db._lock:
                cur = self._db._conn.cursor()

                cur.execute(sql, tickers)

                cols = [d[0] for d in cur.description]

                rows = [dict(zip(cols, r)) for r in cur.fetchall()]

                cur.close()

            for r in rows:
                if hasattr(r.get("time"), "isoformat"):
                    r["time"] = r["time"].isoformat()

                for k in ("open", "high", "low", "close", "volume", "adjust"):
                    if r.get(k) is not None:
                        r[k] = float(r[k])

            return {"summary": rows}

        except Exception as e:
            return {"summary": [], "error": str(e)}

    def get_positions(self, env: str = "simulation") -> dict:
        """Posicao liquida por ticker: soma fills positivos (buy) e negativos (sell)."""

        if self._db is None or self._db._conn is None:
            return {"positions": [], "error": "DB indisponivel"}

        try:
            sql = """

                SELECT ticker, exchange,

                    SUM(CASE WHEN order_side = 1 THEN traded_qty

                             WHEN order_side = 2 THEN -traded_qty ELSE 0 END) AS net_qty,

                    SUM(CASE WHEN order_side = 1 THEN traded_qty * COALESCE(avg_price,0)

                             WHEN order_side = 2 THEN -traded_qty * COALESCE(avg_price,0)

                             ELSE 0 END) AS financial_exposure

                FROM profit_orders

                WHERE env = %s AND order_status IN (1, 2)

                GROUP BY ticker, exchange

                HAVING SUM(CASE WHEN order_side = 1 THEN traded_qty

                                WHEN order_side = 2 THEN -traded_qty ELSE 0 END) != 0

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

    def get_positions_dll(self, env: str = "simulation") -> dict:
        """Consulta ordens via EnumerateAllOrders (assinatura correta manual pág.46)."""
        if not self._dll:
            return {"orders": [], "positions": [], "error": "DLL nao inicializada"}
        broker_id, account_id, sub_id, _ = self._get_account(env)
        if not account_id:
            return {"orders": [], "positions": [], "error": f"Conta {env} nao configurada"}
        orders_found = []
        _EnumCbType = WINFUNCTYPE(c_bool, POINTER(TConnectorOrder), c_long)
        _PosCbType = WINFUNCTYPE(None, POINTER(TConnectorTradingAccountPosition))
        _HistCbType = WINFUNCTYPE(None, c_int, c_int, c_int)

        def _enum_impl(order_ptr, user_data):
            try:
                o = order_ptr.contents
                orders_found.append(
                    {
                        "cl_ord_id": (o.OrderID.ClOrderID or "").strip(),
                        "local_id": o.OrderID.LocalOrderID,
                        "ticker": (o.AssetID.Ticker or "").strip(),
                        "exchange": (o.AssetID.Exchange or "B").strip(),
                        "order_side": o.OrderSide,
                        "order_type": o.OrderType,
                        "order_status": o.OrderStatus,
                        "price": round(o.Price, 4) if o.Price > 0 else None,
                        "stop_price": round(o.StopPrice, 4) if o.StopPrice > 0 else None,
                        "quantity": o.Quantity,
                        "traded_qty": o.TradedQuantity,
                        "leaves_qty": o.LeavesQuantity,
                        "avg_price": round(o.AveragePrice, 4) if o.AveragePrice > 0 else None,
                    }
                )
            except Exception as ex:
                log.warning("enum_orders error: %s", ex)
            return True

        def _pos_impl(pos_ptr):
            pass

        def _hist_impl(broker, count, extra):
            log.info("order_history_cb broker=%d count=%d", broker, count)

        self._gc_enum_cb = _EnumCbType(_enum_impl)
        self._gc_pos_cb = _PosCbType(_pos_impl)
        self._gc_hist_cb = _HistCbType(_hist_impl)
        try:
            for fn_name, cb in [
                ("SetAssetPositionListCallback", self._gc_pos_cb),
                ("SetOrderHistoryCallback", self._gc_hist_cb),
            ]:
                fn = getattr(self._dll, fn_name, None)
                if fn:
                    try:
                        fn.restype = None
                        fn(cb)
                    except Exception as e:
                        log.warning("get_positions_dll %s: %s", fn_name, e)
            self._dll.EnumerateAllOrders.argtypes = [
                POINTER(TConnectorAccountIdentifier),
                c_ubyte,
                c_long,
                _EnumCbType,
            ]
            self._dll.EnumerateAllOrders.restype = c_bool
            acct = TConnectorAccountIdentifier(
                Version=0,
                BrokerID=broker_id,
                AccountID=account_id,
                SubAccountID=sub_id or "",
                Reserved=0,
            )
            ok = self._dll.EnumerateAllOrders(byref(acct), c_ubyte(0), c_long(0), self._gc_enum_cb)
            log.info("EnumerateAllOrders ok=%s orders=%d", ok, len(orders_found))
        except AttributeError as e:
            return {"orders": [], "positions": [], "error": f"EnumerateAllOrders: {e}"}
        except Exception as e:
            log.warning("get_positions_dll error: %s", e)
            return {"orders": [], "positions": [], "error": str(e)}
        if orders_found and self._db:
            for o in orders_found:
                # P2 fix (28/abr): match por local_order_id OU cl_ord_id.
                # Antes filtrava por cl_ord_id apenas, mas envio inicial grava NULL
                # → 0 rows updated permanentemente. Match por local_id pega tudo.
                local_id = o.get("local_id")
                cl_ord = o.get("cl_ord_id")
                if not local_id and not cl_ord:
                    continue
                # Sincroniza tambem price/stop_price quando DLL retorna valores positivos
                # (mudancas via change_order — drag-to-modify, trail cancel+create, etc).
                # Sessão 30/abr: stop_price também — bug encontrado validando drag
                # SL via U1 (price atualizou mas stop_price ficou antigo).
                self._db.execute(
                    "UPDATE profit_orders SET order_status=%s,traded_qty=COALESCE(%s,traded_qty),"
                    "leaves_qty=COALESCE(%s,leaves_qty),avg_price=COALESCE(%s,avg_price),"
                    "price=CASE WHEN %s IS NOT NULL AND %s > 0 THEN %s ELSE price END,"
                    "stop_price=CASE WHEN %s IS NOT NULL AND %s > 0 THEN %s ELSE stop_price END,"
                    "cl_ord_id=COALESCE(cl_ord_id,%s),updated_at=NOW() "
                    "WHERE local_order_id=%s OR cl_ord_id=%s",
                    (
                        o["order_status"],
                        o["traded_qty"] or None,
                        o["leaves_qty"] or None,
                        o["avg_price"],
                        o.get("price"),
                        o.get("price"),
                        o.get("price"),
                        o.get("stop_price"),
                        o.get("stop_price"),
                        o.get("stop_price"),
                        cl_ord or None,
                        local_id or 0,
                        cl_ord or "",
                    ),
                )

                # B.18 hook (P4-aware, 28/abr): callback novo só envia OrderIdentifier,
                # então detecção de FILLED migrou para cá. Comparamos com último status
                # conhecido — só dispara hook na transição (qualquer)→2 (Filled).
                if local_id and o["order_status"] == 2 and o["traded_qty"]:
                    last = self._last_seen_status.get(local_id)
                    self._last_seen_status[local_id] = 2
                    if last != 2:
                        self._maybe_dispatch_diary(
                            {
                                "local_order_id": local_id,
                                "order_status": 2,
                                "avg_price": o["avg_price"],
                                "traded_qty": o["traded_qty"],
                            }
                        )
        return {"orders": orders_found, "positions": [], "env": env, "source": "dll"}

    def enumerate_position_assets(self, env: str = "simulation") -> dict:
        """Lista ativos com posição aberta via EnumerateAllPositionAssets (manual pág.46-47)."""
        if not self._dll:
            return {"assets": [], "error": "DLL nao inicializada"}
        broker_id, account_id, sub_id, _ = self._get_account(env)
        if not account_id:
            return {"assets": [], "error": f"Conta {env} nao configurada"}
        assets_found = []
        _EnumAssetCbType = WINFUNCTYPE(c_bool, POINTER(TConnectorAssetIdentifier), c_long)

        def _asset_impl(asset_ptr, user_data):
            try:
                a = asset_ptr.contents
                t = (a.Ticker or "").strip()
                if t:
                    assets_found.append({"ticker": t, "exchange": (a.Exchange or "B").strip()})
            except Exception as ex:
                log.warning("enumerate_position_asset error: %s", ex)
            return True

        self._gc_enum_asset_cb = _EnumAssetCbType(_asset_impl)
        try:
            self._dll.EnumerateAllPositionAssets.argtypes = [
                POINTER(TConnectorAccountIdentifier),
                c_ubyte,
                c_long,
                _EnumAssetCbType,
            ]
            self._dll.EnumerateAllPositionAssets.restype = c_bool
            acct = TConnectorAccountIdentifier(
                Version=0,
                BrokerID=broker_id,
                AccountID=account_id,
                SubAccountID=sub_id or "",
                Reserved=0,
            )
            ok = self._dll.EnumerateAllPositionAssets(
                byref(acct), c_ubyte(0), c_long(0), self._gc_enum_asset_cb
            )
            log.info("EnumerateAllPositionAssets ok=%s assets=%d", ok, len(assets_found))
        except AttributeError as e:
            return {"assets": [], "error": f"EnumerateAllPositionAssets: {e}"}
        except Exception as e:
            return {"assets": [], "error": str(e)}
        return {"assets": assets_found, "env": env, "source": "dll"}

    def get_position_v2(
        self, ticker: str, exchange: str = "B", env: str = "simulation", position_type: int = 1
    ) -> dict:
        """GetPositionV2 — posição real via DLL. ok=False é normal; dados ficam na struct.

        Resolve alias de futuros (WDOFUT/WINFUT) → contrato vigente e força
        exchange="F" para tickers de futuros (WDO/WIN/IND/DOL/BIT prefix).
        DLL exige código vigente + exchange correto, senão retorna struct zerada.
        """
        if not self._dll:
            return {"error": "DLL nao inicializada"}
        original_ticker = ticker
        is_future = ticker in FUTURES_ALIASES or ticker[:3] in ("WDO", "WIN", "IND", "DOL", "BIT")
        if is_future:
            exchange = "F"
            ticker = self._resolve_active_contract(ticker, exchange)
            if ticker != original_ticker:
                log.info(
                    "position_v2.alias_resolved alias=%s contract=%s exchange=F",
                    original_ticker,
                    ticker,
                )
        broker_id, account_id, sub_id, _ = self._get_account(env)
        if not account_id:
            return {"error": f"Conta {env} nao configurada"}
        try:
            self._dll.GetPositionV2.argtypes = [POINTER(TConnectorTradingAccountPosition)]
            self._dll.GetPositionV2.restype = c_bool
            pos = TConnectorTradingAccountPosition()
            pos.Version = 0 if position_type == 0 else 1
            pos.AccountID = TConnectorAccountIdentifier(
                Version=0,
                BrokerID=broker_id,
                AccountID=account_id,
                SubAccountID=sub_id or "",
                Reserved=0,
            )
            pos.AssetID = TConnectorAssetIdentifier(
                Version=0, Ticker=ticker, Exchange=exchange, FeedType=0
            )
            pos.PositionType = c_ubyte(position_type)
            self._dll.GetPositionV2(byref(pos))
            log.info(
                "GetPositionV2 ticker=%s open_qty=%d avg=%.4f side=%d",
                ticker,
                pos.OpenQuantity,
                pos.OpenAveragePrice,
                pos.OpenSide,
            )
            return {
                "ticker": ticker,
                "exchange": exchange,
                "env": env,
                "position_type": position_type,
                "open_qty": pos.OpenQuantity,
                "open_avg_price": round(pos.OpenAveragePrice, 4),
                "open_side": pos.OpenSide,
                "daily_buy_qty": pos.DailyBuyQuantity,
                "daily_buy_avg_price": round(pos.DailyAverageBuyPrice, 4),
                "daily_sell_qty": pos.DailySellQuantity,
                "daily_sell_avg_price": round(pos.DailyAverageSellPrice, 4),
                "qty_d1": pos.DailyQuantityD1,
                "qty_d2": pos.DailyQuantityD2,
                "qty_d3": pos.DailyQuantityD3,
                "qty_blocked": pos.DailyQuantityBlocked,
                "qty_pending": pos.DailyQuantityPending,
                "qty_available": pos.DailyQuantityAvailable,
                "source": "dll",
            }
        except AttributeError as e:
            return {"ticker": ticker, "error": f"GetPositionV2: {e}"}
        except Exception as e:
            return {"ticker": ticker, "error": str(e)}

    def send_oco_order(self, params: dict) -> dict:
        """
        OCO (One Cancels Other) — DLL não tem OCO nativo.
        Envia Take Profit (limit) + Stop Loss (stop-limit).
        Par registrado em self._oco_pairs para auto-cancelamento pelo _oco_monitor_loop.

        Params: env, ticker, exchange, quantity,
                take_profit   → preço limite (gain),
                stop_loss     → preço de disparo (loss),
                stop_limit    → preço limite do stop (default = stop_loss),
                order_side    → 'sell' (default) | 'buy',
                user_account_id, portfolio_id, is_daytrade, strategy_id
        """
        if not self._dll:
            return {"ok": False, "error": "DLL nao inicializada"}
        ticker = params.get("ticker", "")
        quantity = int(params.get("quantity", 0))
        take_profit = float(params.get("take_profit", 0))
        stop_loss = float(params.get("stop_loss", 0))
        stop_limit = float(params.get("stop_limit", stop_loss))
        env = params.get("env", "simulation")
        order_side = params.get("order_side", "sell")
        if not ticker or quantity <= 0:
            return {"ok": False, "error": "ticker e quantity obrigatorios"}
        if take_profit <= 0 or stop_loss <= 0:
            return {"ok": False, "error": "take_profit e stop_loss obrigatorios"}
        # 1. Take Profit — ordem limite
        tp = self._send_order_legacy(
            {
                **params,
                "order_type": "limit",
                "order_side": order_side,
                "price": take_profit,
                "stop_price": -1,
                "quantity": quantity,
                "strategy_id": f"{params.get('strategy_id', 'oco')}_tp",
            }
        )
        if not tp.get("ok"):
            return {"ok": False, "error": f"Take Profit falhou: {tp.get('error')}"}
        tp_id_local = tp["local_order_id"]
        # 2. Stop Loss — ordem stop-limit
        # P10 fix (29/abr): strategy_id codifica pair=tp_id para reload pos-restart
        sl = self._send_order_legacy(
            {
                **params,
                "order_type": "stop",
                "order_side": order_side,
                "price": stop_limit,
                "stop_price": stop_loss,
                "quantity": quantity,
                "strategy_id": f"oco_legacy_pair_{tp_id_local}_sl",
            }
        )
        if not sl.get("ok"):
            self.cancel_order({"local_order_id": tp["local_order_id"], "env": env})
            return {"ok": False, "error": f"Stop Loss falhou: {sl.get('error')}"}
        tp_id, sl_id = tp["local_order_id"], sl["local_order_id"]
        # 3. Registra par para auto-cancelamento pelo OCO monitor thread
        if not hasattr(self, "_oco_pairs"):
            self._oco_pairs = {}
        self._oco_pairs[tp_id] = {
            "pair_id": sl_id,
            "env": env,
            "type": "tp",
            "ticker": ticker,
            "price": take_profit,
        }
        self._oco_pairs[sl_id] = {
            "pair_id": tp_id,
            "env": env,
            "type": "sl",
            "ticker": ticker,
            "price": stop_loss,
        }
        log.info(
            "oco.sent ticker=%s qty=%d tp_id=%d tp=%.4f sl_id=%d sl=%.4f lim=%.4f",
            ticker,
            quantity,
            tp_id,
            take_profit,
            sl_id,
            stop_loss,
            stop_limit,
        )
        return {
            "ok": True,
            "ticker": ticker,
            "quantity": quantity,
            "take_profit": {"local_order_id": tp_id, "price": take_profit, "type": "limit"},
            "stop_loss": {
                "local_order_id": sl_id,
                "stop": stop_loss,
                "limit": stop_limit,
                "type": "stop_limit",
            },
        }

    def get_oco_status(self, tp_id: int, env: str = "simulation") -> dict:
        """
        Status do par OCO via EnumerateAllOrders.
        Retorna estado atual de ambas as pernas (TP e SL).
        """
        if not hasattr(self, "_oco_pairs") or tp_id not in self._oco_pairs:
            return {"error": f"OCO {tp_id} nao encontrado", "pairs": {}}
        pair_info = self._oco_pairs[tp_id]
        sl_id = pair_info["pair_id"]
        # Busca ordens atuais na DLL
        result = self.get_positions_dll(env)
        orders = {o["local_id"]: o for o in result.get("orders", [])}
        STATUS_MAP = {
            0: "pendente",
            1: "parcial",
            2: "executada",
            4: "cancelada",
            7: "stopped",
            8: "rejeitada",
            10: "nova",
        }

        def order_info(oid, otype):
            o = orders.get(oid, {})
            return {
                "local_order_id": oid,
                "type": otype,
                "status_code": o.get("order_status"),
                "status": STATUS_MAP.get(o.get("order_status", -1), "desconhecido"),
                "price": o.get("price"),
                "traded_qty": o.get("traded_qty", 0),
                "avg_price": o.get("avg_price"),
                "leaves_qty": o.get("leaves_qty", 0),
            }

        tp_info = order_info(tp_id, "take_profit")
        sl_info = order_info(sl_id, "stop_loss")
        # Determina estado do par
        tp_done = tp_info["status_code"] in (2, 4, 8)
        sl_done = sl_info["status_code"] in (2, 4, 8)
        if tp_info["status_code"] == 2:
            oco_status = "take_profit_executado"
        elif sl_info["status_code"] == 2:
            oco_status = "stop_loss_executado"
        elif tp_done and sl_done:
            oco_status = "encerrado"
        elif not tp_done and not sl_done:
            oco_status = "ativo"
        else:
            oco_status = "parcialmente_encerrado"
        return {
            "oco_status": oco_status,
            "ticker": pair_info.get("ticker"),
            "env": env,
            "take_profit": tp_info,
            "stop_loss": sl_info,
        }

    def _load_oco_legacy_pairs_from_db(self) -> int:
        """P10 fix: reconstrói _oco_pairs in-memory a partir de profit_orders.

        Strategy_id padrão `oco_legacy_pair_<tp_id>_sl` codifica o pareamento.
        Roda no boot — sem isso, pairs perdidos no restart deixavam SL órfão.

        Retorna número de pares carregados.
        """
        if self._db is None:
            return 0
        try:
            with self._db._lock:
                cur = self._db._conn.cursor()
                cur.execute(
                    "SELECT local_order_id, ticker, exchange, env, price, stop_price, "
                    "strategy_id FROM profit_orders "
                    "WHERE strategy_id LIKE 'oco_legacy_pair_%%_sl' "
                    "AND order_status IN (0, 1, 10)"
                )
                rows = cur.fetchall()
                cur.close()
        except Exception as exc:
            log.warning("oco_legacy.load_failed err=%s", exc)
            return 0
        if not hasattr(self, "_oco_pairs"):
            self._oco_pairs = {}
        loaded = 0
        for sl_row in rows:
            sl_id, ticker, exchange, env, price, stop_price, strategy_id = sl_row
            try:
                tp_id = int(strategy_id.split("_")[3])  # oco_legacy_pair_<TP>_sl
            except (ValueError, IndexError):
                continue
            # Verifica se TP ainda está pendente também
            try:
                tp_row = self._db.fetch_one(
                    "SELECT order_status, price FROM profit_orders WHERE local_order_id=%s",
                    (tp_id,),
                )
                if not tp_row or tp_row[0] not in (0, 1, 10):
                    continue
                tp_price = float(tp_row[1]) if tp_row[1] else 0.0
            except Exception:
                continue
            self._oco_pairs[tp_id] = {
                "pair_id": sl_id,
                "env": env,
                "type": "tp",
                "ticker": ticker,
                "price": tp_price,
            }
            self._oco_pairs[sl_id] = {
                "pair_id": tp_id,
                "env": env,
                "type": "sl",
                "ticker": ticker,
                "price": float(stop_price) if stop_price else 0.0,
            }
            loaded += 1
        if loaded:
            log.info("oco_legacy.loaded pairs=%d", loaded)
        return loaded

    def _oco_monitor_loop(self) -> None:
        """
        Background thread: monitora pares OCO via EnumerateAllOrders a cada 500ms.
        Quando uma perna executa (status=2), cancela a outra automaticamente.
        """
        log.info("oco_monitor.started")
        while not self._stop_event.is_set():
            try:
                if hasattr(self, "_oco_pairs") and self._oco_pairs:
                    # Obtém status atual de todas as ordens
                    result = self.get_positions_dll()
                    orders_by_id = {o["local_id"]: o for o in result.get("orders", [])}
                    to_remove = []
                    pairs_snapshot = dict(self._oco_pairs)
                    for local_id, pair_info in pairs_snapshot.items():
                        order = orders_by_id.get(local_id)
                        if not order:
                            continue
                        status = order.get("order_status", -1)
                        if status == 2:  # Filled — cancela a perna oposta
                            pair_id = pair_info["pair_id"]
                            pair_env = pair_info.get("env", "simulation")
                            log.info(
                                "oco.filled local_id=%d type=%s → canceling pair %d",
                                local_id,
                                pair_info.get("type"),
                                pair_id,
                            )
                            self.cancel_order({"local_order_id": pair_id, "env": pair_env})
                            to_remove.extend([local_id, pair_id])
                        elif status in (4, 8):  # Cancelada ou rejeitada — remove do mapa
                            to_remove.append(local_id)
                    for rid in set(to_remove):
                        self._oco_pairs.pop(rid, None)
                    if to_remove:
                        log.info(
                            "oco_monitor.removed ids=%s remaining=%d",
                            to_remove,
                            len(self._oco_pairs),
                        )
            except Exception as e:
                log.warning("oco_monitor error: %s", e)
            time.sleep(0.5)

    # ──────────────────────────────────────────────────────────────────
    # OCO multi-level (Phase A 26/abr/2026) — Design_OCO_Trailing_Splits.md
    # Suporta: attach a parent pending, N levels, TP/SL individualmente
    # opcionais (Decisão 3), parent fill parcial → re-rateio (Decisão 2).
    # Trailing (Decisão 1) e UI splits são Phases B/C.
    # ──────────────────────────────────────────────────────────────────

    def attach_oco(self, params: dict) -> dict:
        """Cria group OCO anexado a uma ordem pending.

        Params:
          env, parent_order_id (int), side ('buy'|'sell' opc — default oposto),
          levels: [{qty, tp_price?, sl_trigger?, sl_limit?, is_trailing?,
                    trail_distance?, trail_pct?}],
          is_daytrade, user_account_id, portfolio_id, notes
        """
        if self._db is None:
            return {"ok": False, "error": "DB nao inicializado"}
        try:
            invalid = _validate_attach_oco_params(params)
            if invalid is not None:
                return invalid
            parent_id = int(params["parent_order_id"])
            levels_in = params["levels"]
            env = params.get("env", "simulation")

            # 1. Valida parent: existe + status pending
            row = self._db.fetch_one(
                "SELECT ticker, exchange, order_side, quantity, order_status, "
                "broker_id, account_id, sub_account_id, user_account_id, "
                "portfolio_id, env FROM profit_orders WHERE local_order_id = %s",
                (parent_id,),
            )
            if not row:
                return {"ok": False, "error": f"parent {parent_id} nao existe"}
            (
                p_ticker,
                p_exch,
                p_side_int,
                p_qty,
                p_status,
                p_broker,
                p_acct,
                p_sub,
                p_uacct,
                p_pid,
                p_env,
            ) = row
            if p_status not in (0, 10):  # New, PendingNew
                return {"ok": False, "error": f"parent status={p_status} (precisa pending 0/10)"}

            # 2. Valida levels
            side_opt = params.get("side")
            if side_opt:
                side_int = 1 if str(side_opt).lower() == "buy" else 2
            else:
                side_int = 1 if p_side_int == 2 else 2  # oposto do parent

            tot = 0
            for lv in levels_in:
                q = int(lv.get("qty", 0))
                if q <= 0:
                    return {"ok": False, "error": "level.qty deve ser > 0"}
                tp = lv.get("tp_price")
                sl = lv.get("sl_trigger")
                if tp is None and sl is None:
                    return {"ok": False, "error": "level precisa ao menos 1 TP ou SL"}
                if tp is not None and sl is not None:
                    if side_int == 2 and float(tp) <= float(sl):
                        return {"ok": False, "error": "tp deve ser > sl em sell (proteger long)"}
                    if side_int == 1 and float(tp) >= float(sl):
                        return {"ok": False, "error": "tp deve ser < sl em buy (proteger short)"}
                tot += q
            if tot != int(p_qty):
                return {"ok": False, "error": f"sum(levels.qty)={tot} != parent.qty={p_qty}"}

            # 3. Insere group + levels (constraint unique impede duplo attach)
            try:
                grp_row = self._db.fetch_one(
                    """INSERT INTO profit_oco_groups
                       (parent_order_id, env, ticker, exchange, side, total_qty, remaining_qty,
                        status, is_daytrade, broker_id, account_id, sub_account_id,
                        user_account_id, portfolio_id, notes)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'awaiting', %s, %s, %s, %s, %s, %s, %s)
                       RETURNING group_id::text""",
                    (
                        parent_id,
                        env,
                        p_ticker,
                        p_exch,
                        side_int,
                        p_qty,
                        p_qty,
                        bool(params.get("is_daytrade", True)),
                        p_broker,
                        p_acct,
                        p_sub,
                        p_uacct,
                        p_pid,
                        params.get("notes"),
                    ),
                )
            except Exception as exc:
                msg = str(exc)
                if "ux_oco_groups_one_awaiting_per_parent" in msg or "duplicate key" in msg:
                    return {"ok": False, "error": f"parent {parent_id} ja tem OCO ativo"}
                raise
            group_id = grp_row[0]

            level_ids = []
            for idx, lv in enumerate(levels_in, start=1):
                tp = lv.get("tp_price")
                sl = lv.get("sl_trigger")
                slim = lv.get("sl_limit") if lv.get("sl_limit") is not None else sl
                lvl_row = self._db.fetch_one(
                    """INSERT INTO profit_oco_levels
                       (group_id, level_idx, qty, tp_price, sl_trigger, sl_limit,
                        is_trailing, trail_distance, trail_pct)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       RETURNING level_id::text""",
                    (
                        group_id,
                        idx,
                        int(lv["qty"]),
                        float(tp) if tp is not None else None,
                        float(sl) if sl is not None else None,
                        float(slim) if slim is not None else None,
                        bool(lv.get("is_trailing", False)),
                        float(lv["trail_distance"])
                        if lv.get("trail_distance") is not None
                        else None,
                        float(lv["trail_pct"]) if lv.get("trail_pct") is not None else None,
                    ),
                )
                level_ids.append(lvl_row[0])

            # 4. State em memória
            if not hasattr(self, "_oco_groups"):
                self._oco_groups = {}
                self._order_to_group = {}
            self._oco_groups[group_id] = {
                "parent_order_id": parent_id,
                "env": env,
                "ticker": p_ticker,
                "exchange": p_exch,
                "side": side_int,
                "total_qty": int(p_qty),
                "remaining_qty": int(p_qty),
                "status": "awaiting",
                "is_daytrade": bool(params.get("is_daytrade", True)),
                "broker_id": p_broker,
                "account_id": p_acct,
                "sub_account_id": p_sub,
                "user_account_id": p_uacct,
                "portfolio_id": p_pid,
                "levels": [
                    {
                        "level_id": lid,
                        "idx": i + 1,
                        "qty": int(lv["qty"]),
                        "tp_price": lv.get("tp_price"),
                        "tp_order_id": None,
                        "tp_status": None,
                        "sl_trigger": lv.get("sl_trigger"),
                        "sl_limit": lv.get("sl_limit")
                        if lv.get("sl_limit") is not None
                        else lv.get("sl_trigger"),
                        "sl_order_id": None,
                        "sl_status": None,
                        "is_trailing": bool(lv.get("is_trailing", False)),
                        "trail_distance": lv.get("trail_distance"),
                        "trail_pct": lv.get("trail_pct"),
                    }
                    for i, (lid, lv) in enumerate(zip(level_ids, levels_in))
                ],
            }
            self._order_to_group[parent_id] = (group_id, 0, "parent")

            log.info(
                "oco_group.attached group=%s parent=%d ticker=%s qty=%d levels=%d",
                group_id,
                parent_id,
                p_ticker,
                p_qty,
                len(levels_in),
            )
            return {
                "ok": True,
                "group_id": group_id,
                "parent_order_id": parent_id,
                "ticker": p_ticker,
                "total_qty": p_qty,
                "levels": [{"level_id": l, "idx": i + 1} for i, l in enumerate(level_ids)],
            }
        except Exception as exc:
            log.exception("attach_oco error: %s", exc)
            return {"ok": False, "error": str(exc)}

    def _dispatch_oco_group(self, group_id: str, filled_qty: int) -> None:
        """Dispara TPs e SLs dos levels após parent fill (total ou parcial).

        Decisão 2 (re-rateio): se filled < total, cada level.qty *= filled/total
        arredondado pra baixo; sobra acumula no último level.
        """
        grp = self._oco_groups.get(group_id)
        if not grp or grp["status"] not in ("awaiting",):
            return
        try:
            total = grp["total_qty"]
            ratio = filled_qty / total if total > 0 else 0
            if ratio <= 0:
                return

            # Re-rateio
            adjusted_qtys = []
            running = 0
            n = len(grp["levels"])
            for i, lv in enumerate(grp["levels"]):
                if i == n - 1:
                    q = filled_qty - running
                else:
                    q = int(lv["qty"] * ratio)
                    running += q
                adjusted_qtys.append(max(q, 0))

            # Atualiza DB (group + levels qty se mudou)
            self._db.execute(
                "UPDATE profit_oco_groups SET total_qty = %s, remaining_qty = %s, "
                "status = 'active', updated_at = NOW() WHERE group_id = %s",
                (filled_qty, filled_qty, group_id),
            )
            grp["total_qty"] = filled_qty
            grp["remaining_qty"] = filled_qty
            grp["status"] = "active"

            for lv, q in zip(grp["levels"], adjusted_qtys):
                lv["qty"] = q
                self._db.execute(
                    "UPDATE profit_oco_levels SET qty = %s, updated_at = NOW() WHERE level_id = %s",
                    (q, lv["level_id"]),
                )

            # Envia ordens TP/SL pra cada level com qty > 0
            base_params = {
                "env": grp["env"],
                "ticker": grp["ticker"],
                "exchange": grp["exchange"],
                "is_daytrade": grp["is_daytrade"],
                "user_account_id": grp["user_account_id"],
                "portfolio_id": grp["portfolio_id"],
            }
            side_str = "buy" if grp["side"] == 1 else "sell"
            for lv in grp["levels"]:
                if lv["qty"] <= 0:
                    continue
                # TP (limit)
                if lv["tp_price"] is not None:
                    tp_res = self._send_order_legacy(
                        {
                            **base_params,
                            "order_type": "limit",
                            "order_side": side_str,
                            "price": float(lv["tp_price"]),
                            "stop_price": -1,
                            "quantity": int(lv["qty"]),
                            "strategy_id": f"oco_grp_{group_id[:8]}_lv{lv['idx']}_tp",
                        }
                    )
                    if tp_res.get("ok"):
                        lv["tp_order_id"] = tp_res["local_order_id"]
                        lv["tp_status"] = "sent"
                        self._db.execute(
                            "UPDATE profit_oco_levels SET tp_order_id = %s, tp_status = 'sent', "
                            "updated_at = NOW() WHERE level_id = %s",
                            (tp_res["local_order_id"], lv["level_id"]),
                        )
                        self._order_to_group[tp_res["local_order_id"]] = (group_id, lv["idx"], "tp")
                    else:
                        log.warning(
                            "oco.tp_send_failed level=%s err=%s",
                            lv["level_id"],
                            tp_res.get("error"),
                        )
                # SL (stop-limit)
                if lv["sl_trigger"] is not None:
                    sl_res = self._send_order_legacy(
                        {
                            **base_params,
                            "order_type": "stop",
                            "order_side": side_str,
                            "price": float(lv["sl_limit"]),
                            "stop_price": float(lv["sl_trigger"]),
                            "quantity": int(lv["qty"]),
                            "strategy_id": f"oco_grp_{group_id[:8]}_lv{lv['idx']}_sl",
                        }
                    )
                    if sl_res.get("ok"):
                        lv["sl_order_id"] = sl_res["local_order_id"]
                        lv["sl_status"] = "sent"
                        self._db.execute(
                            "UPDATE profit_oco_levels SET sl_order_id = %s, sl_status = 'sent', "
                            "updated_at = NOW() WHERE level_id = %s",
                            (sl_res["local_order_id"], lv["level_id"]),
                        )
                        self._order_to_group[sl_res["local_order_id"]] = (group_id, lv["idx"], "sl")
                    else:
                        log.warning(
                            "oco.sl_send_failed level=%s err=%s",
                            lv["level_id"],
                            sl_res.get("error"),
                        )

            log.info(
                "oco_group.dispatched group=%s filled=%d/%d levels=%d",
                group_id,
                filled_qty,
                total,
                len(grp["levels"]),
            )
        except Exception as exc:
            log.exception("dispatch_oco_group error group=%s: %s", group_id, exc)

    def _load_oco_state_from_db(self) -> int:
        """Phase D: recarrega groups awaiting/active/partial do DB para self._oco_groups
        após restart. Reconstrói também self._order_to_group (reverse index) a partir
        de tp_order_id e sl_order_id dos levels.

        Retorna número de groups carregados. Idempotente — sobrescreve estado atual.
        """
        if self._db is None:
            return 0
        try:
            with self._db._lock:
                cur = self._db._conn.cursor()
                cur.execute(
                    """SELECT group_id::text, parent_order_id, env, ticker, exchange, side,
                              total_qty, remaining_qty, status, is_daytrade, broker_id,
                              account_id, sub_account_id, user_account_id, portfolio_id
                       FROM profit_oco_groups
                       WHERE status IN ('awaiting','active','partial')
                       ORDER BY created_at"""
                )
                grp_rows = cur.fetchall()
                if not grp_rows:
                    cur.close()
                    return 0
                group_ids = [r[0] for r in grp_rows]
                # Carrega levels de todos groups em 1 query
                cur.execute(
                    """SELECT level_id::text, group_id::text, level_idx, qty,
                              tp_price, tp_order_id, tp_status,
                              sl_trigger, sl_limit, sl_order_id, sl_status,
                              is_trailing, trail_distance, trail_pct, trail_high_water
                       FROM profit_oco_levels
                       WHERE group_id::text = ANY(%s)
                       ORDER BY group_id, level_idx""",
                    (group_ids,),
                )
                lvl_rows = cur.fetchall()
                cur.close()

            # Agrupa levels por group_id
            levels_by_group: dict[str, list[dict]] = {}
            for lr in lvl_rows:
                (
                    lid,
                    gid,
                    idx,
                    qty,
                    tp_p,
                    tp_oid,
                    tp_st,
                    sl_t,
                    sl_l,
                    sl_oid,
                    sl_st,
                    is_tr,
                    t_dist,
                    t_pct,
                    t_hw,
                ) = lr
                levels_by_group.setdefault(gid, []).append(
                    {
                        "level_id": lid,
                        "idx": idx,
                        "qty": int(qty),
                        "tp_price": float(tp_p) if tp_p is not None else None,
                        "tp_order_id": int(tp_oid) if tp_oid is not None else None,
                        "tp_status": tp_st,
                        "sl_trigger": float(sl_t) if sl_t is not None else None,
                        "sl_limit": float(sl_l) if sl_l is not None else None,
                        "sl_order_id": int(sl_oid) if sl_oid is not None else None,
                        "sl_status": sl_st,
                        "is_trailing": bool(is_tr),
                        "trail_distance": float(t_dist) if t_dist is not None else None,
                        "trail_pct": float(t_pct) if t_pct is not None else None,
                        "trail_high_water": float(t_hw) if t_hw is not None else None,
                    }
                )

            # Reconstrói self._oco_groups + self._order_to_group
            self._oco_groups = {}
            self._order_to_group = {}
            for gr in grp_rows:
                (
                    gid,
                    parent_id,
                    env,
                    ticker,
                    exch,
                    side,
                    tot,
                    rem,
                    status,
                    isd,
                    brk,
                    acct,
                    sub,
                    uacct,
                    pid,
                ) = gr
                self._oco_groups[gid] = {
                    "parent_order_id": int(parent_id) if parent_id is not None else None,
                    "env": env,
                    "ticker": ticker,
                    "exchange": exch,
                    "side": int(side),
                    "total_qty": int(tot),
                    "remaining_qty": int(rem),
                    "status": status,
                    "is_daytrade": bool(isd),
                    "broker_id": brk,
                    "account_id": acct,
                    "sub_account_id": sub,
                    "user_account_id": uacct,
                    "portfolio_id": pid,
                    "levels": levels_by_group.get(gid, []),
                }
                # Reverse index: parent + cada TP/SL ainda 'sent'
                if parent_id is not None and status == "awaiting":
                    self._order_to_group[int(parent_id)] = (gid, 0, "parent")
                for lv in levels_by_group.get(gid, []):
                    if lv["tp_order_id"] is not None:
                        self._order_to_group[lv["tp_order_id"]] = (gid, lv["idx"], "tp")
                    if lv["sl_order_id"] is not None:
                        self._order_to_group[lv["sl_order_id"]] = (gid, lv["idx"], "sl")

            log.info(
                "oco.state_loaded groups=%d levels=%d order_index=%d",
                len(self._oco_groups),
                len(lvl_rows),
                len(self._order_to_group),
            )
            return len(self._oco_groups)
        except Exception as exc:
            log.exception("load_oco_state_from_db error: %s", exc)
            return 0

    def _oco_groups_monitor_loop(self) -> None:
        """Background thread: monitora groups OCO multi-level a cada 500ms.

        Responsabilidades:
        - Detecta parent fill (status=2 ou 1 com leaves==0) → _dispatch_oco_group
        - Detecta TP/SL fill em levels → cancela contraparte e fecha level
        - Marca group=completed quando todos levels fechados
        """
        log.info("oco_groups_monitor.started")
        while not self._stop_event.is_set():
            try:
                if hasattr(self, "_oco_groups") and self._oco_groups:
                    result = self.get_positions_dll()
                    orders_by_id = {o["local_id"]: o for o in result.get("orders", [])}
                    groups_snapshot = list(self._oco_groups.items())
                    for group_id, grp in groups_snapshot:
                        # 1. Awaiting → checa parent fill
                        if grp["status"] == "awaiting":
                            parent_order = orders_by_id.get(grp["parent_order_id"])
                            if parent_order:
                                pst = parent_order.get("order_status", -1)
                                traded = int(parent_order.get("traded_qty", 0))
                                if pst == 2 and traded > 0:  # Filled
                                    self._dispatch_oco_group(group_id, traded)
                                elif pst == 1 and traded > 0:  # PartialFilled
                                    self._dispatch_oco_group(group_id, traded)
                                elif pst in (4, 8):  # Cancelled/Rejected → cancela group
                                    self._db.execute(
                                        "UPDATE profit_oco_groups SET status='cancelled', "
                                        "completed_at=NOW(), updated_at=NOW() WHERE group_id=%s",
                                        (group_id,),
                                    )
                                    grp["status"] = "cancelled"
                                    log.info("oco_group.cancelled_with_parent group=%s", group_id)
                        # 2. Active/partial → checa fills de TP/SL
                        elif grp["status"] in ("active", "partial"):
                            self._check_levels_fill(group_id, grp, orders_by_id)
            except Exception as e:
                log.warning("oco_groups_monitor error: %s", e)
            time.sleep(0.5)

    def _check_levels_fill(self, group_id: str, grp: dict, orders_by_id: dict) -> None:
        """Verifica fills de TP/SL em cada level; cancela contraparte; fecha group quando tudo done."""
        any_open = False
        any_filled_now = False
        for lv in grp["levels"]:
            tp_oid = lv.get("tp_order_id")
            sl_oid = lv.get("sl_order_id")
            tp_st = lv.get("tp_status")
            sl_st = lv.get("sl_status")
            level_open = (tp_oid and tp_st in ("sent", "pending")) or (
                sl_oid and sl_st in ("sent", "pending")
            )
            if not level_open:
                continue

            # TP filled? cancela SL
            if tp_oid and tp_st == "sent":
                tpo = orders_by_id.get(tp_oid)
                if tpo:
                    s = tpo.get("order_status", -1)
                    if s == 2:  # filled
                        lv["tp_status"] = "filled"
                        self._db.execute(
                            "UPDATE profit_oco_levels SET tp_status='filled', updated_at=NOW() "
                            "WHERE level_id=%s",
                            (lv["level_id"],),
                        )
                        any_filled_now = True
                        if sl_oid and sl_st == "sent":
                            self.cancel_order({"local_order_id": sl_oid, "env": grp["env"]})
                            lv["sl_status"] = "cancelled"
                            self._db.execute(
                                "UPDATE profit_oco_levels SET sl_status='cancelled', updated_at=NOW() "
                                "WHERE level_id=%s",
                                (lv["level_id"],),
                            )
                            log.info("oco.tp_filled→sl_cancel group=%s lv=%d", group_id, lv["idx"])
                        continue
                    elif s in (4, 8):
                        lv["tp_status"] = "cancelled" if s == 4 else "rejected"

            # SL filled? cancela TP
            if sl_oid and sl_st == "sent":
                slo = orders_by_id.get(sl_oid)
                if slo:
                    s = slo.get("order_status", -1)
                    if s == 2:
                        lv["sl_status"] = "filled"
                        self._db.execute(
                            "UPDATE profit_oco_levels SET sl_status='filled', updated_at=NOW() "
                            "WHERE level_id=%s",
                            (lv["level_id"],),
                        )
                        any_filled_now = True
                        if tp_oid and tp_st == "sent":
                            self.cancel_order({"local_order_id": tp_oid, "env": grp["env"]})
                            lv["tp_status"] = "cancelled"
                            self._db.execute(
                                "UPDATE profit_oco_levels SET tp_status='cancelled', updated_at=NOW() "
                                "WHERE level_id=%s",
                                (lv["level_id"],),
                            )
                            log.info("oco.sl_filled→tp_cancel group=%s lv=%d", group_id, lv["idx"])
                        continue
                    elif s in (4, 8):
                        lv["sl_status"] = "cancelled" if s == 4 else "rejected"

            # Re-avalia se este level ainda tem perna aberta
            still_open = (lv["tp_status"] == "sent") or (lv["sl_status"] == "sent")
            if still_open:
                any_open = True

        # Atualiza status do group
        if not any_open:
            self._db.execute(
                "UPDATE profit_oco_groups SET status='completed', completed_at=NOW(), "
                "updated_at=NOW() WHERE group_id=%s",
                (group_id,),
            )
            grp["status"] = "completed"
            log.info("oco_group.completed group=%s", group_id)
        elif any_filled_now and grp["status"] == "active":
            self._db.execute(
                "UPDATE profit_oco_groups SET status='partial', updated_at=NOW() WHERE group_id=%s",
                (group_id,),
            )
            grp["status"] = "partial"

    # ──────────────────────────────────────────────────────────────────
    # OCO Phase C (26/abr) — Trailing stop
    # Decisão 1: aceita trail_distance (R$) XOR trail_pct (%)
    # Decisão 6: se mercado já além do trigger ao criar → dispara market imediato
    # ──────────────────────────────────────────────────────────────────

    def _get_last_price(self, ticker: str) -> float | None:
        """Última cotação com fallback ao DB.

        `_last_prices` é populado no callback de tick; após restart fica vazio
        até primeiro tick de cada ticker chegar. Pra resilience (broker degradado,
        callback inativo, restart recente), fallback consulta `profit_ticks`
        last row do ticker. Cache hit é zero-overhead.
        """
        last = self._last_prices.get(ticker)
        if last is not None and last > 0:
            return float(last)
        # Tenta alias resolvido tambem (WDOFUT/WDOK26)
        if ticker in FUTURES_ALIASES or ticker[:3] in ("WDO", "WIN", "IND", "DOL", "BIT"):
            resolved = self._resolve_active_contract(ticker, "F")
            if resolved != ticker:
                last2 = self._last_prices.get(resolved)
                if last2 is not None and last2 > 0:
                    self._last_prices[ticker] = float(last2)  # cache fwd
                    return float(last2)
        # Fallback: DB
        if self._db is None:
            return None
        try:
            row = self._db.fetch_one(
                "SELECT price FROM profit_ticks WHERE ticker=%s "
                "AND time > NOW() - INTERVAL '5 min' ORDER BY time DESC LIMIT 1",
                (ticker,),
            )
            if row and row[0]:
                price = float(row[0])
                if price > 0:
                    self._last_prices[ticker] = price  # cache fwd
                    return price
        except Exception:
            pass
        return None

    def _trail_compute_new_sl(self, side: int, last_price: float, lv: dict) -> float | None:
        """Calcula novo SL baseado em high_water + distance/pct.

        Retorna None se SL não deve mover (high_water não favorável).
        side=2 (sell, proteger long): SL = high_water - distance (max ratchet up)
        side=1 (buy, proteger short): SL = low_water + distance (min ratchet down)
        """
        hw = lv.get("trail_high_water")
        moved = False
        if side == 2:  # proteger long — high_water é o MAX
            if hw is None or last_price > hw:
                hw = last_price
                lv["trail_high_water"] = hw
                moved = True
            if lv.get("trail_distance"):
                self._persist_trail_hw_if_moved(lv, moved)
                return hw - float(lv["trail_distance"])
            if lv.get("trail_pct"):
                self._persist_trail_hw_if_moved(lv, moved)
                return hw * (1 - float(lv["trail_pct"]) / 100.0)
        else:  # buy — low_water é o MIN
            if hw is None or last_price < hw:
                hw = last_price
                lv["trail_high_water"] = hw
                moved = True
            if lv.get("trail_distance"):
                self._persist_trail_hw_if_moved(lv, moved)
                return hw + float(lv["trail_distance"])
            if lv.get("trail_pct"):
                self._persist_trail_hw_if_moved(lv, moved)
                return hw * (1 + float(lv["trail_pct"]) / 100.0)
        return None

    def _persist_trail_hw_if_moved(self, lv: dict, moved: bool) -> None:
        """Persiste `trail_high_water` em DB quando mover. Resilience contra
        restart: sem isso, hw em memória resetava pra NULL após cada restart
        (`_load_oco_state_from_db` carrega do DB), trail nunca acumulava em
        sessões com instabilidade NSSM.
        """
        if not moved or not self._db or not lv.get("level_id"):
            return
        try:
            self._db.execute(
                "UPDATE profit_oco_levels SET trail_high_water=%s, updated_at=NOW() "
                "WHERE level_id=%s",
                (lv["trail_high_water"], lv["level_id"]),
            )
        except Exception:
            pass  # Best-effort; reconcile pegará

    def _load_pending_orders_from_db(self) -> None:
        """Boot helper (sessão 30/abr): popula `_pending_orders` com ordens
        em status pendente nas últimas N horas para que `_watch_pending_orders_loop`
        cubra restarts do agent.

        Antes desse fix, restart limpava o dict in-memory e órfãs ficavam fora
        do watch até cleanup_stale_pending_orders_job rodar (1×/dia 23h BRT) —
        gap visível no flatten WDOFUT 30/abr (9 órfãs de 28-29/abr ainda
        retornando ret=-2147483636).

        Janela default 24h (env `PROFIT_WATCH_LOAD_HOURS`). Mais tempo amplia
        cobertura mas aumenta carga em DLL polling no loop.
        """
        if not self._db:
            return
        try:
            hours = int(os.environ.get("PROFIT_WATCH_LOAD_HOURS", "24"))
            rows = self._db.fetch_all(
                """SELECT local_order_id, ticker, env, created_at
                     FROM profit_orders
                    WHERE order_status IN (0, 1, 10)
                      AND created_at >= NOW() - (%s::int * INTERVAL '1 hour')
                    ORDER BY created_at DESC
                    LIMIT 500""",
                (hours,),
            )
            now = time.time()
            with self._pending_lock:
                for row in rows or []:
                    local_id, ticker, env, created_at = row[0], row[1], row[2], row[3]
                    # ts_sent: usa idade real da ordem (created_at) para que
                    # o loop logo classifique como orphan/timeout se DLL
                    # já não enumera (vs assumir age=0 e ficar 60s no limbo).
                    age_s = max(0.0, (now - created_at.timestamp()) if created_at else 0.0)
                    self._pending_orders[int(local_id)] = {
                        "ts_sent": now - age_s,
                        "ticker": ticker or "",
                        "env": env or "simulation",
                    }
            log.info("watch_pending_orders.loaded n=%d hours=%d", len(rows or []), hours)
        except Exception as exc:
            log.warning("watch_pending_orders.load_failed error=%s", exc)

    def _watch_pending_orders_loop(self) -> None:
        """Mitigação P9: detecta status final de ordens pendentes via DLL polling.

        Cenário: broker degradado/callback falho → DB stuck status=10 (PendingNew)
        eternamente. Reconcile loop normal só corrige se DLL ainda enumera, mas
        ordens já encerradas saem do `EnumerateAllOrders` rápido.

        Fluxo:
          1. `_send_order_legacy` registra `local_id` em `_pending_orders`.
          2. Este loop varre a cada 5s, chama `EnumerateAllOrders` (já atualiza DB
             via `get_positions_dll` para ordens enumeradas).
          3. Para `local_id` registrado:
             - Se DLL enumera → status updated pelo reconcile, remove do registry
               se não-pendente.
             - Se DLL NÃO enumera + DB ainda em status pendente após 60s →
               marca como `status=8` (Rejected) com `error_message='watch_orphan_no_dll_record'`.
             - Após 5min, remove do registry mesmo se ainda pending (não vai resolver).
        """
        log.info("watch_pending_orders.started")
        while not self._stop_event.is_set():
            try:
                with self._pending_lock:
                    if not self._pending_orders:
                        time.sleep(5.0)
                        continue
                    snap = dict(self._pending_orders)

                # Enumera + reconcile DB (já atualiza ordens enumeradas)
                env = next(iter(snap.values()))["env"]
                res = self.get_positions_dll(env=env)
                dll_orders = res.get("orders", []) if isinstance(res, dict) else []
                seen_local = {o.get("local_id") for o in dll_orders if o.get("local_id")}

                now = time.time()
                to_drop: list[int] = []
                for local_id, info in snap.items():
                    age = now - info["ts_sent"]
                    # Read DB status atual (após reconcile run)
                    if not self._db:
                        continue
                    row = self._db.fetch_one(
                        "SELECT order_status FROM profit_orders WHERE local_order_id=%s",
                        (local_id,),
                    )
                    if not row:
                        if age > 60:
                            to_drop.append(local_id)
                        continue
                    cur_status = int(row[0])
                    if cur_status not in (0, 1, 10):
                        # Final state — remove
                        log.info(
                            "watch.order_resolved local_id=%d status=%d age=%.1fs",
                            local_id,
                            cur_status,
                            age,
                        )
                        to_drop.append(local_id)
                        continue
                    # Stuck pending no DB
                    if local_id not in seen_local and age > 60:
                        # DLL não enumera mais + DB ainda pending = orphan
                        try:
                            self._db.execute(
                                "UPDATE profit_orders SET order_status=8, "
                                "error_message='watch_orphan_no_dll_record', "
                                "updated_at=NOW() "
                                "WHERE local_order_id=%s AND order_status IN (0,1,10)",
                                (local_id,),
                            )
                            log.warning(
                                "watch.order_orphaned local_id=%d age=%.1fs ticker=%s "
                                "marked status=8",
                                local_id,
                                age,
                                info["ticker"],
                            )
                        except Exception as exc:
                            log.warning("watch.orphan_update_failed local=%d e=%s", local_id, exc)
                        to_drop.append(local_id)
                    elif age > 300:
                        # 5min sem resolução — desiste
                        log.info(
                            "watch.order_timeout local_id=%d age=%.1fs DB status=%d remove",
                            local_id,
                            age,
                            cur_status,
                        )
                        to_drop.append(local_id)

                if to_drop:
                    with self._pending_lock:
                        for lid in to_drop:
                            self._pending_orders.pop(lid, None)

                time.sleep(5.0)
            except Exception as exc:
                log.warning("watch_pending_orders error: %s", exc)
                time.sleep(10.0)

    def _trail_check_immediate_trigger(
        self, group_id: str, grp: dict, lv: dict, last_price: float
    ) -> bool:
        """Decisão 6: se SL trigger inicial já foi atravessado quando trailing
        é ativado, dispara ordem market imediata.

        Retorna True se disparou, False caso contrário."""
        side = grp["side"]
        trig = lv.get("sl_trigger")
        # Detecção extraída para validator puro (testável sem ctypes).
        if not trail_should_immediate_trigger(side, last_price, trig):
            return False
        log.info(
            "trailing.immediate_trigger group=%s lv=%d last=%.4f trigger=%.4f side=%d",
            group_id,
            lv["idx"],
            last_price,
            float(trig),
            side,
        )
        # Cancela SL pending (se existe) e envia market do lado oposto pra fechar
        if lv.get("sl_order_id") and lv.get("sl_status") == "sent":
            self.cancel_order({"local_order_id": lv["sl_order_id"], "env": grp["env"]})
            lv["sl_status"] = "cancelled"
        side_str = "buy" if side == 1 else "sell"
        market_res = self._send_order_legacy(
            {
                "env": grp["env"],
                "ticker": grp["ticker"],
                "exchange": grp["exchange"],
                "is_daytrade": grp["is_daytrade"],
                "user_account_id": grp["user_account_id"],
                "portfolio_id": grp["portfolio_id"],
                "order_type": "market",
                "order_side": side_str,
                "price": -1,
                "stop_price": -1,
                "quantity": int(lv["qty"]),
                "strategy_id": f"oco_grp_{group_id[:8]}_lv{lv['idx']}_trail_imm",
            }
        )
        if market_res.get("ok"):
            lv["sl_order_id"] = market_res["local_order_id"]
            lv["sl_status"] = "sent"  # market deve fillar rápido
            self._db.execute(
                "UPDATE profit_oco_levels SET sl_order_id=%s, sl_status='sent', "
                "trail_high_water=%s, updated_at=NOW() WHERE level_id=%s",
                (market_res["local_order_id"], last_price, lv["level_id"]),
            )
            self._order_to_group[market_res["local_order_id"]] = (group_id, lv["idx"], "sl")
        return True

    def _trail_monitor_loop(self) -> None:
        """Background thread: pra cada level com is_trailing=true e SL aberto,
        atualiza SL via change_order quando high_water move favoravelmente.

        Roda a cada 1s — balance entre responsividade e overhead.
        """
        log.info("trail_monitor.started")
        if not hasattr(self, "_trail_last_log_ts"):
            self._trail_last_log_ts: dict[str, float] = {}
        while not self._stop_event.is_set():
            try:
                if not hasattr(self, "_oco_groups") or not self._oco_groups:
                    time.sleep(1.0)
                    continue
                groups_snap = list(self._oco_groups.items())
                for group_id, grp in groups_snap:
                    if grp["status"] not in ("active", "partial", "awaiting"):
                        continue
                    last = self._get_last_price(grp["ticker"])
                    if last is None or last <= 0:
                        # Log raro para detectar problema de feed
                        last_log = self._trail_last_log_ts.get(f"{group_id}:noprice", 0)
                        if time.time() - last_log > 30:
                            log.warning(
                                "trail.no_price group=%s ticker=%s — cache+DB sem cotacao",
                                group_id,
                                grp["ticker"],
                            )
                            self._trail_last_log_ts[f"{group_id}:noprice"] = time.time()
                        continue
                    # Heartbeat log periódico para observabilidade do trail
                    last_log = self._trail_last_log_ts.get(group_id, 0)
                    if time.time() - last_log > 15:
                        for _lv in grp["levels"]:
                            if _lv.get("is_trailing"):
                                log.info(
                                    "trail.tick group=%s lv=%d ticker=%s last=%.4f hw=%s sl=%s",
                                    group_id,
                                    _lv.get("idx", 0),
                                    grp["ticker"],
                                    last,
                                    _lv.get("trail_high_water"),
                                    _lv.get("sl_trigger"),
                                )
                        self._trail_last_log_ts[group_id] = time.time()
                    for lv in grp["levels"]:
                        if not lv.get("is_trailing"):
                            continue
                        # Awaiting → checa imediato (parent ainda nao fillou —
                        # ou seja, apenas armazena high_water em runtime)
                        if grp["status"] == "awaiting":
                            self._trail_compute_new_sl(grp["side"], last, lv)
                            continue
                        # Active/partial — só ajusta SE SL ativa nesse level
                        if not lv.get("sl_order_id") or lv.get("sl_status") != "sent":
                            continue

                        # Decisão 6: se SL trigger atual já foi cruzado → market imediato
                        if self._trail_check_immediate_trigger(group_id, grp, lv, last):
                            continue

                        # Calcula novo SL trigger
                        new_sl = self._trail_compute_new_sl(grp["side"], last, lv)
                        if new_sl is None:
                            continue
                        cur_trig = lv.get("sl_trigger")
                        if cur_trig is None:
                            continue
                        # Ratchet: só move SE favorecer (sell long: subir SL; buy short: descer SL)
                        moved = (grp["side"] == 2 and new_sl > float(cur_trig) + 0.01) or (
                            grp["side"] == 1 and new_sl < float(cur_trig) - 0.01
                        )
                        if not moved:
                            continue
                        # Trail R$: arredonda 2 decimais. Limit = trigger (stop-market emulado).
                        new_sl = round(new_sl, 2)
                        new_lim = round(new_sl, 2)
                        ret = self.change_order(
                            {
                                "env": grp["env"],
                                "local_order_id": lv["sl_order_id"],
                                "price": new_lim,
                                "stop_price": new_sl,
                                "quantity": int(lv["qty"]),
                            }
                        )
                        moved_ok = bool(ret.get("ok"))

                        # P7 fallback (28/abr): broker simulator rejeita change_order
                        # em ordens stop-limit (ret=-2147483645). Quando change falha,
                        # cancel+create novo SL. Mantém trailing funcional mesmo com
                        # broker degradado.
                        if not moved_ok:
                            # Cooldown 30s para evitar loop infinito + log spam quando
                            # cancel também falha (broker order not found, etc).
                            cd_ts = lv.get("_trail_fallback_cooldown_until", 0)
                            if time.time() < cd_ts:
                                continue
                            self._trail_fallback_count = (
                                getattr(self, "_trail_fallback_count", 0) + 1
                            )
                            log.warning(
                                "trailing.change_failed_fallback_to_cancel_create "
                                "group=%s lv=%d new_sl=%.4f ret=%s",
                                group_id,
                                lv["idx"],
                                new_sl,
                                ret.get("ret"),
                            )
                            cancel_ret = self.cancel_order(
                                {
                                    "env": grp["env"],
                                    "local_order_id": lv["sl_order_id"],
                                }
                            )
                            if not cancel_ret.get("ok"):
                                log.warning(
                                    "trailing.cancel_failed group=%s lv=%d sl_id=%d "
                                    "ret=%s — cooldown 30s",
                                    group_id,
                                    lv["idx"],
                                    lv["sl_order_id"],
                                    cancel_ret.get("ret"),
                                )
                                lv["_trail_fallback_cooldown_until"] = time.time() + 30
                            if cancel_ret.get("ok"):
                                # Cria novo SL stop-limit
                                side_str = "sell" if grp["side"] == 2 else "buy"
                                new_sl_res = self._send_order_legacy(
                                    {
                                        "env": grp["env"],
                                        "ticker": grp["ticker"],
                                        "exchange": grp["exchange"],
                                        "is_daytrade": grp["is_daytrade"],
                                        "user_account_id": grp.get("user_account_id"),
                                        "portfolio_id": grp.get("portfolio_id"),
                                        "order_type": "stop",
                                        "order_side": side_str,
                                        "price": new_lim,
                                        "stop_price": new_sl,
                                        "quantity": int(lv["qty"]),
                                        "strategy_id": (
                                            f"oco_grp_{group_id[:8]}_lv{lv['idx']}_sl_trail"
                                        ),
                                    }
                                )
                                if new_sl_res.get("ok"):
                                    old_sl_id = lv["sl_order_id"]
                                    lv["sl_order_id"] = new_sl_res["local_order_id"]
                                    self._order_to_group[lv["sl_order_id"]] = (
                                        group_id,
                                        lv["idx"],
                                        "sl",
                                    )
                                    self._order_to_group.pop(old_sl_id, None)
                                    self._db.execute(
                                        "UPDATE profit_oco_levels SET sl_order_id=%s, "
                                        "updated_at=NOW() WHERE level_id=%s",
                                        (lv["sl_order_id"], lv["level_id"]),
                                    )
                                    moved_ok = True
                                    log.info(
                                        "trailing.cancel_create group=%s lv=%d "
                                        "old_sl=%d new_sl_id=%d new_sl=%.4f",
                                        group_id,
                                        lv["idx"],
                                        old_sl_id,
                                        lv["sl_order_id"],
                                        new_sl,
                                    )
                                else:
                                    log.warning(
                                        "trailing.create_failed group=%s lv=%d err=%s "
                                        "— cooldown 30s",
                                        group_id,
                                        lv["idx"],
                                        new_sl_res.get("error"),
                                    )
                                    lv["_trail_fallback_cooldown_until"] = time.time() + 30

                        if moved_ok:
                            lv["sl_trigger"] = new_sl
                            lv["sl_limit"] = new_lim
                            self._db.execute(
                                "UPDATE profit_oco_levels SET sl_trigger=%s, sl_limit=%s, "
                                "trail_high_water=%s, updated_at=NOW() WHERE level_id=%s",
                                (new_sl, new_lim, lv["trail_high_water"], lv["level_id"]),
                            )
                            self._trail_adjust_count = getattr(self, "_trail_adjust_count", 0) + 1
                            log.info(
                                "trailing.adjusted group=%s lv=%d hw=%.4f new_sl=%.4f",
                                group_id,
                                lv["idx"],
                                lv["trail_high_water"],
                                new_sl,
                            )
            except Exception as e:
                log.warning("trail_monitor error: %s", e)
            time.sleep(1.0)

    def get_oco_group(self, group_id: str) -> dict:
        """Retorna estado do group + levels (lookup em memória)."""
        if not hasattr(self, "_oco_groups") or group_id not in self._oco_groups:
            return {"ok": False, "error": f"group {group_id} nao encontrado"}
        grp = self._oco_groups[group_id]
        return {"ok": True, "group_id": group_id, **grp}

    def list_oco_groups(self, status_filter: str | None = None) -> dict:
        """Lista groups em memória (opcionalmente filtrando status)."""
        if not hasattr(self, "_oco_groups"):
            return {"groups": []}
        out = []
        for gid, g in self._oco_groups.items():
            if status_filter and g["status"] != status_filter:
                continue
            out.append(
                {
                    "group_id": gid,
                    **{k: v for k, v in g.items() if k != "levels"},
                    "levels_count": len(g["levels"]),
                }
            )
        return {"groups": out, "count": len(out)}

    def cancel_oco_group(self, group_id: str) -> dict:
        """Cancela todas ordens abertas de um group."""
        if not hasattr(self, "_oco_groups") or group_id not in self._oco_groups:
            return {"ok": False, "error": f"group {group_id} nao encontrado"}
        grp = self._oco_groups[group_id]
        cancelled = 0
        for lv in grp["levels"]:
            if lv.get("tp_order_id") and lv.get("tp_status") == "sent":
                self.cancel_order({"local_order_id": lv["tp_order_id"], "env": grp["env"]})
                cancelled += 1
            if lv.get("sl_order_id") and lv.get("sl_status") == "sent":
                self.cancel_order({"local_order_id": lv["sl_order_id"], "env": grp["env"]})
                cancelled += 1
        if self._db is not None:
            self._db.execute(
                "UPDATE profit_oco_groups SET status='cancelled', completed_at=NOW(), "
                "updated_at=NOW() WHERE group_id=%s",
                (group_id,),
            )
        grp["status"] = "cancelled"
        log.info("oco_group.cancel_user group=%s cancelled=%d", group_id, cancelled)
        return {"ok": True, "group_id": group_id, "cancelled_orders": cancelled}

    def list_book(self, ticker: str = "") -> dict:
        """Retorna snapshot atual do book em memoria."""

        if ticker:
            book_data = self._book.get(ticker.upper())

            if not book_data:
                return {"ticker": ticker, "bids": [], "asks": [], "error": "sem dados"}

            def _side(side_dict):

                return [{"position": pos, **data} for pos, data in sorted(side_dict.items())]

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
            "market_connected": self._market_ok,
            "routing_connected": self._routing_ok,
            "login_ok": self._login_ok,
            "activate_ok": self._activate_ok,
            "subscribed_tickers": list(self._subscribed),
            "total_ticks": self._total_ticks,
            "total_orders": self._total_orders,
            "total_assets": self._total_assets,
            "db_queue_size": self._db_queue.qsize(),
            "db_connected": self._db is not None and self._db.is_connected,
        }

    def get_metrics(self) -> str:
        """Prometheus text exposition format (text/plain; version=0.0.4)."""
        db_ok = 1 if (self._db is not None and self._db.is_connected) else 0
        mkt_ok = 1 if self._market_ok else 0
        lines = [
            "# HELP profit_agent_total_ticks Total de ticks processados (acumulado)",
            "# TYPE profit_agent_total_ticks counter",
            f"profit_agent_total_ticks {self._total_ticks}",
            "# HELP profit_agent_total_orders Total de ordens processadas",
            "# TYPE profit_agent_total_orders counter",
            f"profit_agent_total_orders {self._total_orders}",
            "# HELP profit_agent_total_assets Total de ativos reconhecidos na sessao",
            "# TYPE profit_agent_total_assets gauge",
            f"profit_agent_total_assets {self._total_assets}",
            "# HELP profit_agent_db_queue_size Itens na fila de writes do DB",
            "# TYPE profit_agent_db_queue_size gauge",
            f"profit_agent_db_queue_size {self._db_queue.qsize()}",
            "# HELP profit_agent_subscribed_tickers Tickers em subscribe realtime",
            "# TYPE profit_agent_subscribed_tickers gauge",
            f"profit_agent_subscribed_tickers {len(self._subscribed)}",
            "# HELP profit_agent_market_connected 1 se DLL conectada ao mercado",
            "# TYPE profit_agent_market_connected gauge",
            f"profit_agent_market_connected {mkt_ok}",
            "# HELP profit_agent_db_connected 1 se TimescaleDB alcancavel",
            "# TYPE profit_agent_db_connected gauge",
            f"profit_agent_db_connected {db_ok}",
            "# HELP profit_agent_total_probes Total de chamadas /collect_history",
            "# TYPE profit_agent_total_probes counter",
            f"profit_agent_total_probes {self._total_probes}",
            "# HELP profit_agent_total_contaminations Contaminacoes detectadas (first/last != requested)",
            "# TYPE profit_agent_total_contaminations counter",
            f"profit_agent_total_contaminations {self._total_contaminations}",
            "# HELP profit_agent_probe_duration_seconds_sum Soma de duracao dos probes (s)",
            "# TYPE profit_agent_probe_duration_seconds_sum counter",
            f"profit_agent_probe_duration_seconds_sum {self._probe_duration_sum_s:.3f}",
            "# HELP profit_agent_probe_duration_seconds_count Contagem de probes mensurados",
            "# TYPE profit_agent_probe_duration_seconds_count counter",
            f"profit_agent_probe_duration_seconds_count {self._probe_duration_count}",
            "# HELP profit_agent_order_callbacks_total Callbacks recebidos do SetOrderCallback (DLL viva)",
            "# TYPE profit_agent_order_callbacks_total counter",
            f"profit_agent_order_callbacks_total {getattr(self, '_order_cb_count', 0)}",
            "# HELP profit_agent_oco_groups_active OCO groups carregados em memória (status active/awaiting/partial)",
            "# TYPE profit_agent_oco_groups_active gauge",
            f"profit_agent_oco_groups_active {len(getattr(self, '_oco_groups', {}))}",
            "# HELP profit_agent_oco_trail_adjusts_total Ratchets de trailing executados (sucesso, change_order ou cancel+create)",
            "# TYPE profit_agent_oco_trail_adjusts_total counter",
            f"profit_agent_oco_trail_adjusts_total {getattr(self, '_trail_adjust_count', 0)}",
            "# HELP profit_agent_oco_trail_fallbacks_total Vezes que change_order falhou e fallback cancel+create foi acionado",
            "# TYPE profit_agent_oco_trail_fallbacks_total counter",
            f"profit_agent_oco_trail_fallbacks_total {getattr(self, '_trail_fallback_count', 0)}",
        ]
        # Idade do último order_callback (gauge — alerta P5 visual no Grafana)
        last_cb = getattr(self, "_last_order_cb_at", None)
        age = (time.time() - last_cb) if last_cb else -1.0
        lines.extend(
            [
                "# HELP profit_agent_last_order_callback_age_seconds Segundos desde ultimo callback (-1 se nunca recebeu)",
                "# TYPE profit_agent_last_order_callback_age_seconds gauge",
                f"profit_agent_last_order_callback_age_seconds {age:.2f}",
            ]
        )
        return "\n".join(lines) + "\n"

    def _instrument_probe(self, body: dict, result: dict, duration_s: float) -> None:
        """Incrementa contadores Prometheus a partir de um probe /collect_history."""
        try:
            requested = (body or {}).get("ticker")
            first_t = (result.get("first") or {}).get("ticker") if result else None
            last_t = (result.get("last") or {}).get("ticker") if result else None
            ticks_n = (result or {}).get("ticks", 0) or 0
            contaminated = bool(
                ticks_n > 0
                and requested
                and ((first_t and first_t != requested) or (last_t and last_t != requested))
            )
            with self._probes_lock:
                self._total_probes += 1
                self._probe_duration_sum_s += float(duration_s)
                self._probe_duration_count += 1
                if contaminated:
                    self._total_contaminations += 1
        except Exception:
            pass  # métricas nunca devem derrubar o handler

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

        ticker = str(body.get("ticker", "WINFUT")).strip().split(":")[0]

        exchange = str(body.get("exchange", "B"))

        dt_start = str(body.get("dt_start", "09/04/2026 09:00:00"))

        dt_end = str(body.get("dt_end", "09/04/2026 18:00:00"))

        timeout = int(body.get("timeout", 180))

        # ── PATCH contaminacao (17/abr/2026) ──────────────────────────────────
        # Bugs corrigidos:
        #   1. V1 callback global captura realtime de outros tickers durante wait
        #   2. _db_queue.put_nowait polui profit_ticks com time=now
        #   3. Sem filtro de ticker
        #   4. Sem filtro de janela temporal
        # Estrategia:
        #   - self._collecting_history_ticker marca modo historico
        #   - _win_start / _win_end delimitam janela aceita (margem +/-12h p/ TZ)
        #   - callbacks V1/V2 descartam trades fora desses filtros
        def _parse_hist_window(s):
            from datetime import datetime as _dtw

            s = s.strip()
            for _fmt in (
                "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
            ):
                try:
                    return _dtw.strptime(s, _fmt).replace(tzinfo=UTC)
                except ValueError:
                    pass
            try:
                return _dtw.fromisoformat(s).replace(tzinfo=UTC)
            except Exception:
                return None

        from datetime import timedelta as _td_win

        _win_start_parsed = _parse_hist_window(dt_start)
        _win_end_parsed = _parse_hist_window(dt_end)
        if _win_start_parsed and _win_end_parsed:
            _win_start = _win_start_parsed - _td_win(hours=12)
            _win_end = _win_end_parsed + _td_win(hours=12)
        else:
            _win_start = datetime(2000, 1, 1, tzinfo=UTC)
            _win_end = datetime(2099, 1, 1, tzinfo=UTC)
        _hist_ticker_up = ticker.upper()
        self._collecting_history_ticker = _hist_ticker_up
        log.info(
            "collect_history FILTERS ticker=%s window=%s→%s",
            _hist_ticker_up,
            _win_start.isoformat(),
            _win_end.isoformat(),
        )

        ERR = {
            0: "NL_OK",
            -2147483647: "NL_INTERNAL_ERROR",
            -2147483646: "NL_NOT_INITIALIZED",
            -2147483645: "NL_INVALID_ARGS",
            -2147483644: "NL_WAITING_SERVER",
        }

        NL_OK = 0

        TC_LAST_PACKET = 0x02

        TC_IS_EDIT = 0x01

        ticks = []

        done = threading.Event()

        # ── Configura restypes ────────────────────────────────────────────────

        try:
            self._dll.GetHistoryTrades.argtypes = [c_wchar_p, c_wchar_p, c_wchar_p, c_wchar_p]

            self._dll.GetHistoryTrades.restype = c_int

            self._dll.TranslateTrade.argtypes = [c_size_t, POINTER(TConnectorTrade)]

            self._dll.TranslateTrade.restype = c_int

            self._dll.SetHistoryTradeCallbackV2.restype = None

            self._dll.SetTradeCallbackV2.restype = None

            self._dll.SetTradeCallback.restype = None

            self._dll.SetSerieProgressCallback.restype = None

            self._dll.SetEnabledHistOrder.argtypes = [c_int]

            self._dll.SetEnabledHistOrder.restype = None

        except Exception as e:
            log.warning("collect_history setup_error e=%s", e)

        # ── Callback V2 (SetHistoryTradeCallbackV2 / SetTradeCallbackV2) ──────

        @WINFUNCTYPE(None, TConnectorAssetIdentifier, c_size_t, c_uint)
        def _cb_v2(asset_id, p_trade, flags):

            is_last = bool(flags & TC_LAST_PACKET)

            if not bool(flags & TC_IS_EDIT) and p_trade:
                trade = TConnectorTrade(Version=0)

                if self._dll.TranslateTrade(p_trade, byref(trade)) == NL_OK and trade.Price > 0:
                    st = trade.TradeDate

                    try:
                        td = datetime(
                            st.wYear,
                            st.wMonth,
                            st.wDay,
                            st.wHour,
                            st.wMinute,
                            st.wSecond,
                            tzinfo=UTC,
                        )

                    except ValueError:
                        td = datetime(2000, 1, 1, tzinfo=UTC)

                    # PATCH contaminacao: filtra ticker e janela (V2)
                    _tk_v2 = (asset_id.Ticker or ticker).upper()
                    if _tk_v2 != _hist_ticker_up:
                        if is_last:
                            done.set()
                        return
                    if td < _win_start or td > _win_end:
                        if is_last:
                            done.set()
                        return

                    ticks.append(
                        {
                            "src": "v2",
                            "ticker": asset_id.Ticker or ticker,
                            "trade_date": td.isoformat(),
                            "trade_number": int(trade.TradeNumber),
                            "price": trade.Price,
                            "quantity": int(trade.Quantity),
                            "volume": trade.Volume,
                            "trade_type": int(trade.TradeType),
                            "buy_agent": int(trade.BuyAgent),
                            "sell_agent": int(trade.SellAgent),
                        }
                    )

                    if len(ticks) % 1000 == 0:
                        log.info("collect_history v2 ticks=%d", len(ticks))

            if is_last:
                log.info("collect_history v2 TC_LAST_PACKET total=%d", len(ticks))

                done.set()

        # ── Callback V1 (SetTradeCallback — sobrepõe pos 8 DLLInitializeLogin)

        # Assinatura TNewTradeCallback (V1):

        # (asset: TAssetID*, date: wchar_p, trade_num: uint, price: double,

        #  vol: double, qty: int, buy: int, sell: int, type: int, edit: char)

        @WINFUNCTYPE(
            None,
            c_void_p,
            c_wchar_p,
            c_uint,
            c_double,
            c_double,
            c_int,
            c_int,
            c_int,
            c_int,
            c_char,
        )
        def _cb_v1(
            asset_ptr, date_str, trade_num, price, vol, qty, buy_agent, sell_agent, trade_type, edit
        ):

            if not asset_ptr or price <= 0:
                return

            try:
                import ctypes as _ct

                asset = _ct.cast(asset_ptr, _ct.POINTER(TAssetID)).contents

                ticker_v1 = asset.ticker or ticker

                # PATCH contaminacao: filtro de ticker (V1)
                if (ticker_v1 or "").upper() != _hist_ticker_up:
                    return

                # Parse "DD/MM/YYYY HH:mm:SS.ZZZ"

                if date_str and len(date_str) >= 19:
                    try:
                        td = datetime(
                            int(date_str[6:10]),  # year
                            int(date_str[3:5]),  # month
                            int(date_str[0:2]),  # day
                            int(date_str[11:13]),  # hour
                            int(date_str[14:16]),  # minute
                            int(date_str[17:19]),  # second
                            tzinfo=UTC,
                        )

                    except Exception:
                        td = datetime(2000, 1, 1, tzinfo=UTC)

                else:
                    td = datetime(2000, 1, 1, tzinfo=UTC)

                # PATCH contaminacao: filtro de janela temporal (V1)
                if td < _win_start or td > _win_end:
                    return

                ticks.append(
                    {
                        "src": "v1",
                        "ticker": ticker_v1,
                        "trade_date": td.isoformat(),
                        "trade_number": int(trade_num),
                        "price": price,
                        "quantity": int(qty),
                        "volume": vol,
                        "trade_type": int(trade_type),
                        "buy_agent": int(buy_agent),
                        "sell_agent": int(sell_agent),
                    }
                )

                if len(ticks) % 1000 == 0:
                    log.info("collect_history v1 ticks=%d", len(ticks))

                # PATCH contaminacao: NAO mais empurrar para _db_queue durante
                # modo historico — isso poluia profit_ticks com time=now para
                # trades historicos. Persistencia final acontece via INSERT em
                # batch no final do collect_history usando trade_date original.

            except Exception as e:
                log.debug("collect_history v1 error e=%s", e)

        # ── Progress callback (SetSerieProgressCallback — fim do histórico V1)

        @WINFUNCTYPE(None, TAssetID, c_int)
        def _progress_cb(asset_id, progress):

            log.info(
                "collect_history progress ticker=%s pct=%d", asset_id.ticker or ticker, progress
            )

            if progress >= 100:
                log.info("collect_history progress=100 → done total=%d", len(ticks))

                done.set()

        # Guarda refs contra GC

        self._hist_cb_v2_ref = _cb_v2

        self._hist_cb_v1_ref = _cb_v1

        self._hist_progress_ref = _progress_cb

        # ── Guarda callbacks originais ────────────────────────────────────────

        orig_trade_v2 = None

        try:
            cbs = getattr(self, "_callbacks", [])

            if len(cbs) > 6:
                orig_trade_v2 = cbs[6]

        except Exception:
            pass

        orig_init_refs = getattr(self, "_init_refs", [])

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

        log.info("collect_history GetHistoryTrades ticker=%s %s→%s", ticker, dt_start, dt_end)

        ret = self._dll.GetHistoryTrades(
            c_wchar_p(ticker),
            c_wchar_p(exchange),
            c_wchar_p(dt_start),
            c_wchar_p(dt_end),
        )

        ret_name = ERR.get(ret, f"UNKNOWN({ret})")

        log.info("collect_history GetHistoryTrades ret=%d (%s)", ret, ret_name)

        if ret != 0:
            self._restore_callbacks(orig_trade_v2, orig_v1)

            # PATCH contaminacao: limpa flag se abortamos cedo
            self._collecting_history_ticker = None

            return {"error": f"GetHistoryTrades: {ret_name}", "ret": ret}

        # ── Aguarda TC_LAST_PACKET ou nProgress=100 ───────────────────────────

        received = done.wait(timeout=timeout)

        if not received:
            log.warning("collect_history TIMEOUT ticks=%d", len(ticks))

        # ── PATCH estabilizacao — corrige race do done.set() prematuro ────────
        # progress=100 chega antes do ultimo batch V1 ser entregue. Esperar ate
        # len(ticks) estabilizar por 5 seg (max 30 seg total de espera extra).
        import time as _time_stab

        _prev_len = len(ticks)
        _stable_sec = 0
        _stab_start = _time_stab.time()
        while _stable_sec < 5:
            _time_stab.sleep(1)
            if len(ticks) == _prev_len:
                _stable_sec += 1
            else:
                _stable_sec = 0
                _prev_len = len(ticks)
            if _time_stab.time() - _stab_start > 30:
                log.warning("collect_history stab_timeout final=%d", len(ticks))
                break
        log.info("collect_history stabilized final=%d", len(ticks))

        # ── Restaura callbacks ────────────────────────────────────────────────

        self._restore_callbacks(orig_trade_v2, orig_v1)

        # PATCH contaminacao: limpa flag de modo historico
        self._collecting_history_ticker = None

        # ── Persiste em batch (executemany — muito mais rápido) ──────────────
        inserted = 0
        if ticks:
            from datetime import datetime as _dtt3
            import os as _os3

            import psycopg2 as _pg3

            def _parse_trade_dt(s: str):
                s = s.strip()
                for _fmt in (
                    "%d/%m/%Y %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%d",
                ):
                    try:
                        return _dtt3.strptime(s, _fmt)
                    except ValueError:
                        pass
                return _dtt3.fromisoformat(s)

            _dsn3 = _os3.getenv(
                "PROFIT_TIMESCALE_DSN",
                "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
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
                    rows3.append(
                        (
                            _t["ticker"],
                            _td,
                            _t["trade_number"],
                            _t["price"],
                            _t["quantity"],
                            _t["volume"],
                            _t["trade_type"],
                            _t["buy_agent"],
                            _t["sell_agent"],
                        )
                    )
                except Exception as _pe:
                    log.warning(
                        "collect_history parse_date_error date=%s e=%s", _t.get("trade_date"), _pe
                    )

            if rows3:
                try:
                    _conn3 = _pg3.connect(_dsn3)
                    _conn3.autocommit = False
                    _cur3 = _conn3.cursor()
                    CHUNK3 = 5000
                    for _i in range(0, len(rows3), CHUNK3):
                        _chunk = rows3[_i : _i + CHUNK3]
                        _cur3.executemany(UPSERT3, _chunk)
                        inserted += len(_chunk)
                        log.info(
                            "collect_history batch %d/%d", min(_i + CHUNK3, len(rows3)), len(rows3)
                        )
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

        clean_ticks = [{k: v for k, v in t.items() if k != "src"} for t in ticks]

        return {
            "status": "ok" if received else "timeout",
            "ticks": len(clean_ticks),
            "inserted": inserted,
            "v1_count": sum(1 for t in ticks if t.get("src") == "v1"),
            "v2_count": sum(1 for t in ticks if t.get("src") == "v2"),
            "first": clean_ticks[0] if clean_ticks else None,
            "last": clean_ticks[-1] if clean_ticks else None,
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

            init_refs = getattr(self, "_init_refs", [])

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

            init_refs = getattr(self, "_init_refs", [])

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

        # Mata zombies de boots anteriores ANTES de tentar bind (P6/O1 fix 28/abr).
        # NSSM restart pode deixar processo velho ainda LISTENING mesmo com PID novo
        # subindo, criando dois listeners em :8002 e quebrando state in-memory.
        _kill_zombie_agents(os.getpid(), port)

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

                elif self.path == "/metrics":
                    body = agent.get_metrics().encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                elif self.path == "/accounts":
                    self._send_json(
                        {
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
                        }
                    )

                elif self.path == "/tickers":
                    full = agent._db.list_tickers_full() if agent._db else []
                    # Enriquece com: subscribed (set DLL) + has_recent_data (ticks recentes) + last_tick_age_sec
                    _now = datetime.now(tz=UTC)
                    for _row in full:
                        _key = f"{_row.get('ticker', '')}:{_row.get('exchange', '')}"
                        _row["subscribed"] = _key in agent._subscribed
                        _last = agent._last_tick_at.get(_key)
                        if _last:
                            _age = int((_now - _last).total_seconds())
                            _row["last_tick_age_sec"] = _age
                            _row["has_recent_data"] = _age <= 600  # 10min = mercado ativo
                        else:
                            _row["last_tick_age_sec"] = None
                            _row["has_recent_data"] = False

                    self._send_json({"tickers": full, "count": len(full)})

                elif self.path == "/tickers/active":
                    active = agent._db.list_tickers_full(only_active=True) if agent._db else []
                    _now = datetime.now(tz=UTC)
                    for _row in active:
                        _key = f"{_row.get('ticker', '')}:{_row.get('exchange', '')}"
                        _row["subscribed"] = _key in agent._subscribed
                        _last = agent._last_tick_at.get(_key)
                        if _last:
                            _age = int((_now - _last).total_seconds())
                            _row["last_tick_age_sec"] = _age
                            _row["has_recent_data"] = _age <= 600
                        else:
                            _row["last_tick_age_sec"] = None
                            _row["has_recent_data"] = False

                    self._send_json({"tickers": active, "count": len(active)})

                elif self.path == "/health":
                    self._send_json({"ok": True})

                elif self.path == "/history/tickers":
                    tickers = agent._db.list_history_tickers() if agent._db else []

                    self._send_json({"tickers": tickers, "count": len(tickers)})

                elif self.path.startswith("/orders"):
                    from urllib.parse import parse_qs, urlparse

                    qs = parse_qs(urlparse(self.path).query)

                    self._send_json(
                        agent.list_orders(
                            ticker=qs.get("ticker", [""])[0],
                            status=qs.get("status", [""])[0],
                            env=qs.get("env", [""])[0],
                            limit=int(qs.get("limit", ["100"])[0]),
                        )
                    )

                elif self.path.startswith("/positions/dll"):
                    from urllib.parse import parse_qs as _pqs_dll, urlparse

                    _qs_dll = _pqs_dll(urlparse(self.path).query)

                    self._send_json(agent.get_positions_dll(_qs_dll.get("env", ["simulation"])[0]))

                elif self.path.startswith("/positions/assets"):
                    from urllib.parse import parse_qs as _pqs_a, urlparse

                    _qs_a = _pqs_a(urlparse(self.path).query)
                    self._send_json(
                        agent.enumerate_position_assets(_qs_a.get("env", ["simulation"])[0])
                    )

                elif self.path.startswith("/positions/dll"):
                    from urllib.parse import parse_qs as _pqs_d, urlparse

                    _qs_d = _pqs_d(urlparse(self.path).query)
                    self._send_json(agent.get_positions_dll(_qs_d.get("env", ["simulation"])[0]))

                elif self.path.startswith("/resolve_ticker/"):
                    from urllib.parse import parse_qs as _pqs_r, urlparse as _up_r

                    _p_r = _up_r(self.path)
                    _tk = _p_r.path.split("/resolve_ticker/", 1)[-1].upper().strip("/")
                    _qs_r = _pqs_r(_p_r.query)
                    _ex = _qs_r.get("exchange", ["B"])[0]
                    is_future = _tk in FUTURES_ALIASES or _tk[:3] in (
                        "WDO",
                        "WIN",
                        "IND",
                        "DOL",
                        "BIT",
                    )
                    if is_future:
                        _ex = "F"
                        _resolved = agent._resolve_active_contract(_tk, _ex)
                    else:
                        _resolved = _tk
                    self._send_json(
                        {
                            "original": _tk,
                            "resolved": _resolved,
                            "exchange": _ex,
                            "is_future": is_future,
                        }
                    )

                elif self.path.startswith("/position/"):
                    from urllib.parse import parse_qs as _pqs_p, urlparse

                    _p2 = urlparse(self.path)
                    _ticker = _p2.path.split("/position/", 1)[-1].upper().strip("/")
                    _qs_p = _pqs_p(_p2.query)
                    self._send_json(
                        agent.get_position_v2(
                            _ticker,
                            _qs_p.get("exchange", ["B"])[0],
                            _qs_p.get("env", ["simulation"])[0],
                            int(_qs_p.get("type", ["1"])[0]),
                        )
                    )

                elif self.path.startswith("/oco/status/"):
                    from urllib.parse import parse_qs as _pqs_oco, urlparse

                    _p_oco = urlparse(self.path)
                    _tp_id = int(_p_oco.path.split("/oco/status/", 1)[-1].strip("/") or 0)
                    _qs_oco = _pqs_oco(_p_oco.query)
                    _env_oco = _qs_oco.get("env", ["simulation"])[0]
                    self._send_json(agent.get_oco_status(_tp_id, _env_oco))

                elif self.path == "/oco/groups" or self.path.startswith("/oco/groups?"):
                    from urllib.parse import parse_qs as _pqs_g, urlparse as _up_g

                    _qs_g = _pqs_g(_up_g(self.path).query)
                    _filter = _qs_g.get("status", [None])[0]
                    self._send_json(agent.list_oco_groups(_filter))

                elif self.path == "/oco/state/reload":
                    n = agent._load_oco_state_from_db()
                    self._send_json({"ok": True, "groups_loaded": n})

                elif self.path.startswith("/oco/groups/"):
                    _gid = self.path.split("/oco/groups/", 1)[-1].strip("/")
                    self._send_json(agent.get_oco_group(_gid))

                elif self.path.startswith("/positions"):
                    from urllib.parse import parse_qs, urlparse

                    qs2 = parse_qs(urlparse(self.path).query)

                    self._send_json(agent.get_positions(qs2.get("env", ["simulation"])[0]))

                elif self.path.startswith("/ticks/"):
                    from urllib.parse import parse_qs as _pqs, urlparse

                    _p = urlparse(self.path)

                    _tkr = _p.path.split("/ticks/", 1)[-1].upper()

                    _ql = int(_pqs(_p.query).get("limit", ["100"])[0])

                    self._send_json(agent.query_ticks(_tkr, _ql))

                elif self.path.startswith("/assets/"):
                    _at = self.path.split("/assets/", 1)[-1].upper()

                    _ar = agent.query_assets(search=_at, limit=1)

                    self._send_json(
                        _ar["assets"][0] if _ar["assets"] else {"error": "nao encontrado"}
                    )

                elif self.path.startswith("/assets"):
                    from urllib.parse import parse_qs as _pqs2, urlparse

                    _aq = _pqs2(urlparse(self.path).query)

                    self._send_json(
                        agent.query_assets(
                            search=_aq.get("search", [""])[0],
                            sector=_aq.get("sector", [""])[0],
                            sec_type=int(_aq.get("type", ["0"])[0]),
                            limit=int(_aq.get("limit", ["200"])[0]),
                        )
                    )

                elif self.path == "/summary":
                    self._send_json(agent.query_daily_summary())

                elif self.path == "/stream/ticks":
                    import queue as _qmod

                    self.send_response(200)

                    self.send_header("Content-Type", "text/event-stream")

                    self.send_header("Cache-Control", "no-cache")

                    self.send_header("Connection", "keep-alive")

                    self.end_headers()

                    _cq = _qmod.Queue(maxsize=500)

                    with agent._sse_lock:
                        agent._sse_clients.append(_cq)

                    try:
                        while True:
                            try:
                                _d = _cq.get(timeout=15)

                                self.wfile.write(("data: " + _d + "\n\n").encode())

                                self.wfile.flush()

                            except _qmod.Empty:
                                self.wfile.write(b": heartbeat\n\n")

                                self.wfile.flush()

                    except Exception:
                        pass

                    finally:
                        with agent._sse_lock:
                            try:
                                agent._sse_clients.remove(_cq)

                            except ValueError:
                                pass

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

                if self.path == "/restart":
                    # Mata o processo; watchdog (NSSM) reinicia em 2-5s.
                    # Sem NSSM, o profit_agent fica offline ate start manual.
                    # Protecao de auth feita no proxy FastAPI (require_sudo).
                    # Decisao 28/abr: TerminateProcess em vez de os._exit(0) porque
                    # DLL ConnectorThread (C++ nativa) bloqueia exit limpo, deixando
                    # processo zombie + outro novo bind na mesma porta (ambos LISTENING).
                    import os as _os_r
                    import threading as _th_r

                    self._send_json({"ok": True, "message": "restarting"})
                    log.warning("profit_agent.restart_requested via_http")

                    def _exit_soon():
                        import time as _tm_r

                        _tm_r.sleep(0.5)  # deixa resposta HTTP chegar no cliente
                        _hard_exit(0)

                    _th_r.Thread(target=_exit_soon, daemon=True).start()
                    return

                if self.path == "/order/send":
                    self._send_json(agent._send_order_legacy(body))

                elif self.path == "/order/cancel":
                    self._send_json(agent.cancel_order(body))

                elif self.path == "/order/cancel_all":
                    self._send_json(agent.cancel_all_orders(body))

                elif self.path == "/order/change":
                    self._send_json(agent.change_order(body))

                elif self.path == "/order/oco":
                    self._send_json(agent.send_oco_order(body))

                elif self.path == "/order/attach_oco":
                    self._send_json(agent.attach_oco(body))

                elif self.path.startswith("/oco/groups/") and self.path.endswith("/cancel"):
                    _gid = self.path.split("/")[3]
                    self._send_json(agent.cancel_oco_group(_gid))

                elif self.path == "/order/zero_position":
                    self._send_json(agent.zero_position(body))

                elif self.path == "/subscribe":
                    self._send_json(agent.subscribe_ticker(body))

                elif self.path == "/tickers/add":
                    # Body: {"ticker":"WINFUT","exchange":"F","active":true,

                    #        "subscribe_book":false,"priority":10,"notes":"..."}

                    if not agent._db or not agent._db._ensure_connected():
                        self._send_json({"error": "db_unavailable"}, 503)

                    else:
                        tkr = body.get("ticker", "").upper()

                        exch = body.get("exchange", "B").upper()

                        ok = agent._db.upsert_ticker(
                            ticker=tkr,
                            exchange=exch,
                            active=bool(body.get("active", True)),
                            subscribe_book=bool(body.get("subscribe_book", False)),
                            priority=int(body.get("priority", 0)),
                            notes=body.get("notes", ""),
                        )

                        # Subscreve em tempo real se active=True

                        _dll_ok, _dll_ret = (True, 0)
                        if ok and body.get("active", True):
                            _dll_ok, _dll_ret = agent._subscribe(tkr, exch)

                        self._send_json(
                            {
                                "ok": ok,
                                "ticker": tkr,
                                "exchange": exch,
                                "dll_subscribed": _dll_ok,
                                "dll_ret": _dll_ret,
                            }
                        )

                elif self.path == "/tickers/remove":
                    self._send_json(agent.unsubscribe_ticker(body))

                elif self.path == "/tickers/toggle":
                    if not agent._db or not agent._db._ensure_connected():
                        self._send_json({"error": "db_unavailable"}, 503)

                    else:
                        _tkr = body.get("ticker", "").upper()

                        _exch = body.get("exchange", "B").upper()

                        _act = bool(body.get("active", True))

                        _ok = agent._db.toggle_ticker(_tkr, _exch, _act)

                        _dll_ok, _dll_ret = (True, 0)
                        if _ok and _act:
                            _dll_ok, _dll_ret = agent._subscribe(_tkr, _exch)

                        self._send_json(
                            {
                                "ok": _ok,
                                "ticker": _tkr,
                                "active": _act,
                                "dll_subscribed": _dll_ok,
                                "dll_ret": _dll_ret,
                            }
                        )

                elif self.path == "/collect_history":
                    _t0 = time.time()
                    _res = agent.collect_history(body)
                    agent._instrument_probe(body, _res, time.time() - _t0)
                    self._send_json(_res)

                elif self.path == "/history/tickers/add":
                    # Body: {"ticker":"WINFUT","exchange":"F","active":true,

                    #        "collect_from":"2026-01-01 09:00:00","notes":"..."}

                    if not agent._db or not agent._db._ensure_connected():
                        self._send_json({"error": "db_unavailable"}, 503)

                    else:
                        ok = agent._db.upsert_history_ticker(
                            body.get("ticker", "").upper(),
                            body.get("exchange", "B").upper(),
                            bool(body.get("active", True)),
                            body.get("collect_from", "2026-01-01 00:00:00"),
                            body.get("notes", ""),
                        )

                        self._send_json({"ok": ok})

                elif self.path == "/history/tickers/toggle":
                    # Body: {"ticker":"WINFUT","exchange":"F","active":false}

                    if not agent._db or not agent._db._ensure_connected():
                        self._send_json({"error": "db_unavailable"}, 503)

                    else:
                        ok = agent._db.toggle_history_ticker(
                            body.get("ticker", "").upper(),
                            body.get("exchange", "B").upper(),
                            bool(body.get("active", True)),
                        )

                        self._send_json({"ok": ok})

                elif self.path == "/history/collect_all":
                    # Coleta todos os ativos active=True da tabela

                    # Body opcional: {"timeout": 300}

                    if not agent._db or not agent._db._ensure_connected():
                        self._send_json({"error": "db_unavailable"}, 503)

                        return

                    active_tickers = agent._db.get_active_history_tickers()

                    if not active_tickers:
                        self._send_json({"error": "no_active_tickers"})

                        return

                    timeout_each = int(body.get("timeout", 180))

                    results = []

                    for tkr, exch, collect_from in active_tickers:
                        from datetime import datetime

                        # Usa last_collected_to como dt_start se disponível,

                        # senão usa collect_from

                        dt_start = body.get(
                            "dt_start",
                            collect_from.strftime("%d/%m/%Y 09:00:00")
                            if hasattr(collect_from, "strftime")
                            else str(collect_from)[:10].replace("-", "/"),
                        )

                        dt_end = body.get("dt_end", datetime.now(UTC).strftime("%d/%m/%Y 18:00:00"))

                        r = agent.collect_history(
                            {
                                "ticker": tkr,
                                "exchange": exch,
                                "dt_start": dt_start,
                                "dt_end": dt_end,
                                "timeout": timeout_each,
                            }
                        )

                        results.append({"ticker": tkr, "exchange": exch, **r})

                    self._send_json({"results": results, "count": len(results)})

                else:
                    self._send_json({"error": "not found"}, 404)

        from http.server import ThreadingHTTPServer

        # Bind configurável via PROFIT_AGENT_BIND (default 0.0.0.0 desde 01/mai/2026
        # — necessário pro Engine WSL2 puro alcançar host:8002. Antes era
        # 127.0.0.1 hardcoded; Docker Desktop fazia magica VPNkit que resolvia.
        # Pra restringir a localhost only (ambiente sem WSL ou paranoia), set
        # PROFIT_AGENT_BIND=127.0.0.1 no env do NSSM service.
        bind_host = os.getenv("PROFIT_AGENT_BIND", "0.0.0.0")
        server = ThreadingHTTPServer((bind_host, port), Handler)

        log.info("http_server.bound host=%s port=%d", bind_host, port)
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

            log.info(
                "heartbeat ticks=%d orders=%d assets=%d queue=%d",
                self._total_ticks,
                self._total_orders,
                self._total_assets,
                self._db_queue.qsize(),
            )

    # ------------------------------------------------------------------

    # Shutdown

    # ------------------------------------------------------------------

    def stop(self) -> None:

        log.info("profit_agent.stopping")

        self._stop_event.set()

        # C1: drena buffer Kafka antes de finalizar DLL — evita perder ticks
        # ja produzidos mas nao entregues ao broker.
        if self._kafka_producer.enabled:
            unflushed = self._kafka_producer.flush(timeout_s=5.0)
            if unflushed:
                log.warning("kafka.flush_unflushed count=%d", unflushed)

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

    signal.signal(signal.SIGINT, _handle_signal)

    agent.start()


if __name__ == "__main__":
    main()
