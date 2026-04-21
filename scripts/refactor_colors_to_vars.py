"""Migrate hardcoded hex colors -> var(--xxx) in static HTML <style> blocks.

Sprint UI P (21/abr/2026). Conservative — only colors that match the canonical
theme.css :root vars exactly. Skips replacements inside :root{...} blocks
(those DEFINE the vars and must keep literal values).
"""
from pathlib import Path
import re

STATIC = Path('D:/Projetos/finanalytics_ai_fresh/src/finanalytics_ai/interfaces/api/static')
SKIP = {'sidebar.html', 'theme.css'}

# Canonical color -> var. Hex compared case-insensitively.
COLOR_MAP = {
    '#080b10': 'var(--bg)',
    '#0d1117': 'var(--s1)',
    '#111822': 'var(--s2)',
    '#0e1520': 'var(--s3)',
    '#1c2535': 'var(--border)',
    '#253045': 'var(--border2)',
    '#cdd6e0': 'var(--text)',
    '#4a5e72': 'var(--muted)',
    '#e2eaf4': 'var(--text-strong)',
    '#00d4ff': 'var(--accent)',
    '#00e676': 'var(--green)',
    '#ff3d5a': 'var(--red)',
    '#f0b429': 'var(--gold)',
    '#fbbf24': 'var(--yellow)',
    '#fb923c': 'var(--orange)',
    '#a855f7': 'var(--purple)',
}

# Build regex of all hex keys (case-insensitive)
HEX_RE = re.compile('|'.join(re.escape(k) for k in COLOR_MAP.keys()), re.IGNORECASE)
STYLE_RE = re.compile(r'(<style[^>]*>)(.*?)(</style>)', re.DOTALL | re.IGNORECASE)
ROOT_RE = re.compile(r':root\s*\{', re.IGNORECASE)


def find_balanced_close(s, start_brace_idx):
    depth = 0
    i = start_brace_idx
    while i < len(s):
        c = s[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _replace_outside_root(css):
    """Replace COLOR_MAP keys with vars in CSS, skipping :root{...} blocks."""
    # Build list of (start, end) ranges to skip (inside :root{...})
    skip_ranges = []
    for m in ROOT_RE.finditer(css):
        brace = css.find('{', m.start())
        close = find_balanced_close(css, brace)
        if close > 0:
            skip_ranges.append((m.start(), close + 1))
    # Iterate and rebuild
    out = []
    last = 0
    n = 0
    for m in HEX_RE.finditer(css):
        # Check if inside any skip range
        in_skip = any(s <= m.start() < e for s, e in skip_ranges)
        if in_skip:
            continue
        var_name = COLOR_MAP[m.group().lower()]
        out.append(css[last:m.start()])
        out.append(var_name)
        last = m.end()
        n += 1
    out.append(css[last:])
    return ''.join(out), n


def main():
    total = 0
    files_changed = 0
    for f in sorted(STATIC.iterdir()):
        if f.name in SKIP or f.suffix != '.html':
            continue
        txt = f.read_text(encoding='utf-8')
        n_file = 0
        new_chunks = []
        last = 0
        for m in STYLE_RE.finditer(txt):
            new_chunks.append(txt[last:m.start()])
            open_tag, css, close_tag = m.group(1), m.group(2), m.group(3)
            new_css, n = _replace_outside_root(css)
            n_file += n
            new_chunks.append(open_tag + new_css + close_tag)
            last = m.end()
        new_chunks.append(txt[last:])
        new_txt = ''.join(new_chunks)
        if new_txt != txt:
            f.write_text(new_txt, encoding='utf-8')
            total += n_file
            files_changed += 1
            print(f'{f.name}: {n_file}')
    print(f'\nFiles changed: {files_changed}')
    print(f'Total replacements: {total}')


if __name__ == '__main__':
    main()
