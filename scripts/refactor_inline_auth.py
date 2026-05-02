"""G4 batch refactor — substitui IIFE inline `authGuard()` por FAAuth.requireAuth.

Pattern detectado em 13 páginas (26/abr/2026). Validado piloto em /watchlist
(commit 168f977). Idempotente — pula arquivos já migrados.

Skip dashboard.html (lógica auth própria, migração manual cuidadosa).
"""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / "src/finanalytics_ai/interfaces/api/static"

# Pattern do IIFE minificado (mais comum)
_IIFE_MIN = (
    '(function authGuard(){var t=localStorage.getItem("access_token"),'
    'e=parseInt(localStorage.getItem("token_expires_at")||"0");'
    'if(!t||e<Date.now()+60000)window.location.replace("/login");})();'
)

# Pattern do replacement (matched validation in /watchlist/import.html)
_NEW_BLOCK = (
    '<script src="/static/auth_guard.js"></script>\n'
    "<script>\n"
    "// G4 refactor 26/abr — usa FAAuth.requireAuth (silent refresh + lembre-me 7d).\n"
    "(function(){function go(){if(window.FAAuth)FAAuth.requireAuth({});"
    "else setTimeout(go,30);}"
    'if(document.readyState!=="loading")go();'
    'else document.addEventListener("DOMContentLoaded",go);})();'
)

# Pages excluídas (manuais ou já migradas):
SKIP = {"dashboard.html"}  # lógica própria

# Multi-line variants — diferentes encantamentos do mesmo IIFE
# Capturamos via regex multi-line.
_MULTILINE_RE = re.compile(
    r"\(function authGuard\(\) ?\{[^}]+\}\)\(\);",
    re.DOTALL,
)


def migrate_file(path: Path) -> tuple[bool, str]:
    """Retorna (mudou, msg)."""
    content = path.read_text(encoding="utf-8")
    if 'auth_guard.js"></script>' in content and "FAAuth.requireAuth" in content:
        return False, "já migrado"

    # 1. Tenta substituição literal do single-line
    if _IIFE_MIN in content:
        # Encontra o `<script>` que vem ANTES do IIFE pra inserir o src antes
        idx = content.index(_IIFE_MIN)
        # Procura `<script>` mais próximo antes
        before = content.rfind("<script>", 0, idx)
        if before == -1:
            return False, "sem <script> antes do IIFE — pula manual"
        # Substitui:  <script>\nIIFE  →  NEW_BLOCK\nIIFE
        # Remove o IIFE e o `<script>` que o abria
        old = content[before : idx + len(_IIFE_MIN)]
        new = _NEW_BLOCK
        content_new = content.replace(old, new, 1)
        if content_new == content:
            return False, "replace falhou"
        path.write_text(content_new, encoding="utf-8")
        return True, "single-line OK"

    # 2. Tenta multi-line via regex
    matches = list(_MULTILINE_RE.finditer(content))
    if matches:
        m = matches[0]
        # Mesma lógica: encontra <script> antes da posição
        idx = m.start()
        before = content.rfind("<script>", 0, idx)
        if before == -1:
            return False, "multi-line sem <script> antes — pula manual"
        old = content[before : m.end()]
        content_new = content.replace(old, _NEW_BLOCK, 1)
        if content_new == content:
            return False, "multi-line replace falhou"
        path.write_text(content_new, encoding="utf-8")
        return True, "multi-line OK"

    return False, "sem IIFE detectado"


def main() -> None:
    targets = [
        "anomaly.html",
        "backtest.html",
        "correlation.html",
        "diario.html",
        "etf.html",
        "fixed_income.html",
        "forecast.html",
        "laminas.html",
        "macro.html",
        "patrimony.html",
        "performance.html",
        "screener.html",
    ]
    print(f"Migrando {len(targets)} páginas (skip: {SKIP})\n")
    ok, skipped, failed = 0, 0, 0
    for name in targets:
        if name in SKIP:
            print(f"  SKIP {name}")
            skipped += 1
            continue
        path = ROOT / name
        if not path.exists():
            print(f"  ?    {name} — não existe")
            failed += 1
            continue
        changed, msg = migrate_file(path)
        if changed:
            print(f"  OK   {name} ({msg})")
            ok += 1
        else:
            print(f"  ---  {name} ({msg})")
            skipped += 1
    print(f"\nTotal: {ok} migradas, {skipped} skip/idempot, {failed} falhas")


if __name__ == "__main__":
    main()
