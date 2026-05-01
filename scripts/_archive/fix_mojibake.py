"""Corrige mojibake (UTF-8 mal-decodificado como Latin-1) em arquivos HTML/JS estáticos.

Causa: arquivo salvo como UTF-8 mas em algum momento foi lido como Latin-1 e re-salvo,
gerando sequências como `Ã£` (que era ã), `Ã§` (era ç), `â€"` (era —) etc.

Estratégia:
    Substituição de sequências conhecidas. Não decodifica/recodifica o arquivo inteiro
    (risco de quebrar partes válidas). Aplica replace literal apenas nas sequências mojibake.

Uso:
    python scripts/fix_mojibake.py --dry-run             # mostra arquivos com mojibake
    python scripts/fix_mojibake.py --apply               # corrige todos
    python scripts/fix_mojibake.py --apply --file PATH   # corrige um único arquivo
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

STATIC_DIR = Path(__file__).parent.parent / "src" / "finanalytics_ai" / "interfaces" / "api" / "static"

# Sequências mojibake → caracter correto. Ordem importa: começar pelas mais longas/raras.
MOJIBAKE = {
    # Em-dash e en-dash
    "â€\"": "—",  # em-dash
    "â€“": "–",   # en-dash
    "â€œ": "“",  # left double quote
    "â€": "”",   # right double quote
    "â€™": "’",  # right single quote (apostrophe)
    "â€˜": "‘",  # left single quote
    "â€¢": "•",   # bullet
    "â€¦": "…",   # ellipsis
    # Acentos minúsculos
    "Ã¡": "á",
    "Ã ": "à",
    "Ã¢": "â",
    "Ã£": "ã",
    "Ã¤": "ä",
    "Ã©": "é",
    "Ã¨": "è",
    "Ãª": "ê",
    "Ã«": "ë",
    "Ã­": "í",
    "Ã¬": "ì",
    "Ã®": "î",
    "Ã¯": "ï",
    "Ã³": "ó",
    "Ã²": "ò",
    "Ã´": "ô",
    "Ãµ": "õ",
    "Ã¶": "ö",
    "Ãº": "ú",
    "Ã¹": "ù",
    "Ã»": "û",
    "Ã¼": "ü",
    "Ã§": "ç",
    "Ã±": "ñ",
    "Ã½": "ý",
    # Acentos maiúsculos
    "Ã\x81": "Á",
    "Ã€": "À",
    "Ã‚": "Â",
    "Ãƒ": "Ã",
    "Ã„": "Ä",
    "Ã‰": "É",
    "Ãˆ": "È",
    "ÃŠ": "Ê",
    "Ã‹": "Ë",
    "Ã\x8d": "Í",
    "ÃŒ": "Ì",
    "ÃŽ": "Î",
    "Ã\x8f": "Ï",
    "Ã\x93": "Ó",
    "Ã\x92": "Ò",
    "Ã\x94": "Ô",
    "Ã\x95": "Õ",
    "Ã–": "Ö",
    "Ã\x9a": "Ú",
    "Ã™": "Ù",
    "Ã›": "Û",
    "Ãœ": "Ü",
    "Ã‡": "Ç",
    # Outros
    "Â°": "°",
    "Â§": "§",
    "Â®": "®",
    "Â©": "©",
    "Â²": "²",
    "Â³": "³",
    "Â¹": "¹",
    "Â¼": "¼",
    "Â½": "½",
    "Â¾": "¾",
    "Âª": "ª",
    "Âº": "º",
    "Â«": "«",
    "Â»": "»",
    "Â¿": "¿",
    "Â¡": "¡",
}


def fix_text(text: str) -> tuple[str, int]:
    """Aplica substituições e retorna texto fixado + count de substituições totais."""
    total = 0
    for bad, good in MOJIBAKE.items():
        if bad in text:
            count = text.count(bad)
            text = text.replace(bad, good)
            total += count
    return text, total


def find_html_js_files(root: Path) -> list[Path]:
    return sorted(
        list(root.rglob("*.html")) + list(root.rglob("*.js"))
    )


def process_file(path: Path, apply: bool) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"file": str(path), "error": "not_utf8"}

    fixed, count = fix_text(text)
    if count == 0:
        return {"file": str(path), "fixed": 0, "skipped": True}

    if apply:
        path.write_text(fixed, encoding="utf-8")
    return {"file": str(path), "fixed": count, "skipped": False}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--file", help="Apenas este arquivo (path absoluto ou relativo a static/)")
    args = p.parse_args()

    if not (args.dry_run or args.apply):
        p.error("Use --dry-run ou --apply")

    if args.file:
        f = Path(args.file)
        if not f.is_absolute():
            f = STATIC_DIR / f
        files = [f] if f.exists() else []
    else:
        files = find_html_js_files(STATIC_DIR)

    print(f"\n[fix_mojibake] mode: {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"[fix_mojibake] files to scan: {len(files)}\n")

    total_fixed = 0
    files_with_issues = 0
    for f in files:
        result = process_file(f, apply=args.apply)
        if result.get("error"):
            print(f"  [ERR] {result['file']}: {result['error']}")
            continue
        if result.get("skipped"):
            continue
        files_with_issues += 1
        total_fixed += result["fixed"]
        prefix = "[FIXED]" if args.apply else "[FOUND]"
        print(f"  {prefix} {result['file'].replace(str(STATIC_DIR) + chr(92), '').replace(str(STATIC_DIR) + chr(47), '')}: {result['fixed']} substituições")

    print(f"\n[fix_mojibake] arquivos com mojibake: {files_with_issues}")
    print(f"[fix_mojibake] total de substituições: {total_fixed}")
    if not args.apply and total_fixed > 0:
        print("\nUse --apply para corrigir.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
