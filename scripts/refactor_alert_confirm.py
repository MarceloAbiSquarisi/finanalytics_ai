"""Bulk-replace alert()/confirm() with FAToast/FAModal across static HTML pages.

Sprint UI B (21/abr/2026). Run once.
"""

from pathlib import Path
import re

STATIC = Path("D:/Projetos/finanalytics_ai_fresh/src/finanalytics_ai/interfaces/api/static")
SKIP = {"sidebar.html"}

CONFIRM_RE = re.compile(r"if\s*\(\s*!\s*confirm\(", re.IGNORECASE)


def find_balanced_close(s, start):
    """Given s[start] == '(', return index of matching ')'."""
    depth = 0
    in_str = None
    i = start
    while i < len(s):
        c = s[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
        else:
            if c in ('"', "'", "`"):
                in_str = c
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


def alert_to_toast(arg_text):
    low = arg_text.lower().lstrip()
    # Strip leading quote
    if low and low[0] in ("'", '"', "`"):
        low = low[1:]
    if low.startswith("erro") or "erro: " in low or "erro ao" in low or "falha" in low:
        return "FAToast.err"
    if any(
        low.startswith(w)
        for w in (
            "selecione",
            "informe",
            "preencha",
            "voce precisa",
            "você precisa",
            "falta",
            "obrigato",
            "soma dos pesos",
        )
    ):
        return "FAToast.warn"
    if "removidos" in low or "sucesso" in low or "salvo" in low:
        return "FAToast.ok"
    return "FAToast.info"


def make_funcs_async(txt):
    n = 0
    # Function declarations
    func_re = re.compile(r"\b(async\s+)?function\s+\w+\s*\(")
    chunks = []
    last = 0
    for fm in func_re.finditer(txt):
        if fm.group(1):
            continue
        paren_open = txt.find("(", fm.end() - 1)
        if paren_open < 0:
            continue
        paren_close = find_balanced_close(txt, paren_open)
        if paren_close < 0:
            continue
        brace_open = txt.find("{", paren_close)
        if brace_open < 0:
            continue
        depth = 0
        i = brace_open
        while i < len(txt):
            c = txt[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = txt[brace_open:i]
        if "await FAModal" in body:
            chunks.append(txt[last : fm.start()])
            chunks.append("async " + fm.group())
            last = fm.end()
            n += 1
    chunks.append(txt[last:])
    txt = "".join(chunks)

    # Arrow functions assigned with `name = (params) => {`
    arrow_re = re.compile(r"(\b[\w$]+\s*=\s*)(\([^()\n]*\)|[\w$]+)\s*=>\s*\{")
    chunks = []
    last = 0
    for am in arrow_re.finditer(txt):
        prefix_start = am.start()
        before = txt[max(0, prefix_start - 8) : prefix_start]
        if "async " in before:
            continue
        brace_open = am.end() - 1
        depth = 0
        i = brace_open
        while i < len(txt):
            c = txt[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = txt[brace_open:i]
        if "await FAModal" in body:
            inject_at = am.start(2)
            chunks.append(txt[last:inject_at])
            chunks.append("async " + txt[inject_at : am.end()])
            last = am.end()
            n += 1
    chunks.append(txt[last:])
    return "".join(chunks), n


def main():
    stats = {"files_changed": 0, "confirm_replaced": 0, "alert_replaced": 0, "funcs_made_async": 0}

    for f in sorted(STATIC.glob("*.html")):
        if f.name in SKIP:
            continue
        txt = f.read_text(encoding="utf-8")
        orig = txt
        n_confirm = 0
        n_alert = 0

        # PASS 1: confirm() -> FAModal.confirm()
        out = []
        cursor = 0
        for m in list(CONFIRM_RE.finditer(txt)):
            paren_start = m.end() - 1
            close = find_balanced_close(txt, paren_start)
            if close < 0:
                continue
            rest = txt[close + 1 :]
            rm = re.match(r"\s*\)\s*return\s*;", rest)
            if not rm:
                continue
            full_end = close + 1 + rm.end()
            arg = txt[paren_start + 1 : close]
            replacement = "if (!await FAModal.confirm(" + arg + ")) return;"
            out.append(txt[cursor : m.start()])
            out.append(replacement)
            cursor = full_end
            n_confirm += 1
        out.append(txt[cursor:])
        txt = "".join(out)

        # PASS 2: alert() -> FAToast.X
        out = []
        cursor = 0
        for m in re.finditer(r"\balert\(", txt):
            if m.start() > 0 and txt[m.start() - 1] in (".", "_"):
                continue
            paren_start = m.end() - 1
            close = find_balanced_close(txt, paren_start)
            if close < 0:
                continue
            arg = txt[paren_start + 1 : close].strip()
            if not arg:
                continue
            toast_fn = alert_to_toast(arg)
            replacement = toast_fn + "(" + arg + ")"
            out.append(txt[cursor : m.start()])
            out.append(replacement)
            cursor = close + 1
            n_alert += 1
        out.append(txt[cursor:])
        txt = "".join(out)

        # PASS 3: ensure enclosing functions are async
        txt, n_async = make_funcs_async(txt)

        if txt != orig:
            f.write_text(txt, encoding="utf-8")
            stats["files_changed"] += 1
            stats["confirm_replaced"] += n_confirm
            stats["alert_replaced"] += n_alert
            stats["funcs_made_async"] += n_async
            print(f"{f.name}: confirms={n_confirm}, alerts={n_alert}, async+={n_async}")

    print()
    print(stats)


if __name__ == "__main__":
    main()
