"""
patch_trade_callback.py
-----------------------
Fix M1: O _trade_cb em start() usa assinatura minimal (c_size_t, c_size_t, c_int)
mas SetTradeCallbackV2 espera (TConnectorAssetIdentifier, c_size_t, c_uint).

Com a assinatura errada, asset_id_raw = Ticker ptr, trade_ptr = Exchange ptr (ERRADO).
O callback com assinatura correta recebe asset_id como struct by-value com .Ticker acessível.

Fix: adiciona _trade_cb_proper com assinatura correta logo após _trade_cb em start().
"""
from pathlib import Path
import sys

TARGET = Path("src/finanalytics_ai/infrastructure/market_data/profit_dll/client.py")

if not TARGET.exists():
    print(f"ERRO: {TARGET} não encontrado.")
    sys.exit(1)

content = TARGET.read_text(encoding="utf-8")
original = content

# Âncora: linha após self._cb_trade = _trade_cb
ANCHOR = "        self._cb_trade = _trade_cb           # mantém referência (evita GC ctypes)"

INSERTION = """        self._cb_trade = _trade_cb           # mantém referência (evita GC ctypes)

        # ── Callback com assinatura CORRETA para SetTradeCallbackV2 ───────────
        # O exemplo oficial Nelogica usa (TConnectorAssetIdentifier, c_size_t, c_uint)
        # passado by-value — asset_id.Ticker funciona diretamente.
        # _trade_cb minimal acima tem assinatura errada: lê Exchange ptr como trade_ptr.
        from finanalytics_ai.infrastructure.market_data.profit_dll.types import (
            TConnectorAssetIdentifier as _AI_v2,
            TConnectorTrade as _CT_v2,
        )
        from ctypes import WINFUNCTYPE as _WFT_v2, c_size_t as _csz_v2, c_uint as _cuint_v2
        from ctypes import byref as _byref_v2
        from datetime import datetime, timezone as _tz_v2

        _queue_v2 = self._tick_queue
        _loop_v2  = self._loop
        _dll_v2   = self._dll

        @_WFT_v2(None, _AI_v2, _csz_v2, _cuint_v2)
        def _trade_cb_v2(asset_id, trade_ptr, flags):
            try:
                ticker = asset_id.Ticker or ""
                if not ticker:
                    return
                trade = _CT_v2(Version=0)
                if not _dll_v2.TranslateTrade(_csz_v2(trade_ptr), _byref_v2(trade)):
                    return
                if trade.Price <= 0:
                    return
                from finanalytics_ai.infrastructure.market_data.profit_dll.types import PriceTick as _PT
                tick = _PT(
                    ticker=ticker,
                    exchange=asset_id.Exchange or "B",
                    price=trade.Price,
                    volume=trade.Volume,
                    quantity=int(trade.Quantity),
                    trade_number=int(trade.TradeNumber),
                    trade_type=int(trade.TradeType),
                    buy_agent=int(trade.BuyAgent),
                    sell_agent=int(trade.SellAgent),
                    timestamp=datetime.now(tz=_tz_v2.utc),
                    is_edit=bool(flags & 1),
                )
                _loop_v2.call_soon_threadsafe(_queue_v2.put_nowait, tick)
            except Exception:
                pass

        self._cb_trade = _trade_cb_v2  # sobrescreve minimal com assinatura correta"""

if ANCHOR not in content:
    print(f"ERRO: âncora não encontrada:\n{ANCHOR!r}")
    sys.exit(1)

content = content.replace(ANCHOR, INSERTION, 1)

if content == original:
    print("ERRO: nenhuma alteração feita")
    sys.exit(1)

TARGET.write_text(content, encoding="utf-8")
print(f"[ok] {TARGET} atualizado")

# Verificação
final = TARGET.read_text(encoding="utf-8")
checks = [
    ("_trade_cb_v2 criado",         "_trade_cb_v2" in final),
    ("assinatura correta",           "_WFT_v2(None, _AI_v2, _csz_v2, _cuint_v2)" in final),
    ("self._cb_trade = _trade_cb_v2","self._cb_trade = _trade_cb_v2" in final),
    ("TranslateTrade com not ret",   "if not _dll_v2.TranslateTrade" in final),
    ("_trade_cb minimal preservado", "self._cb_trade = _trade_cb" in final),
]
print("\n=== Verificação ===")
for label, ok in checks:
    print(f"  {'[ok]' if ok else '[!!]'} {label}")
