"""
fix_pnl_safe.py — Remove links fa-sb-sub-link injetados fora da sidebar.
SEGURO: não usa re.DOTALL, opera linha a linha, nunca toca em <script> blocks.
"""
from __future__ import annotations
import argparse, hashlib, re, sys
from pathlib import Path

STATIC_DIR = Path(
    r"D:\Projetos\finanalytics_ai_fresh"
    r"\src\finanalytics_ai\interfaces\api\static"
)

# Padrão SINGLE-LINE: links injetados em uma única linha
# Captura <a href="..." class="fa-sb-sub-link">...<span>...</span></a>
# SEM re.DOTALL — não cruza linhas, nunca toca em script blocks
SUBLINK_SINGLE = re.compile(
    r'<a\s+href="[^"]*"\s+class="fa-sb-sub-link"[^>]*>'
    r'<svg[^>]*>[^<]*(?:<[^>]+>[^<]*)*</svg>'
    r'<span>[^<]+</span>'
    r'</a>'
)

def _sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()[:10]

def fix_file(path: Path, dry_run: bool = False) -> dict:
    raw = path.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8").replace("\r\n", "\n")
    lines = text.split("\n")

    # Identifica o bloco <aside class="fa-sidebar"> (linhas a preservar)
    sidebar_start = sidebar_end = -1
    for i, line in enumerate(lines):
        if '<aside class="fa-sidebar"' in line and sidebar_start == -1:
            sidebar_start = i
        if sidebar_start >= 0 and '</aside>' in line and sidebar_end == -1:
            sidebar_end = i
            break

    # Processa linha a linha (nunca toca no sidebar)
    removed = 0
    new_lines = []
    for i, line in enumerate(lines):
        # Preserva o bloco sidebar intato
        if sidebar_start <= i <= sidebar_end:
            new_lines.append(line)
            continue

        # Remove sublinks nesta linha
        new_line, n = SUBLINK_SINGLE.subn("", line)
        if n > 0:
            removed += n
            # Remove linha se ficou vazia/só espaços
            if new_line.strip():
                new_lines.append(new_line)
            # else: linha removida completamente
        else:
            new_lines.append(line)

    result = {
        "file": path.name,
        "removed": removed,
        "sidebar": f"lines {sidebar_start}-{sidebar_end}"
    }

    if dry_run or removed == 0:
        return result

    patched = "\n".join(new_lines)
    bak = path.with_suffix(f".html.bak_{_sha(text)}")
    bak.write_bytes(raw)
    out = patched.encode("utf-8")
    if crlf:
        out = out.replace(b"\n", b"\r\n")
    path.write_bytes(out)
    result["backup"] = bak.name
    return result


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--dir", default=str(STATIC_DIR))
    a = p.parse_args()

    static = Path(a.dir)
    if not static.exists():
        print(f"[ERROR] {static}", file=sys.stderr); sys.exit(2)

    html_files = [f for f in static.rglob("*.html") if ".bak" not in f.name]
    print(f"{'[DRY-RUN] ' if a.dry_run else ''}Processando {len(html_files)} HTMLs\n")

    total = 0
    for f in sorted(html_files):
        r = fix_file(f, dry_run=a.dry_run)
        if r["removed"]:
            tag = "DRY" if a.dry_run else "FIXED"
            print(f"[{tag}] {r['file']:45s} — {r['removed']:3d} removidos | sidebar {r['sidebar']}")
            total += r["removed"]
        else:
            print(f"[OK]   {r['file']:45s} — limpo")

    print(f"\nTotal: {total} snippets removidos de {len(html_files)} arquivos")

if __name__ == "__main__":
    main()
