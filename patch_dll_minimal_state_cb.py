from __future__ import annotations
import argparse, hashlib, shutil, sys
from pathlib import Path

TARGET = Path(
    r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai"
    r"\infrastructure\market_data\profit_dll\client.py"
)

ANCHOR = '        import structlog as _structlog_cb  # importado fora do callback — evita deadlock no GIL\n        _cb_log_ref = _structlog_cb.get_logger("profit_dll.state")\n\n        @_WFTYPE(None, _cint, _cint)\n        def _state_cb(t, r):\n            _cb_log = _cb_log_ref\n            if t == 0:\n                _state.login_connected = (r == 0)\n                _cb_log.info("dll_state.login", conn_type=t, result=r,\n                             connected=_state.login_connected)\n            elif t == 2:\n                _state.market_connected = (r == 4)\n                state_name = _MARKET_STATES.get(r, f"UNKNOWN_{r}")\n                _cb_log.info("dll_state.market_data", conn_type=t, result=r,\n                             state=state_name, connected=_state.market_connected)\n            elif t == 3:\n                _state.market_login_valid = (r == 0)\n                _cb_log.info("dll_state.market_login", conn_type=t, result=r,\n                             valid=_state.market_login_valid)\n            elif t == 1:\n                # conn_type=1: roteamento. result=4 ou 5 = conectado/autenticado\n                _state.routing_connected = (r >= 4)\n                _cb_log.info("dll_state.other", conn_type=t, result=r,\n                             routing_connected=_state.routing_connected)\n            else:\n                _cb_log.info("dll_state.other", conn_type=t, result=r)\n            if _state.ready and _loop:\n                _loop.call_soon_threadsafe(_event.set)\n'
REPLACEMENT = '        # state_cb minimo: apenas atualizacoes de estado, SEM logging nem I/O.\n        # I/O na ConnectorThread bloqueia entrega de conn_type=2 (market data).\n        @_WFTYPE(None, _cint, _cint)\n        def _state_cb(t, r):\n            if t == 0:\n                _state.login_connected = (r == 0)\n            elif t == 1:\n                _state.routing_connected = (r >= 4)\n            elif t == 2:\n                _state.market_connected = (r == 4)\n            elif t == 3:\n                _state.market_login_valid = (r == 0)\n            if _state.ready and _loop:\n                _loop.call_soon_threadsafe(_event.set)\n'
SENTINEL = '# state_cb minimo: apenas atualizacoes de estado, SEM logging nem I/O.'

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
