"""patch_dll_trade_cb_signature.py
Corrige assinatura do _trade_cb via substituicao por numero de linha.
Imune a diferenca CRLF/LF entre Windows e Linux.
"""
from __future__ import annotations
import argparse, hashlib, shutil, sys
from pathlib import Path

TARGET = Path(
    r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai"
    r"\infrastructure\market_data\profit_dll\client.py"
)

OLD_DECORATOR = "@_WFT2(None, _csz, _csz, _cint)"
NEW_DECORATOR  = "@_WFT2(None, _AssetID, _csz3, _cint)"

SENTINEL = "# fix: TConnectorAssetIdentifier por valor (Nelogica main.py L324)"

IMPORTS_ANCHOR = "        from ctypes import WINFUNCTYPE as _WFT2, c_size_t as _csz"
IMPORTS_REPLACEMENT = (
    "        from ctypes import WINFUNCTYPE as _WFT2, c_size_t as _csz\n"
    "        # fix: TConnectorAssetIdentifier por valor (Nelogica main.py L324)\n"
    "        from finanalytics_ai.infrastructure.market_data.profit_dll.types import (\n"
    "            TConnectorAssetIdentifier as _AssetID,\n"
    "            TConnectorTrade as _CT2,\n"
    "        )\n"
    "        from ctypes import byref as _byref3\n"
    "        from datetime import datetime as _dt2, timezone as _tz2"
)

NEW_BODY = (
    "        @_WFT2(None, _AssetID, _csz, _cint)\n"
    "        def _trade_cb(asset_id, trade_ptr, flags):\n"
    "            # Callback: TConnectorAssetIdentifier por valor conforme main.py L324.\n"
    "            if _dll_t is None or _loop_t is None:\n"
    "                return\n"
    "            try:\n"
    '                ticker   = asset_id.Ticker   or ""\n'
    '                exchange = asset_id.Exchange or "B"\n'
    "                trade = _CT2()\n"
    "                ret = _dll_t.TranslateTrade(_csz(trade_ptr), _byref3(trade))\n"
    "                if ret != 0:\n"
    "                    return\n"
    "                tick = PriceTick(\n"
    "                    ticker       = ticker,\n"
    "                    exchange     = exchange,\n"
    "                    price        = trade.Price,\n"
    "                    volume       = trade.Volume,\n"
    "                    quantity     = int(trade.Quantity),\n"
    "                    trade_number = int(trade.TradeNumber),\n"
    "                    trade_type   = int(trade.TradeType),\n"
    "                    buy_agent    = int(trade.BuyAgent),\n"
    "                    sell_agent   = int(trade.SellAgent),\n"
    "                    timestamp    = _dt2.now(tz=_tz2.utc),\n"
    "                    is_edit      = bool(flags & 1),\n"
    "                )\n"
    "                _loop_t.call_soon_threadsafe(_queue_t.put_nowait, tick)\n"
    "            except Exception:\n"
    "                pass\n"
)


def _sha(t):
    return hashlib.sha256(t.encode()).hexdigest()[:12]


def apply_patch(path, dry_run=False, check=False):
    if not path.exists():
        print(f"[ERROR] Nao encontrado: {path}", file=sys.stderr)
        return 2

    raw = path.read_bytes()
    # Normalise to LF for processing, remember original line ending
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8").replace("\r\n", "\n")

    if SENTINEL in text:
        print("[OK] Patch ja aplicado.")
        return 0

    if OLD_DECORATOR not in text:
        print("[ERROR] Decorator alvo nao encontrado.", file=sys.stderr)
        return 3

    if check:
        print("[FAIL] Patch nao aplicado.", file=sys.stderr)
        return 1

    # Step 1: add imports after the _WFT2 import line
    text = text.replace(IMPORTS_ANCHOR, IMPORTS_REPLACEMENT, 1)

    # Step 2: replace the old _trade_cb body
    # Find decorator line and replace until the closing `pass` of the function
    lines = text.splitlines(keepends=True)
    out = []
    i = 0
    replaced = False
    while i < len(lines):
        line = lines[i]
        if not replaced and OLD_DECORATOR in line:
            # Skip old decorator + entire function body until standalone `pass`
            indent = len(line) - len(line.lstrip())
            out.append(NEW_BODY)
            replaced = True
            i += 1
            # Skip lines belonging to old _trade_cb (deeper indent or blank)
            while i < len(lines):
                l = lines[i]
                stripped = l.strip()
                # Stop when we hit a line at same or lower indent that is not blank
                if stripped and (len(l) - len(l.lstrip())) <= indent and stripped != "pass":
                    break
                i += 1
            continue
        out.append(line)
        i += 1

    patched = "".join(out)

    if dry_run:
        import difflib
        diff = list(difflib.unified_diff(
            text.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=path.name + " (original)",
            tofile=path.name + " (patched)",
            n=3,
        ))
        print("".join(diff) if diff else "[DRY-RUN] Sem diferenca.")
        return 0

    bak = path.with_suffix(f".py.bak_{_sha(text)}")
    bak.write_bytes(raw)
    print(f"[BACKUP] {bak}")

    out_bytes = patched.encode("utf-8")
    if crlf:
        out_bytes = out_bytes.replace(b"\n", b"\r\n")
    path.write_bytes(out_bytes)
    print(f"[PATCHED] {path}")
    print("\nSequencia esperada: ticks chegando via Redis\n")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("--file", default=str(TARGET))
    a = p.parse_args()
    sys.exit(apply_patch(Path(a.file), a.dry_run, a.check))


if __name__ == "__main__":
    main()
