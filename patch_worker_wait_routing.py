"""patch_worker_wait_routing.py
Corrige timing do SubscribeTicker: espera conn_type=1 result>=4
antes de subscrever — evita ret=-2147483647 (DLL not ready).
"""
from __future__ import annotations
import argparse, hashlib, shutil, sys
from pathlib import Path

TARGET = Path(
    r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai"
    r"\workers\profit_market_worker.py"
)

ANCHOR = """    # Nao espera market_connected=True antes de subscrever.
    # Motivo: conn_type=2 result=4 (MARKET_CONNECTED) so dispara APOS
    # SubscribeTicker — esperar antes de subscrever e um deadlock.
    # market_login_valid=True ja confirma credenciais validas (conn_type=3).
    log.info("profit_market_worker.skipping_market_wait",
             reason="subscribe_early_fix: market_connected fires after subscribe")
"""

REPLACEMENT = """    # Espera roteamento (conn_type=1 result>=4) antes de subscrever.
    # SubscribeTicker retorna -2147483647 se chamado antes do routing conectar.
    # conn_type=1 result=4 = ROUTING_CONNECTED (manual Nelogica).
    # Timeout curto (10s) — routing tipicamente conecta em <2s apos login.
    _routing_timeout = int(os.getenv("PROFIT_ROUTING_TIMEOUT", "20"))
    for _ri in range(_routing_timeout * 2):
        if getattr(profit_client.state, "routing_connected", False):
            log.info("profit_market_worker.routing_connected", attempts=_ri)
            break
        await asyncio.sleep(0.5)
    else:
        log.warning("profit_market_worker.routing_timeout_proceeding")
"""

SENTINEL = "# Espera roteamento (conn_type=1 result>=4) antes de subscrever."


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
    print(f"[PATCHED] {path}")
    print("\nAgora precisamos marcar routing_connected=True no state_cb.")
    print("Execute tambem: patch_dll_routing_state.py\n")
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
