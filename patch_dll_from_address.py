from __future__ import annotations
import argparse, hashlib, shutil, sys
from pathlib import Path

TARGET = Path(
    r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai"
    r"\infrastructure\market_data\profit_dll\client.py"
)

ANCHOR = '            try:\n                from finanalytics_ai.infrastructure.market_data.profit_dll.types import (\n                    TConnectorAssetIdentifier as _AI,\n                )\n                ai       = _cast2(asset_id_raw, _PTR2(_AI)).contents\n                ticker   = ai.Ticker   or ""\n                exchange = ai.Exchange or "B"\n            except Exception as _decode_err:\n                import structlog as _sl; _sl.get_logger("profit_dll").error("asset_decode_error", error=str(_decode_err))\n                ticker, exchange = "", "B"\n'
REPLACEMENT = '            try:\n                from finanalytics_ai.infrastructure.market_data.profit_dll.types import (\n                    TConnectorAssetIdentifier as _AI,\n                )\n                # from_address() aceita int Python diretamente (cast nao aceita).\n                _ai = _AI.from_address(asset_id_raw)\n                ticker   = _ai.Ticker   or ""\n                exchange = _ai.Exchange or "B"\n            except Exception:\n                ticker, exchange = "", "B"\n'
SENTINEL = '# from_address() aceita int Python diretamente (cast nao aceita).'

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
