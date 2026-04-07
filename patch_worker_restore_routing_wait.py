from __future__ import annotations
import argparse, hashlib, shutil, sys
from pathlib import Path

TARGET = Path(
    r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai"
    r"\workers\profit_market_worker.py"
)

ANCHOR = '    # HasRoteamento=False: sem routing. Espera diretamente market_connected.\n    log.info("profit_market_worker.no_routing_account")\n\n    # Registra SetTradeCallbackV2 UMA UNICA VEZ apos routing — igual ao diagnostico.\n    # Dupla chamada (antes do init + apos routing) corrompia estado interno da DLL.\n'
REPLACEMENT = '    # Espera routing + broker (crBrokerConnected) antes de registrar callback.\n    # Delphi mostra: cstMarketData so fica cmdConnectedLogged APOS crBrokerConnected.\n    for _ri in range(60):  # max 30s\n        if getattr(profit_client.state, \'routing_connected\', False):\n            log.info("profit_market_worker.routing_connected", attempts=_ri)\n            break\n        await asyncio.sleep(0.5)\n    else:\n        log.warning("profit_market_worker.routing_timeout_proceeding")\n\n    # Registra SetTradeCallbackV2 UMA UNICA VEZ apos routing — igual ao diagnostico.\n    # Dupla chamada (antes do init + apos routing) corrompia estado interno da DLL.\n'
SENTINEL = '# Espera routing + broker (crBrokerConnected) antes de registrar callback.'

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
