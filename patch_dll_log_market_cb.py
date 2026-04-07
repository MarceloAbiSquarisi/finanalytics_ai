from __future__ import annotations
import argparse, hashlib, shutil, sys
from pathlib import Path

TARGET = Path(
    r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai"
    r"\infrastructure\market_data\profit_dll\client.py"
)

ANCHOR = "        # state_cb com latch para market_connected.\n        # r>=4 = qualquer estado 'conectado/logado' (4=CONNECTED, 5=LOGGED, etc)\n        # r==0 = DISCONNECTED, reseta o latch.\n        @_WFTYPE(None, _cint, _cint)\n        def _state_cb(t, r):\n            if t == 0:   _state.login_connected    = (r == 0)\n            elif t == 1: _state.routing_connected  = (r >= 4)\n            elif t == 2:\n                if r >= 4:   _state.market_connected = True   # latch: qualquer estado conectado\n                elif r == 0: _state.market_connected = False  # so reseta no disconnect\n            elif t == 3: _state.market_login_valid = (r == 0)\n"
REPLACEMENT = "        # state_cb com latch para market_connected + file log para conn_type=2.\n        import builtins as _bi2\n        _log2 = r'C:\\\\Temp\\\\market_cb.log'\n        @_WFTYPE(None, _cint, _cint)\n        def _state_cb(t, r):\n            if t == 0:   _state.login_connected    = (r == 0)\n            elif t == 1: _state.routing_connected  = (r >= 4)\n            elif t == 2:\n                if r >= 4:   _state.market_connected = True\n                elif r == 0: _state.market_connected = False\n                try:\n                    with _bi2.open(_log2, 'a') as _f2:\n                        _f2.write(f'conn_type=2 r={r} market_connected={_state.market_connected}\\n')\n                except Exception: pass\n            elif t == 3: _state.market_login_valid = (r == 0)\n"
SENTINEL = '# state_cb com latch para market_connected + file log para conn_type=2.'

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
