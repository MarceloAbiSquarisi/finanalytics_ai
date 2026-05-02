"""Bulk-migrate `const r = await fetch(...); if (!r.ok) throw...; const X = await r.json();`
to `const X = await FAErr.fetchJson(...);` across static HTML.

Sprint UI Q (21/abr/2026). Conservative — only migrates the simplest pattern,
preserving fetch calls with custom 401/409/422/503 handling.
"""

from pathlib import Path
import re

STATIC = Path("D:/Projetos/finanalytics_ai_fresh/src/finanalytics_ai/interfaces/api/static")
SKIP = {"sidebar.html", "login.html", "reset_password.html"}

# Pattern: const r = await fetch(URL[, OPTS]); if (!r.ok) throw new Error('HTTP ' + r.status); const X = await r.json();
# Multi-line, naive. Captures URL[+OPTS] as one group.

# Two variants:
# A) const X = await r.json(); after the throw
# B) renderXxx(await r.json()); after the throw
PATTERN_A = re.compile(
    r"const\s+r\s*=\s*await\s+fetch\(([^;]+?)\);\s*"
    r"if\s*\(\s*!r\.ok\s*\)\s*throw\s+new\s+Error\(\s*'HTTP\s*'\s*\+\s*r\.status\s*\)\s*;\s*"
    r"const\s+(\w+)\s*=\s*await\s+r\.json\(\)\s*;",
    re.DOTALL,
)


def main():
    total = 0
    for f in sorted(STATIC.glob("*.html")):
        if f.name in SKIP:
            continue
        txt = f.read_text(encoding="utf-8")
        n = 0

        def repl(m):
            nonlocal n
            n += 1
            args = m.group(1).strip()
            var = m.group(2)
            return f"const {var} = await FAErr.fetchJson({args});"

        new = PATTERN_A.sub(repl, txt)
        if new != txt:
            f.write_text(new, encoding="utf-8")
            total += n
            print(f"{f.name}: {n}")
    print(f"\nTotal migrated: {total}")


if __name__ == "__main__":
    main()
