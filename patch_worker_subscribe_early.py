"""patch_worker_subscribe_early.py
Corrige deadlock no profit_market_worker:
  - Worker esperava market_connected=True para subscrever tickers
  - market_connected so dispara APOS subscrever (conn_type=2 result=4)
  - Fix: subscreve assim que market_login_valid=True (credenciais validas)
"""
from __future__ import annotations
import argparse, hashlib, shutil, sys
from pathlib import Path

TARGET = Path(
    r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai"
    r"\workers\profit_market_worker.py"
)

ANCHOR = """    # Aguarda market_connected=True antes de subscrever (result=4 da DLL)
    # Timeout de 120s: DLLInitializeMarketLogin sem Profit Pro pode levar ate 60-90s
    _market_timeout = int(os.getenv("PROFIT_MARKET_TIMEOUT", "240"))
    for _i in range(_market_timeout):
        if profit_client.state.market_connected:
            log.info("profit_market_worker.market_connected", attempts=_i)
            break
        if _i % 20 == 0 and _i > 0:
            log.info("profit_market_worker.waiting_market", seconds=_i // 2)
        await asyncio.sleep(0.5)
    if not profit_client.state.market_connected:
        log.warning("profit_market_worker.market_not_connected_using_login_valid")
"""

REPLACEMENT = """    # Nao espera market_connected=True antes de subscrever.
    # Motivo: conn_type=2 result=4 (MARKET_CONNECTED) so dispara APOS
    # SubscribeTicker — esperar antes de subscrever e um deadlock.
    # market_login_valid=True ja confirma credenciais validas (conn_type=3).
    log.info("profit_market_worker.skipping_market_wait",
             reason="subscribe_early_fix: market_connected fires after subscribe")
"""

SENTINEL = "subscribe_early_fix: market_connected fires after subscribe"


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
    print("\nSequencia esperada nos logs:")
    print("  profit_market_worker.skipping_market_wait")
    print("  profit_worker.redis_publisher_ready")
    print("  profit_worker.tape_bridge_registered")
    print("  profit_dll.subscribed   ticker=WINFUT  (x8)")
    print("  profit_market_worker.subscribed")
    print("  [ticks chegando via Redis]\n")
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
