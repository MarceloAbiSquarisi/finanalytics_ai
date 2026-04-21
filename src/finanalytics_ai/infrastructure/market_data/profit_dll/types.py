"""
infrastructure/market_data/profit_dll/types.py

Tipos ctypes da ProfitDLL64 — adaptado de profitTypes.py do exemplo Nelogica.
Este arquivo é importável em qualquer plataforma (sem WINFUNCTYPE).
A inicialização real da DLL requer Windows.
"""

from __future__ import annotations

from ctypes import (
    Structure,
    c_double,
    c_int,
    c_int64,
    c_ubyte,
    c_uint,
    c_ushort,
    c_wchar_p,
)
from enum import IntEnum

# ── Enums ─────────────────────────────────────────────────────────────────────


class OrderType(IntEnum):
    Market = 1
    Limit = 2
    Stop = 4


class OrderSide(IntEnum):
    Buy = 1
    Sell = 2


class OrderStatus(IntEnum):
    New = 0
    PartiallyFilled = 1
    Filled = 2
    Canceled = 4
    Rejected = 8
    Expired = 12
    Unknown = 200


class MarketState(IntEnum):
    Disconnected = 0
    Connecting = 1
    Waiting = 2
    NotLogged = 3
    Connected = 4


class ConnStateType(IntEnum):
    Login = 0
    Routing = 1
    MarketData = 2
    MarketLogin = 3


class LoginResult(IntEnum):
    Connected = 0
    Invalid = 1
    InvalidPass = 2
    BlockedPass = 3
    ExpiredPass = 4
    UnknownError = 200


class TradeType(IntEnum):
    CrossTrade = 1
    BuyAggression = 2
    SellAggression = 3
    Auction = 4
    Surveillance = 5
    Expit = 6
    OptionsExercise = 7
    OverTheCounter = 8
    DerivativeTerm = 9
    Index = 10
    BTC = 11
    OnBehalf = 12
    RLP = 13
    Unknown = 32


# Flags
TC_IS_EDIT = 1
TC_LAST_PACKET = 2


# ── Estruturas ────────────────────────────────────────────────────────────────


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


class TAssetID(Structure):
    """Identificador legado de ativo (callbacks antigos)."""

    _fields_ = [
        ("ticker", c_wchar_p),
        ("bolsa", c_wchar_p),
        ("feed", c_int),
    ]


class TConnectorAssetIdentifier(Structure):
    """Identificador moderno de ativo."""

    _fields_ = [
        ("Version", c_ubyte),
        ("Ticker", c_wchar_p),
        ("Exchange", c_wchar_p),
        ("FeedType", c_ubyte),
    ]


class TConnectorAssetIdentifierOut(Structure):
    _fields_ = [
        ("Version", c_ubyte),
        ("Ticker", c_wchar_p),
        ("TickerLength", c_int),
        ("Exchange", c_wchar_p),
        ("ExchangeLength", c_int),
        ("FeedType", c_ubyte),
    ]


class TConnectorPriceGroup(Structure):
    _fields_ = [
        ("Version", c_ubyte),
        ("Price", c_double),
        ("Count", c_uint),
        ("Quantity", c_int64),
        ("PriceGroupFlags", c_uint),
    ]


class TConnectorTrade(Structure):
    """Trade decodificado via TranslateTrade."""

    _fields_ = [
        ("Version", c_ubyte),
        ("TradeDate", SystemTime),
        ("TradeNumber", c_uint),
        ("Price", c_double),
        ("Quantity", c_int64),
        ("Volume", c_double),
        ("BuyAgent", c_int),
        ("SellAgent", c_int),
        ("TradeType", c_ubyte),
    ]


class TNewTradeCallback(Structure):
    """Estrutura do callback legado de trade (não usamos — preferimos V2)."""

    _fields_ = [
        ("assetId", TAssetID),
        ("date", c_wchar_p),
        ("tradeNumber", c_uint),
        ("price", c_double),
        ("vol", c_double),
        ("qtd", c_int),
        ("buyAgent", c_int),
        ("sellAgent", c_int),
        ("tradeType", c_int),
        ("bIsEdit", c_int),
    ]


class TNewDailyCallback(Structure):
    _fields_ = [
        ("tAssetIDRec", TAssetID),
        ("date", c_wchar_p),
        ("sOpen", c_double),
        ("sHigh", c_double),
        ("sLow", c_double),
        ("sClose", c_double),
        ("sVol", c_double),
        ("sAjuste", c_double),
        ("sMaxLimit", c_double),
        ("sMinLimit", c_double),
        ("sVolBuyer", c_double),
        ("sVolSeller", c_double),
        ("nQtd", c_int),
        ("nNegocios", c_int),
        ("nContratosOpen", c_int),
        ("nQtdBuyer", c_int),
        ("nQtdSeller", c_int),
        ("nNegBuyer", c_int),
        ("nNegSeller", c_int),
    ]


class TTheoreticalPriceCallback(Structure):
    _fields_ = [
        ("assetId", TAssetID),
        ("dTheoreticalPrice", c_double),
        ("nTheoreticalQtd", c_uint),
    ]


# Error codes
NL_OK = 0x00000000
NL_INTERNAL_ERROR = -2147483647
NL_NOT_INITIALIZED = NL_INTERNAL_ERROR + 1
NL_INVALID_ARGS = NL_NOT_INITIALIZED + 1
NL_WAITING_SERVER = NL_INVALID_ARGS + 1
NL_NO_LOGIN = NL_WAITING_SERVER + 1
NL_NO_LICENSE = NL_NO_LOGIN + 1

# Bolsas B3
EXCHANGE_BOVESPA = "B"
EXCHANGE_BMF = "F"
EXCHANGE_BCB = "A"
