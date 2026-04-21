# profit_dll.py  —  Sprint V3 fix: todas as argtypes declaradas
# MUDANCAS vs original:
#   1. Adicionados imports: c_int32, c_void_p
#   2. Bloco "Funcoes de inicializacao e market data" (DLLInitializeLogin,
#      DLLInitializeMarketLogin, DLLFinalize, SubscribeTicker,
#      UnsubscribeTicker, SubscribeOfferBook, GetOrders, GetLastDailyClose)
#   3. Bloco "Set*Callback" (14 funcoes) — sem argtypes causava corrupcao de pilha
#   4. GetAgentName: ordem dos argtypes documentada com comentario
# O restante (funcoes de roteamento) permanece identico ao original.

from ctypes import (
    POINTER,
    WinDLL,
    c_double,
    c_int,
    c_int64,
    c_long,
    c_longlong,
    c_size_t,
    c_ubyte,
    c_void_p,
    c_wchar_p,
)

from profitTypes import (
    SystemTime,
    TConnectorAccountIdentifier,
    TConnectorAccountIdentifierOut,
    TConnectorAssetIdentifier,
    TConnectorCancelAllOrders,
    TConnectorCancelOrder,
    TConnectorCancelOrders,
    TConnectorChangeOrder,
    TConnectorEnumerateAssetProc,
    TConnectorEnumerateOrdersProc,
    TConnectorOrderOut,
    TConnectorPriceGroup,
    TConnectorSendOrder,
    TConnectorTrade,
    TConnectorTradingAccountOut,
    TConnectorTradingAccountPosition,
    TConnectorZeroPosition,
)


def initializeDll(path: str) -> WinDLL:
    dll = WinDLL(path)

    # ── Inicializacao ─────────────────────────────────────────────────────────
    # DLLInitializeLogin(key, user, password,
    #   cb_state, cb_history?, cb_?, cb_account,
    #   cb_?, cb_daily, cb_?, cb_?, cb_?, cb_progress, cb_tinybook)
    # 14 parametros: 3 strings + 11 function pointers (c_void_p aceita None)
    dll.DLLInitializeLogin.argtypes = [
        c_wchar_p,
        c_wchar_p,
        c_wchar_p,  # key, user, password
        c_void_p,
        c_void_p,
        c_void_p,
        c_void_p,
        c_void_p,
        c_void_p,
        c_void_p,
        c_void_p,
        c_void_p,
        c_void_p,
        c_void_p,
    ]
    dll.DLLInitializeLogin.restype = c_int

    # DLLInitializeMarketLogin(key, user, password,
    #   cb_state, cb_daily, cb_?, cb_?, cb_?, cb_progress, cb_tinybook)
    # 11 parametros: 3 strings + 8 function pointers
    dll.DLLInitializeMarketLogin.argtypes = [
        c_wchar_p,
        c_wchar_p,
        c_wchar_p,
        c_void_p,
        c_void_p,
        c_void_p,
        c_void_p,
        c_void_p,
        c_void_p,
        c_void_p,
        c_void_p,
    ]
    dll.DLLInitializeMarketLogin.restype = c_int

    dll.DLLFinalize.argtypes = []
    dll.DLLFinalize.restype = c_int

    # ── Market data — subscricoes ─────────────────────────────────────────────
    dll.SubscribeTicker.argtypes = [c_wchar_p, c_wchar_p]
    dll.SubscribeTicker.restype = c_int

    dll.UnsubscribeTicker.argtypes = [c_wchar_p, c_wchar_p]
    dll.UnsubscribeTicker.restype = c_int

    dll.SubscribeOfferBook.argtypes = [c_wchar_p, c_wchar_p]
    dll.SubscribeOfferBook.restype = c_int

    # GetLastDailyClose(ticker, exchange, POINTER(c_double) out, flags) -> int
    dll.GetLastDailyClose.argtypes = [c_wchar_p, c_wchar_p, POINTER(c_double), c_int]
    dll.GetLastDailyClose.restype = c_int

    # GetOrders — resultado retorna via orderHistoryCallback
    dll.GetOrders.argtypes = [c_wchar_p, c_wchar_p, c_wchar_p, c_wchar_p]
    dll.GetOrders.restype = c_int

    # ── Set*Callback — TODOS declarados com c_void_p ─────────────────────────
    # Sem argtypes, o ctypes nao garante marshal correto do function pointer
    # em WinDLL stdcall, o que pode causar corrupcao de pilha silenciosa.
    for _fn in (
        "SetTradeCallbackV2",
        "SetPriceDepthCallback",
        "SetAssetListCallback",
        "SetAdjustHistoryCallbackV2",
        "SetAssetListInfoCallback",
        "SetAssetListInfoCallbackV2",
        "SetOfferBookCallbackV2",
        "SetOrderCallback",
        "SetOrderHistoryCallback",
        "SetInvalidTickerCallback",
        "SetAssetPositionListCallback",
        "SetBrokerAccountListChangedCallback",
        "SetBrokerSubAccountListChangedCallback",
        "SetTradingMessageResultCallback",
    ):
        getattr(dll, _fn).argtypes = [c_void_p]
        getattr(dll, _fn).restype = None

    # ── Roteamento — SendOrder / CancelOrder / etc. ───────────────────────────
    dll.SendSellOrder.restype = c_longlong
    dll.SendBuyOrder.restype = c_longlong
    dll.SendZeroPosition.restype = c_longlong
    dll.GetAgentNameByID.restype = c_wchar_p
    dll.GetAgentShortNameByID.restype = c_wchar_p
    dll.GetPosition.restype = POINTER(c_int)
    dll.SendMarketSellOrder.restype = c_int64
    dll.SendMarketBuyOrder.restype = c_int64

    dll.SendStopSellOrder.argtypes = [
        c_wchar_p,
        c_wchar_p,
        c_wchar_p,
        c_wchar_p,
        c_wchar_p,
        c_double,
        c_double,
        c_int,
    ]
    dll.SendStopSellOrder.restype = c_longlong

    dll.SendStopBuyOrder.argtypes = [
        c_wchar_p,
        c_wchar_p,
        c_wchar_p,
        c_wchar_p,
        c_wchar_p,
        c_double,
        c_double,
        c_int,
    ]
    dll.SendStopBuyOrder.restype = c_longlong

    dll.SendOrder.argtypes = [POINTER(TConnectorSendOrder)]
    dll.SendOrder.restype = c_int64

    dll.SendChangeOrderV2.argtypes = [POINTER(TConnectorChangeOrder)]
    dll.SendChangeOrderV2.restype = c_int

    dll.SendCancelOrderV2.argtypes = [POINTER(TConnectorCancelOrder)]
    dll.SendCancelOrderV2.restype = c_int

    dll.SendCancelOrdersV2.argtypes = [POINTER(TConnectorCancelOrders)]
    dll.SendCancelOrdersV2.restype = c_int

    dll.SendCancelAllOrdersV2.argtypes = [POINTER(TConnectorCancelAllOrders)]
    dll.SendCancelAllOrdersV2.restype = c_int

    dll.SendZeroPositionV2.argtypes = [POINTER(TConnectorZeroPosition)]
    dll.SendZeroPositionV2.restype = c_int64

    # ── Contas ────────────────────────────────────────────────────────────────
    dll.GetAccountCount.argtypes = []
    dll.GetAccountCount.restype = c_int

    dll.GetAccounts.argtypes = [c_int, c_int, c_int, POINTER(TConnectorAccountIdentifierOut)]
    dll.GetAccounts.restype = c_int

    dll.GetAccountDetails.argtypes = [POINTER(TConnectorTradingAccountOut)]
    dll.GetAccountDetails.restype = c_int

    dll.GetSubAccountCount.argtypes = [POINTER(TConnectorAccountIdentifier)]
    dll.GetSubAccountCount.restype = c_int

    dll.GetSubAccounts.argtypes = [
        POINTER(TConnectorAccountIdentifier),
        c_int,
        c_int,
        c_int,
        POINTER(TConnectorAccountIdentifierOut),
    ]
    dll.GetSubAccounts.restype = c_int

    dll.GetAccountCountByBroker.argtypes = [c_int]
    dll.GetAccountCountByBroker.restype = c_int

    dll.GetAccountsByBroker.argtypes = [
        c_int,
        c_int,
        c_int,
        c_int,
        POINTER(TConnectorAccountIdentifierOut),
    ]
    dll.GetAccountsByBroker.restype = c_int

    # ── Posicao ───────────────────────────────────────────────────────────────
    dll.GetPositionV2.argtypes = [POINTER(TConnectorTradingAccountPosition)]
    dll.GetPositionV2.restype = c_int

    # ── Ordens ────────────────────────────────────────────────────────────────
    dll.GetOrderDetails.argtypes = [POINTER(TConnectorOrderOut)]
    dll.GetOrderDetails.restype = c_int

    dll.HasOrdersInInterval.argtypes = [
        POINTER(TConnectorAccountIdentifier),
        SystemTime,
        SystemTime,
    ]
    dll.HasOrdersInInterval.restype = c_int

    dll.EnumerateOrdersByInterval.argtypes = [
        POINTER(TConnectorAccountIdentifier),
        c_ubyte,
        SystemTime,
        SystemTime,
        c_long,
        TConnectorEnumerateOrdersProc,
    ]
    dll.EnumerateOrdersByInterval.restype = c_int

    dll.EnumerateAllOrders.argtypes = [
        POINTER(TConnectorAccountIdentifier),
        c_ubyte,
        c_long,
        TConnectorEnumerateOrdersProc,
    ]
    dll.EnumerateAllOrders.restype = c_int

    dll.TranslateTrade.argtypes = [c_size_t, POINTER(TConnectorTrade)]
    dll.TranslateTrade.restype = c_int

    # ── Price Depth ───────────────────────────────────────────────────────────
    dll.SubscribePriceDepth.argtypes = [POINTER(TConnectorAssetIdentifier)]
    dll.SubscribePriceDepth.restype = c_int

    dll.UnsubscribePriceDepth.argtypes = [POINTER(TConnectorAssetIdentifier)]
    dll.UnsubscribePriceDepth.restype = c_int

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

    # ── Agentes ───────────────────────────────────────────────────────────────
    # ATENCAO: GetAgentName(brokerId, shortFlag, buffer, bufferLength)
    # A chamada correta e: dll.GetAgentName(brokerId, shortFlag, buf, bufLen)
    # Nao invertir brokerId com agentLength (bug encontrado em main.py da Nelogica)
    dll.GetAgentNameLength.argtypes = [c_int, c_int]
    dll.GetAgentNameLength.restype = c_int

    dll.GetAgentName.argtypes = [c_int, c_int, c_wchar_p, c_int]
    dll.GetAgentName.restype = c_int

    # ── Posicao de ativos ─────────────────────────────────────────────────────
    dll.EnumerateAllPositionAssets.argtypes = [
        POINTER(TConnectorAccountIdentifier),
        c_ubyte,
        c_long,
        TConnectorEnumerateAssetProc,
    ]
    dll.EnumerateAllPositionAssets.restype = c_int

    return dll
