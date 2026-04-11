"""
order_worker.py
Worker de envio de ordens via DLL Nelogica (ProfitDLL).

Uso:
    python order_worker.py --action buy  --ticker WINFUT --exchange F --qty 1 --price 197500 --account 12345 --broker 308
    python order_worker.py --action sell --ticker PETR4  --exchange B --qty 100 --price 48.90 --account 12345 --broker 308
    python order_worker.py --action cancel --clordid <id>
"""
from __future__ import annotations
import argparse, os, sys, time, threading
from ctypes import (
    WINFUNCTYPE, WinDLL, Structure, POINTER, byref,
    c_double, c_int, c_int32, c_int64, c_ubyte, c_uint, c_ushort, c_wchar_p,
)

if sys.platform != "win32":
    sys.exit("Requer Windows.")

import structlog
log = structlog.get_logger(__name__)

# ── Flags globais ─────────────────────────────────────────────────────────────
routing_done     = False
market_connected = False
_order_result: dict = {}
_order_event  = threading.Event()

# ── Tipos DLL ─────────────────────────────────────────────────────────────────
class TConnectorAccountIdentifier(Structure):
    _fields_ = [
        ("Version",    c_ubyte),
        ("BrokerID",   c_wchar_p),
        ("AccountID",  c_wchar_p),
        ("SubAccountID", c_wchar_p),
    ]

class TConnectorAssetIdentifier(Structure):
    _fields_ = [
        ("Version",  c_ubyte),
        ("Ticker",   c_wchar_p),
        ("Exchange", c_wchar_p),
        ("FeedType", c_ubyte),
    ]

class TConnectorSendOrder(Structure):
    _fields_ = [
        ("Version",   c_ubyte),
        ("AccountID", TConnectorAccountIdentifier),
        ("AssetID",   TConnectorAssetIdentifier),
        ("Password",  c_wchar_p),
        ("OrderType", c_ubyte),   # 0=Limite 1=Mercado 2=Stop
        ("OrderSide", c_ubyte),   # 0=Compra 1=Venda
        ("Price",     c_double),
        ("StopPrice", c_double),
        ("Quantity",  c_int64),
        ("MessageID", c_int64),   # V2
    ]

class TConnectorCancelOrder(Structure):
    _fields_ = [
        ("Version",   c_ubyte),
        ("AccountID", TConnectorAccountIdentifier),
        ("Password",  c_wchar_p),
        ("ClOrderID", c_wchar_p),
    ]

# ── Callbacks ─────────────────────────────────────────────────────────────────
StateCallbackType = WINFUNCTYPE(None, c_int, c_int)
OrderCallbackType = WINFUNCTYPE(None, c_int, c_wchar_p, c_wchar_p, c_double, c_int64, c_int, c_int)

@StateCallbackType
def state_callback(state: int, msg: int) -> None:
    global routing_done, market_connected
    print(f"[STATE] {state} {msg}", flush=True)
    if state == 2 and msg == 2:
        routing_done = True
        print("[OK] routing_done", flush=True)
    if state == 5 and msg == 5:
        market_connected = True
        print("[OK] market_connected", flush=True)
    if state == 0 and routing_done and not market_connected:
        market_connected = True
        print("[OK] market fallback", flush=True)

@OrderCallbackType
def order_callback(error: int, clordid: str, account: str, price: float, qty: int, side: int, status: int) -> None:
    global _order_result
    _order_result = {
        "error":   error,
        "clordid": clordid,
        "account": account,
        "price":   price,
        "qty":     qty,
        "side":    "BUY" if side == 0 else "SELL",
        "status":  status,
    }
    log.info("order.callback", **_order_result)
    _order_event.set()

# ── Init DLL ──────────────────────────────────────────────────────────────────
def init_dll(dll_path: str, key: str, user: str, pwd: str) -> WinDLL:
    dll = WinDLL(dll_path)
    dll.DLLInitializeLogin.restype  = c_int32
    dll.DLLFinalize.restype         = c_int32
    dll.SendOrder.restype           = c_int64
    dll.SendCancelOrderV2.restype   = c_int

    ret = dll.DLLInitializeLogin(
        c_wchar_p(key), c_wchar_p(user), c_wchar_p(pwd),
        state_callback, None, None,
        None, None, None, None, None, None, None, None,
    )
    if ret < 0:
        raise RuntimeError(f"DLLInitializeLogin falhou: {ret}")
    print(f"[INIT] ret={ret}", flush=True)

    # Registra callback de ordens
    dll.SetOrderCallback(order_callback)

    # Aguarda routing
    print("[WAIT] routing...", flush=True)
    for _ in range(60):
        if routing_done:
            break
        time.sleep(1)
    if not routing_done:
        raise TimeoutError("Timeout aguardando routing")

    # Aguarda market data (necessario para preco de referencia)
    print("[WAIT] market...", flush=True)
    for _ in range(30):
        if market_connected:
            break
        time.sleep(1)

    return dll

# ── Envio de ordem ────────────────────────────────────────────────────────────
def send_order(
    dll: WinDLL, *,
    ticker: str, exchange: str,
    side: str,           # "buy" | "sell"
    qty: int,
    price: float,
    account: str,
    broker: str,
    password: str,
    order_type: int = 0, # 0=Limite 1=Mercado
) -> dict:
    acct = TConnectorAccountIdentifier(
        Version=0,
        BrokerID=broker,
        AccountID=account,
        SubAccountID="",
    )
    asset = TConnectorAssetIdentifier(
        Version=0,
        Ticker=ticker.upper(),
        Exchange=exchange.upper(),
        FeedType=0,
    )
    order = TConnectorSendOrder(
        Version=2,
        AccountID=acct,
        AssetID=asset,
        Password=password,
        OrderType=order_type,
        OrderSide=0 if side.lower() == "buy" else 1,
        Price=price,
        StopPrice=0.0,
        Quantity=qty,
        MessageID=0,
    )
    _order_event.clear()
    ret = dll.SendOrder(byref(order))
    log.info("order.sent", local_id=ret, ticker=ticker, side=side, qty=qty, price=price)

    if ret < 0:
        raise RuntimeError(f"SendOrder falhou: ret={ret}")

    # Aguarda callback de confirmacao
    _order_event.wait(timeout=15)
    return {"local_id": ret, **_order_result}

# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Envio de ordens via DLL Nelogica")
    parser.add_argument("--action",   required=True, choices=["buy", "sell", "cancel"])
    parser.add_argument("--ticker",   default="WINFUT")
    parser.add_argument("--exchange", default="F")
    parser.add_argument("--qty",      type=int,   default=1)
    parser.add_argument("--price",    type=float, default=0.0)
    parser.add_argument("--account",  default=os.environ.get("PROFIT_ACCOUNT", ""))
    parser.add_argument("--broker",   default=os.environ.get("PROFIT_BROKER",  "308"))
    parser.add_argument("--clordid",  default="")
    args = parser.parse_args()

    dll_path = os.environ["PROFIT_DLL_PATH"]
    key      = os.environ["PROFIT_ACTIVATION_KEY"]
    user     = os.environ["PROFIT_USERNAME"]
    pwd      = os.environ["PROFIT_PASSWORD"]
    rot_pwd  = os.environ.get("PROFIT_ROUTING_PASSWORD", pwd)

    dll = init_dll(dll_path, key, user, pwd)

    try:
        if args.action in ("buy", "sell"):
            result = send_order(
                dll,
                ticker=args.ticker,
                exchange=args.exchange,
                side=args.action,
                qty=args.qty,
                price=args.price,
                account=args.account,
                broker=args.broker,
                password=rot_pwd,
            )
            print(f"\nResultado: {result}")
        elif args.action == "cancel":
            if not args.clordid:
                print("ERRO: --clordid obrigatorio para cancel")
                sys.exit(1)
            acct = TConnectorCancelOrder(
                Version=0,
                AccountID=TConnectorAccountIdentifier(Version=0, BrokerID=args.broker, AccountID=args.account, SubAccountID=""),
                Password=rot_pwd,
                ClOrderID=args.clordid,
            )
            ret = dll.SendCancelOrderV2(byref(acct))
            print(f"Cancel ret={ret}")
    finally:
        dll.DLLFinalize()
        print("[OK] DLLFinalize")

if __name__ == "__main__":
    main()
