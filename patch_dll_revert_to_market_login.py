from __future__ import annotations
import argparse, hashlib, shutil, sys
from pathlib import Path

TARGET = Path(
    r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai"
    r"\infrastructure\market_data\profit_dll\client.py"
)

ANCHOR = '        # Inicializa via DLLInitializeLogin (login completo).\n        # DLLInitializeMarketLogin exige assinatura API standalone.\n        # DLLInitializeLogin ativa conn_type=1 (routing) que libera\n        # conn_type=2 (market data) — igual ao exemplo oficial Delphi.\n        from ctypes import c_wchar_p as _wstr\n        ret = self._dll.DLLInitializeLogin(\n            _wstr(self._activation_key),\n            _wstr(self._username),\n            _wstr(self._password),\n            _state_cb,  # StateCallback\n            None,       # HistoryCallback\n            None,       # OrderChangeCallback\n            None,       # AccountCallback\n            None,       # TradeCallback (via SetTradeCallbackV2 acima)\n            None,       # DailyCallback\n            None,       # PriceBookCallback\n            None,       # OfferBookCallback\n            None,       # HistoryTradeCallback\n            None,       # ProgressCallback\n            None,       # TinyBookCallback\n        )\n        if ret != 0:\n            raise RuntimeError(f"DLLInitializeLogin falhou: {ret}")\n        log.info("profit_dll.initialized", mode="full_login")\n'
REPLACEMENT = '        # DLLInitializeMarketLogin — conta HasRoteamento=False (market data only).\n        # DLLInitializeLogin causava crash (Access Violation) no routing thread.\n        # conn_type=2 result=4 (MARKET_CONNECTED) dispara sem routing.\n        from ctypes import c_wchar_p as _wstr\n        ret = self._dll.DLLInitializeMarketLogin(\n            _wstr(self._activation_key),\n            _wstr(self._username),\n            _wstr(self._password),\n            _state_cb,  # StateCallback\n            None,       # NewTradeCallback (via SetTradeCallbackV2)\n            None,       # NewDailyCallback\n            None,       # PriceBookCallback\n            None,       # OfferBookCallback\n            None,       # HistoryTradeCallback\n            None,       # ProgressCallback\n            None,       # TinyBookCallback\n        )\n        if ret != 0:\n            raise RuntimeError(f"DLLInitializeMarketLogin falhou: {ret}")\n        log.info("profit_dll.initialized", mode="market_login")\n'
SENTINEL = '# DLLInitializeMarketLogin — conta HasRoteamento=False (market data only).'

def _sha(t):
    return hashlib.sha256(t.encode()).hexdigest()[:12]

def apply_patch(path, dry_run=False, check=False):
    if not path.exists():
        print(f"[ERROR] {path}", file=sys.stderr); return 2
    raw = path.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8").replace("\r\n", "\n")
    if SENTINEL in text:
        print("[OK] Patch ja aplicado."); return 0
    if ANCHOR not in text:
        print("[ERROR] Ancora nao encontrada.", file=sys.stderr); return 3
    if check:
        print("[FAIL]", file=sys.stderr); return 1
    patched = text.replace(ANCHOR, REPLACEMENT, 1)
    if dry_run:
        import difflib
        diff = list(difflib.unified_diff(
            text.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=path.name+" (original)",
            tofile=path.name+" (patched)", n=3))
        print("".join(diff) if diff else "[DRY-RUN] Sem diferenca."); return 0
    bak = path.with_suffix(f".py.bak_{_sha(text)}")
    bak.write_bytes(raw)
    print(f"[BACKUP] {bak}")
    out = patched.encode("utf-8")
    if crlf:
        out = out.replace(b"\n", b"\r\n")
    path.write_bytes(out)
    print(f"[PATCHED] {path}")
    return 0

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("--file", default=str(TARGET))
    a = p.parse_args()
    sys.exit(apply_patch(Path(a.file), a.dry_run, a.check))

if __name__ == "__main__":
    main()
