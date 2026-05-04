"""
ctypes Structures do profit_agent — extraidas em 01/mai/2026 (sessao limpeza).

Manual Nelogica ProfitDLL: tipos C declarados em Delphi, espelhados aqui via
ctypes.Structure pra interop Python <-> DLL Windows.

Decisoes:
  - Apenas Structures puras (zero callbacks) — `WINFUNCTYPE` permanece em
    profit_agent.py pq e' Windows-only e usado por callbacks runtime.
  - 17 Structures cobrem: identidade (Account/Asset/Order), comandos
    (Send/Change/Cancel/Zero), feed (Trade/PriceGroup), resultado de
    roteamento (TradingMessageResult), tempo (SystemTime).
  - `_TRADING_RESULT_STATUS` mapeia ResultCode (FIX-style) -> order_status
    interno. Tabela vincula a TConnectorTradingMessageResult via ResultCode.
  - Layout deve respeitar `sizeof()` exato verificado em
    tests/unit/workers/test_profit_agent_fixes.py (P4 fix bug):
      TConnectorOrderIdentifier == 24 bytes (Delphi compat)
      TConnectorOrder == 152 bytes
      Mudanca de campo aqui e' breaking change — rodar testes!

Re-exportadas em profit_agent.py (preserva API publica).
"""

from __future__ import annotations

from ctypes import (
    POINTER,
    WINFUNCTYPE,
    Structure,
    c_bool,
    c_double,
    c_int,
    c_int64,
    c_long,
    c_longlong,
    c_size_t,  # noqa: F401  (re-export pra paridade com import original)
    c_ubyte,
    c_uint,
    c_ushort,
    c_void_p,  # noqa: F401  (re-export)
    c_wchar,
    c_wchar_p,
)

# ── Tempo ──────────────────────────────────────────────────────────────────


class SystemTime(Structure):
    _fields_ = [
        ("wYear", c_ushort),
        ("wMonth", c_ushort),
        ("wDayOfWeek", c_ushort),
        ("wDay", c_ushort),
        ("wHour", c_ushort),
        ("wMinute", c_ushort),
        ("wSecond", c_ushort),
        ("wMilliseconds", c_ushort),
    ]


# ── Identidade (Asset/Account/Order) ───────────────────────────────────────


class TAssetID(Structure):
    _fields_ = [
        ("ticker", c_wchar_p),
        ("bolsa", c_wchar_p),
        ("feed", c_int),
    ]


class TConnectorAccountIdentifier(Structure):
    _fields_ = [
        ("Version", c_ubyte),
        ("BrokerID", c_int),
        ("AccountID", c_wchar_p),
        ("SubAccountID", c_wchar_p),
        ("Reserved", c_int64),
    ]


class TConnectorAccountIdentifierOut(Structure):
    _fields_ = [
        ("Version", c_ubyte),
        ("BrokerID", c_int),
        ("AccountID", c_wchar * 100),
        ("AccountIDLength", c_int),
        ("SubAccountID", c_wchar * 100),
        ("SubAccountIDLength", c_int),
        ("Reserved", c_int64),
    ]


class TConnectorAssetIdentifier(Structure):
    _fields_ = [
        ("Version", c_ubyte),
        ("Ticker", c_wchar_p),
        ("Exchange", c_wchar_p),
        ("FeedType", c_ubyte),
    ]


class TConnectorOrderIdentifier(Structure):
    _fields_ = [
        ("Version", c_ubyte),
        ("LocalOrderID", c_int64),
        ("ClOrderID", c_wchar_p),
    ]


# ── Order + Position ──────────────────────────────────────────────────────


class TConnectorOrder(Structure):
    """Ordem completa com status — usada no SetOrderCallback e EnumerateOrders."""

    _fields_ = [
        ("Version", c_ubyte),
        ("OrderID", TConnectorOrderIdentifier),
        ("AccountID", TConnectorAccountIdentifier),
        ("AssetID", TConnectorAssetIdentifier),
        ("Quantity", c_int64),
        ("TradedQuantity", c_int64),
        ("LeavesQuantity", c_int64),
        ("Price", c_double),
        ("StopPrice", c_double),
        ("AveragePrice", c_double),
        ("OrderSide", c_ubyte),
        ("OrderType", c_ubyte),
        ("OrderStatus", c_ubyte),
        ("ValidityType", c_ubyte),
    ]


class TConnectorAssetIdentifierOut(Structure):
    """Asset identifier de retorno (Ticker/Exchange como `c_wchar_p`
    pre-alocados pelo caller; DLL preenche). Usado por `GetOrderDetails`
    no fluxo 2-pass: 1ª chamada -> obter `*Length`, 2ª chamada com
    buffers do tamanho retornado."""

    _fields_ = [
        ("Version", c_ubyte),
        ("Ticker", c_wchar_p),
        ("TickerLength", c_int),
        ("Exchange", c_wchar_p),
        ("ExchangeLength", c_int),
        ("FeedType", c_ubyte),
    ]


class TConnectorOrderOut(Structure):
    """Order full details — retornado por `GetOrderDetails`. Inclui
    OrderStatus, AveragePrice, TextMessage etc, alem dos campos do
    TConnectorOrderIdentifier passado de input. Layout match Delphi
    (Exemplo Python da Nelogica)."""

    _fields_ = [
        ("Version", c_ubyte),
        ("OrderID", TConnectorOrderIdentifier),
        ("AccountID", TConnectorAccountIdentifierOut),
        ("AssetID", TConnectorAssetIdentifierOut),
        ("Quantity", c_int64),
        ("TradedQuantity", c_int64),
        ("LeavesQuantity", c_int64),
        ("Price", c_double),
        ("StopPrice", c_double),
        ("AveragePrice", c_double),
        ("OrderSide", c_ubyte),
        ("OrderType", c_ubyte),
        ("OrderStatus", c_ubyte),
        ("ValidityType", c_ubyte),
        ("Date", SystemTime),
        ("LastUpdate", SystemTime),
        ("CloseDate", SystemTime),
        ("ValidityDate", SystemTime),
        ("TextMessage", c_wchar_p),
        ("TextMessageLength", c_int),
        ("EventID", c_int64),
    ]


class TConnectorTradingAccountPosition(Structure):
    """Posição consolidada por ativo — populada via callback de posição."""

    _fields_ = [
        ("Version", c_ubyte),
        ("AccountID", TConnectorAccountIdentifier),
        ("AssetID", TConnectorAssetIdentifier),
        ("OpenQuantity", c_int64),
        ("OpenAveragePrice", c_double),
        ("OpenSide", c_ubyte),
        ("DailyAverageSellPrice", c_double),
        ("DailySellQuantity", c_int64),
        ("DailyAverageBuyPrice", c_double),
        ("DailyBuyQuantity", c_int64),
        ("DailyQuantityD1", c_int64),
        ("DailyQuantityD2", c_int64),
        ("DailyQuantityD3", c_int64),
        ("DailyQuantityBlocked", c_int64),
        ("DailyQuantityPending", c_int64),
        ("DailyQuantityAlloc", c_int64),
        ("DailyQuantityProvision", c_int64),
        ("DailyQuantity", c_int64),
        ("DailyQuantityAvailable", c_int64),
        ("PositionType", c_ubyte),
        ("EventID", c_int64),
    ]


# Tipo callback para EnumerateOrders — declaracao funciona em Linux (stdlib),
# call de fato so' e' usado em runtime Windows + DLL carregada.
TConnectorEnumerateOrdersProc = WINFUNCTYPE(c_bool, POINTER(TConnectorOrder), c_long)


# ── Comandos (Send/Change/Cancel/Zero) ─────────────────────────────────────


class TConnectorSendOrder(Structure):
    _fields_ = [
        ("Version", c_ubyte),
        ("AccountID", TConnectorAccountIdentifier),
        ("AssetID", TConnectorAssetIdentifier),
        ("Password", c_wchar_p),
        ("OrderType", c_ubyte),
        ("OrderSide", c_ubyte),
        ("Price", c_double),
        ("StopPrice", c_double),
        ("Quantity", c_int64),
        ("MessageID", c_int64),
    ]


class TConnectorChangeOrder(Structure):
    _fields_ = [
        ("Version", c_ubyte),
        ("AccountID", TConnectorAccountIdentifier),
        ("OrderID", TConnectorOrderIdentifier),
        ("Password", c_wchar_p),
        ("Price", c_double),
        ("StopPrice", c_double),
        ("Quantity", c_int64),
        ("MessageID", c_int64),
    ]


class TConnectorCancelOrder(Structure):
    _fields_ = [
        ("Version", c_ubyte),
        ("AccountID", TConnectorAccountIdentifier),
        ("OrderID", TConnectorOrderIdentifier),
        ("Password", c_wchar_p),
        ("MessageID", c_int64),
    ]


class TConnectorCancelAllOrders(Structure):
    _fields_ = [
        ("Version", c_ubyte),
        ("AccountID", TConnectorAccountIdentifier),
        ("Password", c_wchar_p),
    ]


class TConnectorZeroPosition(Structure):
    _fields_ = [
        ("Version", c_ubyte),
        ("AccountID", TConnectorAccountIdentifier),
        ("AssetID", TConnectorAssetIdentifier),
        ("Password", c_wchar_p),
        ("Price", c_double),
        ("PositionType", c_ubyte),
        ("MessageID", c_int64),
    ]


# ── Feed (Trade + Book) ────────────────────────────────────────────────────


class TConnectorTrade(Structure):
    _fields_ = [
        ("Version", c_ubyte),
        ("TradeDate", SystemTime),
        ("TradeNumber", c_uint),
        ("Price", c_double),
        ("Quantity", c_longlong),
        ("Volume", c_double),
        ("BuyAgent", c_int),
        ("SellAgent", c_int),
        ("TradeType", c_ubyte),
    ]


class TConnectorPriceGroup(Structure):
    _fields_ = [
        ("Version", c_ubyte),
        ("Price", c_double),
        ("Count", c_uint),
        ("Quantity", c_long),  # c_long = 32-bit no Windows (conforme manual Nelogica)
        ("PriceGroupFlags", c_uint),
    ]


# ── Routing result ─────────────────────────────────────────────────────────


class TConnectorTradingMessageResult(Structure):
    """Resultado de uma operacao de roteamento (aceite, rejeicao, fill)."""

    _fields_ = [
        ("Version", c_ubyte),
        ("BrokerID", c_int),
        ("OrderID", TConnectorOrderIdentifier),
        ("MessageID", c_int64),
        ("ResultCode", c_ubyte),
        ("Message", c_wchar_p),
        ("MessageLength", c_int),
    ]


# Mapa de ResultCode (FIX-style) -> order_status interno
_TRADING_RESULT_STATUS: dict[int, int] = {
    # TConnectorTradingMessageResultCode → estágio de roteamento
    0: 0,  # Starting
    1: 8,  # NotConnected → rejeitada
    2: 0,  # SentToHadesProxy
    3: 8,  # RejectedMercury → rejeitada
    4: 0,  # SentToHades
    5: 8,  # RejectedHades → rejeitada
    6: 0,  # SentToBroker
    7: 8,  # RejectedBroker → rejeitada
    8: 0,  # SentToMarket
    9: 8,  # RejectedMarket → rejeitada
    10: 0,  # Accepted (pendente no book)
    24: 8,  # BlockedByRisk → rejeitada
}


# c_bool re-exportado pra preservar paridade do bloco importado (usado em
# WINFUNCTYPE no profit_agent.py logo apos esses tipos).
__all__ = [
    "SystemTime",
    "TAssetID",
    "TConnectorAccountIdentifier",
    "TConnectorAccountIdentifierOut",
    "TConnectorAssetIdentifier",
    "TConnectorAssetIdentifierOut",
    "TConnectorOrderIdentifier",
    "TConnectorOrder",
    "TConnectorOrderOut",
    "TConnectorTradingAccountPosition",
    "TConnectorEnumerateOrdersProc",
    "TConnectorSendOrder",
    "TConnectorChangeOrder",
    "TConnectorCancelOrder",
    "TConnectorCancelAllOrders",
    "TConnectorZeroPosition",
    "TConnectorTrade",
    "TConnectorPriceGroup",
    "TConnectorTradingMessageResult",
    "_TRADING_RESULT_STATUS",
    "c_bool",
]
