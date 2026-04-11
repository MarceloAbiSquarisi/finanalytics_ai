"""
patch_trade_diag.py
-------------------
Adiciona log de arquivo dentro do _trade_cb_v2 para diagnosticar:
1. Se o callback é chamado
2. Se asset_id.Ticker é válido
3. Se TranslateTrade retorna sucesso
4. Qual exceção ocorre
"""
from pathlib import Path
import sys

TARGET = Path("src/finanalytics_ai/infrastructure/market_data/profit_dll/client.py")

if not TARGET.exists():
    print(f"ERRO: {TARGET} não encontrado.")
    sys.exit(1)

content = TARGET.read_text(encoding="utf-8")

OLD = '''        @_WFT_v2(None, _AI_v2, _csz_v2, _cuint_v2)
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
                    timestamp=datetime.now(tz=_tz_v2.utc),
                    is_edit=bool(flags & 1),
                )
                _loop_v2.call_soon_threadsafe(_queue_v2.put_nowait, tick)
            except Exception:
                pass'''

NEW = '''        @_WFT_v2(None, _AI_v2, _csz_v2, _cuint_v2)
        def _trade_cb_v2(asset_id, trade_ptr, flags):
            import os as _os
            _log = r"C:\\Temp\\trade_diag.log"
            try:
                _os.makedirs(r"C:\\Temp", exist_ok=True)
                ticker = asset_id.Ticker or ""
                trade = _CT_v2(Version=0)
                translate_ret = _dll_v2.TranslateTrade(_csz_v2(trade_ptr), _byref_v2(trade))
                with open(_log, "a") as _f:
                    _f.write(f"ticker={ticker!r} ptr={trade_ptr} translate={translate_ret} price={trade.Price}\\n")
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
                    timestamp=datetime.now(tz=_tz_v2.utc),
                    is_edit=bool(flags & 1),
                )
                _loop_v2.call_soon_threadsafe(_queue_v2.put_nowait, tick)
            except Exception as _ex:
                try:
                    with open(_log, "a") as _f:
                        _f.write(f"EXCEPTION: {_ex}\\n")
                except Exception:
                    pass'''

if OLD not in content:
    print("ERRO: padrão não encontrado. Verificando o que há em client.py:")
    for i, line in enumerate(content.splitlines(), 1):
        if "_trade_cb_v2" in line:
            print(f"  L{i}: {line}")
    sys.exit(1)

content = content.replace(OLD, NEW, 1)
TARGET.write_text(content, encoding="utf-8")
print("[ok] Log diagnóstico adicionado em _trade_cb_v2")
print("Reinicie o worker e verifique: Get-Content C:\\Temp\\trade_diag.log -Wait")
