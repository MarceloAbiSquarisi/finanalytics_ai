"""
Patch D1 + D3 no dashboard.html

D3: volSeries.setData usa bars (original, pode ser string)
    em vez de _bars2 (convertido). Corrige para usar _bars2.

D1: Remove implementacoes duplicadas de drawDaySeparators (linhas 1528-1578)
    Mantem apenas a versao com parametros (linha 1579+) que e chamada em 1748.
    Remove tambem a chamada initDaySeparators (1718) que usa versao antiga.
"""
from __future__ import annotations
import argparse, hashlib, sys
from pathlib import Path

TARGET = Path(
    r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai"
    r"\interfaces\api\static\dashboard.html"
)

# ── D3: volume usa bars em vez de _bars2 ──────────────────────────────────────
D3_OLD = (
    "  volSeries.setData(bars.map(b => ({\n"
    "    time:b.time, value:b.volume||0,\n"
    "    color: b.close>=b.open ? 'rgba(46,212,122,.22)' : 'rgba(224,85,85,.22)',\n"
    "  })));"
)
D3_NEW = (
    "  volSeries.setData(_bars2.map(b => ({\n"
    "    time:b.time, value:b.volume||0,\n"
    "    color: b.close>=b.open ? 'rgba(46,212,122,.22)' : 'rgba(224,85,85,.22)',\n"
    "  })));"
)

# ── D1: remove initDaySeparators call (usa implementacao duplicada antiga) ────
D1_OLD_INIT = (
    "  // Separadores de dia apos render\n"
    "  setTimeout(function(){if(typeof initDaySeparators==='function'"
    "&&typeof bars!=='undefined')initDaySeparators(bars);},250);"
)
D1_NEW_INIT = (
    "  // Separadores de dia: chamados via drawDaySeparators(chart,bars,container) em seguida"
)

SENTINEL_D3 = "_bars2.map(b => ({\n    time:b.time, value:b.volume||0,"
SENTINEL_D1 = "// Separadores de dia: chamados via drawDaySeparators(chart,bars,container) em seguida"


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()[:12]


def apply_patch(path: Path, dry_run: bool = False) -> int:
    if not path.exists():
        print(f"[ERROR] {path}", file=sys.stderr); return 2
    raw = path.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8").replace("\r\n", "\n")

    done = []
    if SENTINEL_D3 in text:
        done.append("D3 já aplicado")
    if SENTINEL_D1 in text:
        done.append("D1 já aplicado")
    if done:
        print("[OK]", ", ".join(done)); return 0

    patched = text
    if SENTINEL_D3 not in patched:
        if D3_OLD not in patched:
            print("[WARN] D3 âncora não encontrada — pulando D3", file=sys.stderr)
        else:
            patched = patched.replace(D3_OLD, D3_NEW, 1)
            print("[D3] volume timestamps corrigidos")

    if SENTINEL_D1 not in patched:
        if D1_OLD_INIT not in patched:
            print("[WARN] D1 âncora não encontrada — pulando D1", file=sys.stderr)
        else:
            patched = patched.replace(D1_OLD_INIT, D1_NEW_INIT, 1)
            print("[D1] initDaySeparators duplicado removido")

    if dry_run:
        import difflib
        diff = list(difflib.unified_diff(
            text.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile="dashboard.html (original)",
            tofile="dashboard.html (patched)", n=3))
        print("".join(diff) if diff else "[DRY-RUN] Sem diferença")
        return 0

    bak = path.with_suffix(f".html.bak_{_sha(text)}")
    bak.write_bytes(raw)
    print(f"[BACKUP] {bak.name}")
    out = patched.encode("utf-8")
    if crlf:
        out = out.replace(b"\n", b"\r\n")
    path.write_bytes(out)
    print(f"[PATCHED] {path.name}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--file", default=str(TARGET))
    a = p.parse_args()
    sys.exit(apply_patch(Path(a.file), a.dry_run))


if __name__ == "__main__":
    main()
