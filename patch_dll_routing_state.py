"""patch_dll_routing_state.py
Marca ConnectionState.routing_connected=True quando conn_type=1 result>=4.
Necessario para patch_worker_wait_routing.py funcionar.
"""
from __future__ import annotations
import argparse, hashlib, shutil, sys
from pathlib import Path

TARGET = Path(
    r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai"
    r"\infrastructure\market_data\profit_dll\client.py"
)

ANCHOR = """            else:
                _cb_log.info("dll_state.other", conn_type=t, result=r)
            if _state.ready and _loop:
                _loop.call_soon_threadsafe(_event.set)"""

REPLACEMENT = """            elif t == 1:
                # conn_type=1: roteamento. result=4 ou 5 = conectado/autenticado
                _state.routing_connected = (r >= 4)
                _cb_log.info("dll_state.other", conn_type=t, result=r,
                             routing_connected=_state.routing_connected)
            else:
                _cb_log.info("dll_state.other", conn_type=t, result=r)
            if _state.ready and _loop:
                _loop.call_soon_threadsafe(_event.set)"""

SENTINEL = "# conn_type=1: roteamento. result=4 ou 5 = conectado/autenticado"


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()[:12]


def apply_patch(path: Path, dry_run: bool = False, check: bool = False) -> int:
    if not path.exists():
        print(f"[ERROR] Nao encontrado: {path}", file=sys.stderr); return 2
    original = path.read_text(encoding="utf-8")
    if SENTINEL in original:
        print("[OK] Patch ja aplicado."); return 0
    if ANCHOR not in original:
        print("[ERROR] Ancora nao encontrada.", file=sys.stderr); return 3
    if check:
        print("[FAIL] Patch nao aplicado.", file=sys.stderr); return 1
    patched = original.replace(ANCHOR, REPLACEMENT, 1)
    if dry_run:
        import difflib
        diff = list(difflib.unified_diff(
            original.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=path.name + " (original)",
            tofile=path.name + " (patched)", n=3))
        print("".join(diff) if diff else "[DRY-RUN] Sem diferenca."); return 0
    bak = path.with_suffix(f".py.bak_{_sha(original)}")
    shutil.copy2(path, bak)
    print(f"[BACKUP] {bak}")
    path.write_text(patched, encoding="utf-8")
    print(f"[PATCHED] {path}\n")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("--file", default=str(TARGET))
    a = p.parse_args()
    sys.exit(apply_patch(Path(a.file), a.dry_run, a.check))


if __name__ == "__main__":
    main()
