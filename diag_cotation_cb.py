"""
Diagnóstico v5: assinaturas corretas baseadas em TNewTradeCallback/TNewTinyBookCallBack
do profitTypes.py — TAssetID passado by-value como primeiro argumento.
"""
import os, time, pathlib, threading
from ctypes import WinDLL, WINFUNCTYPE, Structure, c_wchar_p, c_double, c_int, c_uint, c_ubyte

env_path = pathlib.Path(".env")
for line in env_path.read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

DLL_PATH = os.environ["PROFIT_DLL_PATH"]
ACT_KEY  = os.environ["PROFIT_ACTIVATION_KEY"]
USERNAME = os.environ["PROFIT_USERNAME"]
PASSWORD = os.environ["PROFIT_PASSWORD"]
TICKERS  = os.environ.get("PROFIT_TICKERS", "WDOFUT,WINFUT,PETR4").split(",")

class TAssetID(Structure):
    _fields_ = [("ticker", c_wchar_p),
                ("bolsa",  c_wchar_p),
                ("feed",   c_int)]

dll = WinDLL(DLL_PATH)
_market_event = threading.Event()

@WINFUNCTYPE(None, c_int, c_int)
def on_state(t, r):
    labels = {0: "Login", 1: "Routing", 2: "MarketData", 3: "MarketLogin"}
    print(f"  STATE t={t}({labels.get(t,'?')}) r={r}", flush=True)
    if t == 2 and r == 4:
        _market_event.set()

# SetChangeCotationCallback — usa TNewTradeCallback: (TAssetID, date, tradeNumber, price, vol, qtd, buyAgent, sellAgent, tradeType, bIsEdit)
@WINFUNCTYPE(None, TAssetID, c_wchar_p, c_uint, c_double, c_double, c_int, c_int, c_int, c_int, c_int)
def on_trade(asset, date, trade_num, price, vol, qtd, buy_agent, sell_agent, trade_type, is_edit):
    ticker = asset.ticker or "?"
    print(f"  TRADE: {ticker} | {date} | price={price:.2f} vol={vol:.0f} qty={qtd} type={trade_type}", flush=True)

# SetTinyBookCallback — usa TNewTinyBookCallBack: (TAssetID, price, qtd, side)
@WINFUNCTYPE(None, TAssetID, c_double, c_int, c_int)
def on_tiny_book(asset, price, qty, side):
    ticker = asset.ticker or "?"
    print(f"  BOOK: {ticker} {price:.2f} qty={qty} side={side}", flush=True)

# SetChangeCotationCallback provavelmente usa assinatura mais simples: (TAssetID, date_str, price)
@WINFUNCTYPE(None, TAssetID, c_wchar_p, c_double)
def on_cotation(asset, dt_str, price):
    ticker = asset.ticker or "?"
    print(f"  COTACAO: {ticker} | {dt_str} | {price:.2f}", flush=True)

def _subscribe_thread():
    connected = _market_event.wait(timeout=90)
    if not connected:
        print("TIMEOUT — market nunca conectou", flush=True)
        return
    print("  >> Thread: subscrevendo tickers...", flush=True)
    time.sleep(0.5)
    for ticker in TICKERS:
        ret = dll.SubscribeTicker(c_wchar_p(ticker), c_wchar_p("B"))
        print(f"     SubscribeTicker({ticker}) ret={ret}", flush=True)
    print("  >> Aguardando dados...", flush=True)

t = threading.Thread(target=_subscribe_thread, daemon=True)
t.start()

print(f"Inicializando DLL... tickers={TICKERS}", flush=True)
ret = dll.DLLInitializeLogin(
    c_wchar_p(ACT_KEY), c_wchar_p(USERNAME), c_wchar_p(PASSWORD),
    on_state,
    None, None, None, None, None, None, None, None, None, None,
)
print(f"DLLInitializeLogin ret={ret}", flush=True)

# Registra callbacks APÓS init (igual Delphi)
dll.SetChangeCotationCallback(on_cotation)
dll.SetTradeCallback(on_trade)
dll.SetTinyBookCallback(on_tiny_book)
print("Callbacks registrados. Aguardando 180s...", flush=True)

time.sleep(180)
print("Fim.")
