"""
fix_pnl_injection.py
Remove a injeção repetida do snippet <a href="/pnl" class="fa-sb-sub-link">
de todos os arquivos HTML estáticos do projeto.

O snippet correto DEVE existir dentro da tag <aside class="fa-sidebar"> ou
<div class="fa-sb-group"> na navegação lateral. Em qualquer outro lugar é bug.

Uso:
    python fix_pnl_injection.py --dry-run   # mostra o que seria removido
    python fix_pnl_injection.py             # aplica o fix em todos os HTMLs
"""
from __future__ import annotations
import argparse, hashlib, re, sys
from pathlib import Path

STATIC_DIR = Path(
    r"D:\Projetos\finanalytics_ai_fresh"
    r"\src\finanalytics_ai\interfaces\api\static"
)

# Padrão exato do snippet injetado (P&L Intraday link com ícone wave)
# Aparece em duas variantes (com ou sem espaço/newline antes)
PNL_PATTERN = re.compile(
    r'\s*<a\s+href="/pnl"\s+class="fa-sb-sub-link">'
    r'<svg[^>]*>.*?</svg>'
    r'<span>P(?:&amp;|&)L\s*Intraday</span>'
    r'</a>',
    re.DOTALL
)

# Padrão mais amplo para outros fa-sb-sub-link injetados fora do sidebar
# (Setups, Diário de Trade, etc que aparecem fora do <aside>)
OTHER_SUBLINK_PATTERN = re.compile(
    r'\s*<a\s+href="[^"]*"\s+class="fa-sb-sub-link"[^>]*>'
    r'<svg[^>]*>.*?</svg>'
    r'<span>[^<]+</span>'
    r'</a>',
    re.DOTALL
)

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:10]

def count_outside_sidebar(text: str, pattern: re.Pattern) -> int:
    """Conta ocorrências fora da tag <aside class='fa-sidebar'>"""
    # Extrai o bloco do sidebar
    sidebar_match = re.search(r'<aside class="fa-sidebar".*?</aside>', text, re.DOTALL)
    sidebar_text = sidebar_match.group(0) if sidebar_match else ""
    outside = text.replace(sidebar_text, "", 1)
    return len(pattern.findall(outside))

def fix_file(path: Path, dry_run: bool = False) -> dict:
    raw = path.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8").replace("\r\n", "\n")

    # Conta ocorrências fora do sidebar
    pnl_outside = count_outside_sidebar(text, PNL_PATTERN)
    other_outside = count_outside_sidebar(text, OTHER_SUBLINK_PATTERN)

    if pnl_outside == 0 and other_outside == 0:
        return {"file": path.name, "status": "clean", "removed": 0}

    # Estratégia: preserva o bloco <aside class="fa-sidebar">
    # e remove todos os snippets de fora
    sidebar_match = re.search(r'<aside class="fa-sidebar".*?</aside>', text, re.DOTALL)
    sidebar_block = sidebar_match.group(0) if sidebar_match else ""
    PLACEHOLDER = "___SIDEBAR_PLACEHOLDER___"

    # Substitui sidebar por placeholder temporário
    working = text.replace(sidebar_block, PLACEHOLDER, 1) if sidebar_block else text

    # Remove snippets injetados fora do sidebar
    before_count = len(PNL_PATTERN.findall(working)) + len(OTHER_SUBLINK_PATTERN.findall(working))
    working = PNL_PATTERN.sub("", working)
    working = OTHER_SUBLINK_PATTERN.sub("", working)
    after_count = len(PNL_PATTERN.findall(working)) + len(OTHER_SUBLINK_PATTERN.findall(working))
    removed = before_count - after_count

    # Restaura sidebar
    if sidebar_block:
        working = working.replace(PLACEHOLDER, sidebar_block, 1)

    result = {
        "file": path.name,
        "status": "fixed" if not dry_run else "would_fix",
        "removed": removed,
        "pnl_outside": pnl_outside
    }

    if not dry_run and removed > 0:
        bak = path.with_suffix(f".html.bak_{_sha(text)}")
        bak.write_bytes(raw)
        out = working.encode("utf-8")
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
        print(f"[ERROR] Diretório não encontrado: {static}", file=sys.stderr)
        sys.exit(2)

    html_files = [f for f in static.rglob("*.html") if ".bak" not in f.name]
    print(f"Analisando {len(html_files)} arquivos HTML em {static}\n")

    total_removed = 0
    for f in sorted(html_files):
        r = fix_file(f, dry_run=a.dry_run)
        if r["removed"] > 0:
            status = "DRY-RUN" if a.dry_run else "FIXED"
            print(f"[{status}] {r['file']:40s} — {r['removed']:3d} snippets removidos")
            total_removed += r["removed"]
        else:
            print(f"[OK]     {r['file']:40s} — limpo")

    print(f"\nTotal removido: {total_removed} snippets de {len(html_files)} arquivos")
    if a.dry_run:
        print("(dry-run — nenhum arquivo modificado)")

if __name__ == "__main__":
    main()
