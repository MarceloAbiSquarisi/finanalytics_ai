"""
infrastructure/market_data/profit_dll/client.py

ProfitDLLClient — wrapper async-friendly para a ProfitDLL64 da Nelogica.

Arquitetura:
  A DLL dispara callbacks em uma thread própria (ConnectorThread).
  Callbacks devem ser rápidos — sem I/O, sem awaits.
  Dados chegam via callback → asyncio.Queue thread-safe → consumer async.

  ┌──────────────┐   ctypes callbacks    ┌─────────────────────┐
  │  ProfitDLL   │ ─────────────────────▶│  asyncio.Queue      │
  │ (ConnThread) │                       │  (thread-safe)      │
  └──────────────┘                       └────────┬────────────┘
                                                  │ await queue.get()
                                         ┌────────▼────────────┐
                                         │  _consume_loop()    │
                                         │  TimescaleWriter    │
                                         │  EventPublisher     │
                                         └─────────────────────┘

Restrições da DLL (manual seção 3.2):
  - Nunca chamar funções da DLL dentro de um callback
  - Callbacks compartilham a mesma fila interna → processamento leve
  - DLL é Windows-only (WinDLL / WINFUNCTYPE)

Uso:
    client = ProfitDLLClient(
        dll_path="C:/ProfitDLL64.dll",
        activation_key="...",
        username="...",
        password="...",
    )
    await client.start(loop)
    await client.subscribe_tickers(["PETR4", "VALE3", "WINFUT"])
    # Recebe ticks via on_trade callback ou consome da queue
    await client.stop()
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import sys
import threading
from typing import Any

from finanalytics_ai.observability.logging import get_logger

log = get_logger(__name__)

# ── Guard Windows-only ────────────────────────────────────────────────────────

if sys.platform != "win32":
    raise ImportError(
        "ProfitDLLClient requer Windows (ProfitDLL64.dll é uma WinDLL). "
        "Em Linux/Mac use NoOpProfitClient para testes."
    )

from ctypes import WINFUNCTYPE, WinDLL, byref, c_double, c_int32, c_int64, c_uint  # noqa: E402

from finanalytics_ai.infrastructure.market_data.profit_dll.types import (  # noqa: E402
    TAssetID,
    TConnectorAssetIdentifier,
)

# ── Tick domain object ────────────────────────────────────────────────────────


@dataclass
class PriceTick:
    """Trade em tempo real recebido via callback da ProfitDLL."""

    ticker: str
    exchange: str
    price: float
    volume: float
    quantity: int
    trade_number: int
    trade_type: int
    buy_agent: int
    sell_agent: int
    timestamp: datetime
    is_edit: bool = False
    source: str = "profit_dll"


@dataclass
class DailyBar:
    """Candle diário agregado recebido via callback."""

    ticker: str
    exchange: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    adjust: float
    quantity: int
    trades: int
    timestamp: datetime
    source: str = "profit_dll"


# ── Connection state ──────────────────────────────────────────────────────────


@dataclass
class ConnectionState:
    login_connected: bool = False
    market_connected: bool = False
    market_login_valid: bool = False
    routing_connected: bool = False

    @property
    def ready(self) -> bool:
        # market_connected=True so quando mercado esta aberto (result==4).
        # market_login_valid=True indica credenciais validas mesmo com mercado fechado.
        return self.market_login_valid


# ── ProfitDLLClient ───────────────────────────────────────────────────────────


class ProfitDLLClient:
    """
    Wrapper async-friendly para ProfitDLL64.

    Inicializa com DLLInitializeMarketLogin (somente Market Data).
    Callbacks são registrados e despacham para asyncio.Queue.
    """

    # Bolsa padrão B3 Bovespa
    DEFAULT_EXCHANGE = "B"

    def __init__(
        self,
        dll_path: str,
        activation_key: str,
        username: str,
        password: str,
        tick_queue_size: int = 10_000,
        preloaded_dll: Any = None,
    ) -> None:
        self._dll_path = dll_path
        self._activation_key = activation_key
        self._username = username
        self._password = password

        self._dll: WinDLL | None = preloaded_dll  # DLL ja conectada ou None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tick_queue: asyncio.Queue[PriceTick | DailyBar] = asyncio.Queue(
            maxsize=tick_queue_size
        )
        self._state = ConnectionState()
        self._subscribed: set[str] = set()
        self._on_tick_handlers: list[Callable[[PriceTick], Awaitable[None]]] = []
        self._on_daily_handlers: list[Callable[[DailyBar], Awaitable[None]]] = []
        self._consumer_task: asyncio.Task | None = None
        self._connected_event = asyncio.Event()

        # Mantém referências aos callbacks (evita GC)
        self._subscribe_event: threading.Event = threading.Event()
        self._cb_state: Any = None
        self._cb_trade: Any = None
        self._cb_daily: Any = None
        self._cb_progress: Any = None
        self._cb_tiny_book: Any = None
        self._cb_price_book: Any = None
        self._cb_offer_book: Any = None
        self._cb_history_trade: Any = None

    # ── Public API ────────────────────────────────────────────────────────────

    async def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Inicializa a DLL — usa preloaded_dll se disponivel, senao conecta do zero."""
        self._loop = loop or asyncio.get_running_loop()
        # Recria o Event dentro do loop ativo (Python 3.12 requer isso)
        self._connected_event = asyncio.Event()

        from ctypes import (
            WINFUNCTYPE as _WFTYPE,
            WinDLL as _WinDLL,
            c_int as _cint,
            c_wchar_p as _wstr,
        )

        # Se DLL ja foi pre-conectada (evita conflito ProactorEventLoop vs ConnectorThread)
        if self._dll is not None:
            log.info("profit_dll.using_preloaded_dll")
            self._state.market_connected = True
            self._subscribe_event.set()
            self._consumer_task = asyncio.create_task(self._consume_loop())
            return

        # Carrega DLL sem configurar restype (igual ao diagnostico)
        self._dll = _WinDLL(self._dll_path)

        # Callback MINIMAL identico ao diagnostico — sem Structures, sem c_int32
        _loop = self._loop
        _state = self._state
        _event = self._connected_event

        # Mapa de estados do manual Nelogica para logging
        _MARKET_STATES = {
            0: "MARKET_DISCONNECTED",
            1: "MARKET_CONNECTING",
            2: "MARKET_WAITING",
            3: "MARKET_NOT_LOGGED",
            4: "MARKET_CONNECTED",
        }

        # state_cb com latch para market_connected + file log para conn_type=2.
        import builtins as _bi2

        _log2 = r"C:\\Temp\\market_cb.log"

        @_WFTYPE(None, _cint, _cint)
        def _state_cb(t, r):
            try:
                with _bi2.open(_log2, "a") as _f2:
                    _f2.write("t=%d r=%d\n" % (t, r))
            except Exception:
                pass
            if t == 0:
                _state.login_connected = r == 0
            elif t == 1:
                _state.routing_connected = r >= 4
            elif t == 2:
                if r >= 4:
                    _state.market_connected = True
                    try:
                        self._subscribe_event.set()
                    except Exception:
                        pass
                # latch: nao resetar market_connected
            elif t == 3:
                _state.market_login_valid = r == 0

        self._cb_state = _state_cb  # evita GC

        # Chamada identica ao diagnostico
        # DLLInitializeLogin ativa o servidor de roteamento (conn_type=1)
        # que por sua vez ativa o stream de market data (conn_type=2).
        # DLLInitializeMarketLogin pulava o roteamento e exigia Profit Pro.
        # Referencia: exemplo oficial Delphi Nelogica (frmClientU.pas:362)
        from ctypes import (
            WINFUNCTYPE as _WFT2,
            byref as _byref2,
            c_size_t as _csz,
        )

        _loop_t = self._loop
        _queue_t = self._tick_queue
        _dll_t = self._dll

        @_WFT2(None, _csz, _csz, _cint)
        def _trade_cb(asset_id_raw, trade_ptr, flags):
            """Callback chamado pela ConnectorThread a cada trade em tempo real."""
            if _dll_t is None or _loop_t is None:
                return

            # Decodifica AssetIdentifier para obter Ticker e Exchange
            try:
                from finanalytics_ai.infrastructure.market_data.profit_dll.types import (
                    TConnectorAssetIdentifier as _AI,
                )

                # from_address() aceita int Python diretamente (cast nao aceita).
                _ai = _AI.from_address(asset_id_raw)
                ticker = _ai.Ticker or ""
                exchange = _ai.Exchange or "B"
            except Exception as _e:
                # full_login: asset_id pode ser passado by value (primeiro campo = ptr Ticker)
                try:
                    import ctypes as _ct

                    ticker = _ct.wstring_at(asset_id_raw) if asset_id_raw else ""
                    exchange = "B"
                except Exception:
                    ticker, exchange = "", "B"

            # Decodifica TConnectorTrade via TranslateTrade
            from ctypes import c_size_t as _csz2
            from datetime import datetime

            from finanalytics_ai.infrastructure.market_data.profit_dll.types import (
                TConnectorTrade as _CT,
            )

            trade = _CT()
            ret = _dll_t.TranslateTrade(_csz2(trade_ptr), _byref2(trade))
            if not ret:  # nao-zero = sucesso
                return

            tick = PriceTick(
                ticker=ticker,
                exchange=exchange,
                price=trade.Price,
                volume=trade.Volume,
                quantity=int(trade.Quantity),
                trade_number=int(trade.TradeNumber),
                trade_type=int(trade.TradeType),
                buy_agent=int(trade.BuyAgent),
                sell_agent=int(trade.SellAgent),
                timestamp=datetime.now(tz=UTC),
                is_edit=bool(flags & 1),
            )
            try:
                _loop_t.call_soon_threadsafe(_queue_t.put_nowait, tick)
            except Exception:
                pass

        self._cb_trade = _trade_cb  # mantém referência (evita GC ctypes)

        # ── Callback com assinatura CORRETA para SetTradeCallbackV2 ───────────
        # O exemplo oficial Nelogica usa (TConnectorAssetIdentifier, c_size_t, c_uint)
        # passado by-value — asset_id.Ticker funciona diretamente.
        # _trade_cb minimal acima tem assinatura errada: lê Exchange ptr como trade_ptr.
        from ctypes import (
            WINFUNCTYPE as _WFT_v2,
            byref as _byref_v2,
            c_size_t as _csz_v2,
            c_uint as _cuint_v2,
        )
        from datetime import datetime

        from finanalytics_ai.infrastructure.market_data.profit_dll.types import (
            TConnectorAssetIdentifier as _AI_v2,
            TConnectorTrade as _CT_v2,
        )

        _queue_v2 = self._tick_queue
        _loop_v2 = self._loop
        _dll_v2 = self._dll

        @_WFT_v2(None, _AI_v2, _csz_v2, _cuint_v2)
        def _trade_cb_v2(asset_id, trade_ptr, flags):
            import os as _os

            _log = r"C:\Temp\trade_diag.log"
            try:
                _os.makedirs(r"C:\Temp", exist_ok=True)
                ticker = asset_id.Ticker or ""
                trade = _CT_v2(Version=0)
                translate_ret = _dll_v2.TranslateTrade(_csz_v2(trade_ptr), _byref_v2(trade))
                with open(_log, "a") as _f:
                    _f.write(
                        f"ticker={ticker!r} ptr={trade_ptr} translate={translate_ret} price={trade.Price}\n"
                    )
                if not ticker or not translate_ret or trade.Price <= 0:
                    return
                tick = PriceTick(
                    ticker=ticker,
                    exchange=asset_id.Exchange or "B",
                    price=trade.Price,
                    volume=trade.Volume,
                    quantity=int(trade.Quantity),
                    trade_number=int(trade.TradeNumber),
                    trade_type=int(trade.TradeType),
                    buy_agent=int(trade.BuyAgent),
                    sell_agent=int(trade.SellAgent),
                    timestamp=datetime.now(tz=UTC),
                    is_edit=bool(flags & 1),
                )
                _loop_v2.call_soon_threadsafe(_queue_v2.put_nowait, tick)
            except Exception as _ex:
                try:
                    with open(_log, "a") as _f:
                        _f.write(f"EXCEPTION: {_ex}\n")
                except Exception:
                    pass

        self._cb_trade = _trade_cb_v2  # sobrescreve minimal com assinatura correta
        log.info("profit_dll.trade_callback_stored")

        # Registra callbacks ANTES do DLLInitializeLogin — igual ao diag que funcionou
        self._dll.SetTradeCallback(_trade_cb_v2)
        self._dll.SetChangeCotationCallback(_trade_cb_v2)
        log.info("profit_dll.callbacks_registered_before_init")

        # Inicializa via DLLInitializeLogin (login completo).
        # DLLInitializeMarketLogin exige assinatura API standalone.
        # DLLInitializeLogin ativa conn_type=1 (routing) que libera
        # conn_type=2 (market data) — igual ao exemplo oficial Delphi.

        ret = self._dll.DLLInitializeLogin(
            _wstr(self._activation_key),
            _wstr(self._username),
            _wstr(self._password),
            _state_cb,  # StateCallback
            None,  # HistoryCallback
            None,  # OrderChangeCallback
            None,  # AccountCallback
            None,  # TradeCallback (via SetTradeCallbackV2 apos routing)
            None,  # DailyCallback
            None,  # PriceBookCallback
            None,  # OfferBookCallback
            None,  # HistoryTradeCallback
            None,  # ProgressCallback
            None,  # TinyBookCallback
        )
        if ret != 0:
            raise RuntimeError(f"DLLInitializeMarketLogin falhou: {ret}")
        log.info("profit_dll.initialized", mode="full_login")

        # callbacks ja registrados antes do init

    async def wait_connected(self, timeout: float = 30.0) -> bool:
        """Aguarda market_login_valid via polling — sem threading primitives no callback."""
        steps = int(timeout * 2)
        for _ in range(steps):
            if self._state.market_login_valid:
                if self._consumer_task is None:
                    self._consumer_task = asyncio.create_task(self._consume_loop())
                return True
            await asyncio.sleep(0.5)
        log.warning("profit_dll.connect_timeout", timeout=timeout, state=self._state)
        return False

    def start_subscribe_thread(self, tickers: list[str], exchange: str = DEFAULT_EXCHANGE) -> None:
        """Thread separada: aguarda t=2 r=4 e chama SubscribeTicker fora do callback."""
        from ctypes import c_wchar_p as _cwp
        import threading as _threading
        import time as _time

        _dll, _log = self._dll, log
        _tevent = _threading.Event()
        self._subscribe_event = _tevent  # referencia para state_cb sinalizar

        def _sub():
            if not _tevent.wait(timeout=90):
                _log.warning("profit_dll.subscribe_thread_timeout")
                return
            _time.sleep(0.5)
            for ticker in tickers:
                ret = _dll.SubscribeTicker(_cwp(ticker), _cwp(exchange))
                _log.info("profit_dll.subscribed_via_thread", ticker=ticker, ret=ret)

        _threading.Thread(target=_sub, daemon=True).start()
        log.info("profit_dll.subscribe_thread_started", tickers=tickers)

    async def subscribe_tickers(self, tickers: list[str], exchange: str = DEFAULT_EXCHANGE) -> None:
        """Inscreve uma lista de tickers para receber trades em tempo real."""
        if self._dll is None:
            raise RuntimeError("DLL não inicializada. Chame start() primeiro.")

        for ticker in tickers:
            key = f"{ticker}:{exchange}"
            if key in self._subscribed:
                continue
            ret = self._dll.SubscribeTicker(ticker, exchange)
            if ret == 0:
                self._subscribed.add(key)
                log.info("profit_dll.subscribed", ticker=ticker, exchange=exchange)
            else:
                log.warning(
                    "profit_dll.subscribe_failed", ticker=ticker, exchange=exchange, ret=ret
                )

    async def unsubscribe_tickers(
        self, tickers: list[str], exchange: str = DEFAULT_EXCHANGE
    ) -> None:
        if self._dll is None:
            return
        for ticker in tickers:
            self._dll.UnsubscribeTicker(ticker, exchange)
            self._subscribed.discard(f"{ticker}:{exchange}")

    def add_tick_handler(self, handler: Callable[[PriceTick], Awaitable[None]]) -> None:
        """Registra handler async chamado para cada trade recebido."""
        self._on_tick_handlers.append(handler)

    def add_daily_handler(self, handler: Callable[[DailyBar], Awaitable[None]]) -> None:
        """Registra handler async chamado para cada candle diário."""
        self._on_daily_handlers.append(handler)

    async def stop(self) -> None:
        """Finaliza a DLL e cancela o consumer."""
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass

        if self._dll is not None:
            try:
                self._dll.DLLFinalize()
            except Exception as exc:
                log.warning("profit_dll.finalize_error", error=str(exc))
            self._dll = None

        log.info("profit_dll.stopped")

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def subscribed_tickers(self) -> set[str]:
        return set(self._subscribed)

    # ── DLL loading ───────────────────────────────────────────────────────────

    def _load_dll(self) -> WinDLL:
        dll = WinDLL(self._dll_path)

        # Configura restype das funções usadas
        dll.DLLInitializeMarketLogin.restype = c_int32
        dll.DLLFinalize.restype = c_int32
        dll.SubscribeTicker.restype = c_int32
        dll.UnsubscribeTicker.restype = c_int32

        self._dll.DLLInitializeLogin(
            _wstr(self._activation_key),
            _wstr(self._username),
            _wstr(self._password),
            _state_cb,  # StateCallback
            None,  # HistoryCallback
            None,  # OrderChangeCallback
            None,  # AccountCallback
            None,  # TradeCallback
            None,  # DailyCallback
            None,  # PriceBookCallback
            None,  # OfferBookCallback
            None,  # HistoryTradeCallback
            None,  # ProgressCallback
            None,  # TinyBookCallback
        )

        log.info("profit_dll.started", dll_path=self._dll_path)

        # ── Registra SetTradeCallbackV2 apos o init ───────────────────────────
        # Motivo: DLLInitializeMarketLogin recebe None para o trade callback
        # (callbacks diretos corrompem a ConnectorThread).
        # SetTradeCallbackV2 e chamado APOS a init para registrar o handler.
        dll.SetDailyCallback.restype = c_int32
        dll.SetStateCallback.restype = c_int32
        dll.SetTheoreticalPriceCallback.restype = c_int32

        return dll

    # ── Callback registration ─────────────────────────────────────────────────

    def _register_callbacks(self) -> None:
        """Cria e registra todos os callbacks necessários."""
        assert self._dll is not None

        # State callback MINIMAL — sem logging dentro do callback
        # Manual Nelogica: nenhum processamento pesado em callbacks
        _loop_ref = self._loop
        _state_ref = self._state
        _event_ref = self._connected_event

        @WINFUNCTYPE(None, c_int32, c_int32)
        def state_callback(conn_type: int, result: int) -> None:
            if conn_type == 0:
                _state_ref.login_connected = result == 0
            elif conn_type == 2:
                if result == 4:
                    _state_ref.market_connected = True  # latch
                try:
                    open(r"C:\\Temp\\market_cb2.log", "a").write(f"t={conn_type} r={result}\n")
                except:
                    pass
            elif conn_type == 3:
                _state_ref.market_login_valid = result == 0
            if _state_ref.ready and _loop_ref:
                _loop_ref.call_soon_threadsafe(_event_ref.set)
            if _loop_ref:
                _loop_ref.call_soon_threadsafe(
                    lambda ct=conn_type, r=result: log.info(
                        "profit_dll.state_callback", conn_type=ct, result=r
                    )
                )

        # Trade callback V2 (preferencial)
        # Assinatura: (AssetID, pTrade pointer, flags)
        from ctypes import POINTER, c_size_t

        @WINFUNCTYPE(None, TConnectorAssetIdentifier, c_size_t, c_uint)
        def trade_callback_v2(asset_id: Any, trade_ptr: int, flags: int) -> None:
            self._on_trade_v2(asset_id, trade_ptr, flags)

        # Daily callback
        @WINFUNCTYPE(
            None,
            TAssetID,  # assetId
            c_size_t,  # date ptr
            c_double,
            c_double,
            c_double,
            c_double,  # open/high/low/close
            c_double,
            c_double,  # vol, adjust
            c_double,
            c_double,  # maxLimit, minLimit
            c_double,
            c_double,  # volBuyer, volSeller
            c_int32,
            c_int32,
            c_int32,  # qtd, negocios, contratosOpen
            c_int32,
            c_int32,
            c_int32,
            c_int32,  # qtdBuyer, qtdSeller, negBuyer, negSeller
        )
        def daily_callback(
            asset_id,
            date_ptr,
            open_,
            high,
            low,
            close,
            vol,
            adjust,
            max_lim,
            min_lim,
            vol_buyer,
            vol_seller,
            qtd,
            negocios,
            contratos_open,
            qtd_buyer,
            qtd_seller,
            neg_buyer,
            neg_seller,
        ) -> None:
            self._on_daily(asset_id, open_, high, low, close, vol, adjust, qtd, negocios)

        # Progress (obrigatório na inicialização, pode ser noop)
        @WINFUNCTYPE(None, TAssetID, c_int32)
        def progress_callback(asset_id: Any, progress: int) -> None:
            pass  # noop — progresso de download histórico

        # TinyBook (obrigatório na inicialização)
        @WINFUNCTYPE(None, TAssetID, c_double, c_int32, c_int32)
        def tiny_book_callback(asset_id: Any, price: float, qty: int, side: int) -> None:
            pass  # noop para Market Data

        # PriceBook e OfferBook (obrigatórios na init, mas usamos SubscribePriceDepth depois)
        from ctypes import c_int

        @WINFUNCTYPE(
            None,
            TAssetID,
            c_int32,
            c_int32,
            c_int32,
            c_int32,
            c_int32,
            c_double,
            POINTER(c_int),
            POINTER(c_int),
        )
        def price_book_callback(*args) -> None:
            pass

        @WINFUNCTYPE(
            None,
            TAssetID,
            c_int32,
            c_int32,
            c_int32,
            c_int32,
            c_int32,
            c_int64,
            c_double,
            c_int32,
            c_int32,
            c_int32,
            c_int32,
            c_int32,
            c_size_t,
            POINTER(c_int),
            POINTER(c_int),
        )
        def offer_book_callback(*args) -> None:
            pass

        @WINFUNCTYPE(
            None,
            TAssetID,
            c_size_t,
            c_uint,
            c_double,
            c_double,
            c_int32,
            c_int32,
            c_int32,
            c_int32,
            c_int32,
        )
        def history_trade_callback(*args) -> None:
            pass

        # Guarda referências (evita GC)
        self._cb_state = state_callback
        self._cb_trade = trade_callback_v2  # callback correto: TConnectorAssetIdentifier by value
        self._cb_daily = daily_callback
        self._cb_progress = progress_callback
        self._cb_tiny_book = tiny_book_callback
        self._cb_price_book = price_book_callback
        self._cb_offer_book = offer_book_callback
        self._cb_history_trade = history_trade_callback

    def _initialize(self) -> None:
        """
        Chama DLLInitializeMarketLogin passando None para todos os callbacks
        opcionais (trade, daily, book, etc.) e registra-os via Set* apos a init.

        Motivo: callbacks com assinaturas incorretas passados diretamente ao
        DLLInitializeMarketLogin corrompem a ConnectorThread internamente,
        impedindo a transicao MARKET_CONNECTING -> MARKET_CONNECTED.
        O script de diagnostico confirmou que passar None e registrar via Set*
        resolve o problema (conectou em 2s).
        """
        assert self._dll is not None

        from ctypes import c_wchar_p as _wstr

        ret = self._dll.DLLInitializeLogin(
            _wstr(self._activation_key),
            _wstr(self._username),
            _wstr(self._password),
            self._cb_state,  # StateCallback
            None,  # HistoryCallback
            None,  # OrderChangeCallback
            None,  # AccountCallback
            None,  # TradeCallback
            None,  # NewDailyCallback
            None,  # PriceBookCallback
            None,  # OfferBookCallback
            None,  # HistoryTradeCallback
            None,  # ProgressCallback
            None,  # TinyBookCallback
        )

        if ret != 0:
            raise RuntimeError(f"DLLInitializeMarketLogin falhou: {ret}")

        log.info("profit_dll.initialized")

    def _on_state(self, conn_type: int, result: int) -> None:
        """Atualiza estado de conexão. Chamado na ConnectorThread."""
        # CONNECTION_STATE_LOGIN = 0, LOGIN_CONNECTED = 0
        if conn_type == 0:
            self._state.login_connected = result == 0
            log.info("profit_dll.login_state", connected=self._state.login_connected, result=result)
        # CONNECTION_STATE_MARKET_DATA = 2, MARKET_CONNECTED = 4
        elif conn_type == 2:
            if result == 4:
                self._state.market_connected = True  # latch
            log.info(
                "profit_dll.market_state", connected=self._state.market_connected, result=result
            )
        # CONNECTION_STATE_MARKET_LOGIN = 3, CONNECTION_ACTIVATE_VALID = 0
        elif conn_type == 3:
            self._state.market_login_valid = result == 0
            log.info("profit_dll.market_login", valid=self._state.market_login_valid, result=result)

        if self._state.ready and self._loop:
            self._loop.call_soon_threadsafe(self._connected_event.set)

    def _on_trade_v2(self, asset_id: Any, trade_ptr: int, flags: int) -> None:
        """
        Callback de trade em tempo real (ConnectorThread).
        Usa TranslateTrade para decodificar o ponteiro.
        """
        if self._dll is None or self._loop is None:
            return

        from ctypes import c_size_t

        from finanalytics_ai.infrastructure.market_data.profit_dll.types import TConnectorTrade

        trade = TConnectorTrade(Version=0)
        ret = self._dll.TranslateTrade(c_size_t(trade_ptr), byref(trade))
        if not ret:  # TranslateTrade retorna nao-zero em sucesso (igual exemplo oficial)
            return

        now = datetime.now(tz=UTC)
        ticker = getattr(asset_id, "Ticker", "") or ""
        exchange = getattr(asset_id, "Exchange", "B") or "B"

        tick = PriceTick(
            ticker=ticker,
            exchange=exchange,
            price=trade.Price,
            volume=trade.Volume,
            quantity=int(trade.Quantity),
            trade_number=int(trade.TradeNumber),
            trade_type=int(trade.TradeType),
            buy_agent=int(trade.BuyAgent),
            sell_agent=int(trade.SellAgent),
            timestamp=now,
            is_edit=bool(flags & 1),  # TC_IS_EDIT = 1
        )

        # Thread-safe: enfileira para o loop asyncio processar
        try:
            self._loop.call_soon_threadsafe(self._tick_queue.put_nowait, tick)
        except asyncio.QueueFull:
            log.warning("profit_dll.queue_full", ticker=ticker)

    def _on_daily(
        self,
        asset_id: Any,
        open_: float,
        high: float,
        low: float,
        close: float,
        vol: float,
        adjust: float,
        qtd: int,
        negocios: int,
    ) -> None:
        """Callback de candle diário (ConnectorThread)."""
        if self._loop is None:
            return

        ticker = getattr(asset_id, "ticker", "") or ""
        exchange = getattr(asset_id, "bolsa", "B") or "B"

        bar = DailyBar(
            ticker=ticker,
            exchange=exchange,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=vol,
            adjust=adjust,
            quantity=qtd,
            trades=negocios,
            timestamp=datetime.now(tz=UTC),
        )

        try:
            self._loop.call_soon_threadsafe(self._tick_queue.put_nowait, bar)
        except asyncio.QueueFull:
            pass

    # ── Consumer loop (asyncio) ───────────────────────────────────────────────

    async def _consume_loop(self) -> None:
        """
        Consome ticks da queue e chama os handlers registrados.
        Roda no event loop principal — pode fazer I/O async.
        """
        log.info("profit_dll.consumer_started")
        while True:
            try:
                item = await asyncio.wait_for(self._tick_queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                if isinstance(item, PriceTick):
                    for handler in self._on_tick_handlers:
                        await handler(item)
                elif isinstance(item, DailyBar):
                    for handler in self._on_daily_handlers:
                        await handler(item)
            except Exception as exc:
                log.error("profit_dll.handler_error", error=str(exc), exc_info=True)
            finally:
                self._tick_queue.task_done()

        log.info("profit_dll.consumer_stopped")
