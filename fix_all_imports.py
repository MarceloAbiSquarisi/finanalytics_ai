import pathlib, ast, re

src = pathlib.Path('src/finanalytics_ai')
fixed_files = []
error_files = []

for f in src.rglob('*.py'):
    try:
        text = f.read_text(encoding='utf-8-sig')
    except Exception:
        continue
    
    original = text
    lines = text.splitlines(keepends=True)
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Linha indentada com import fora de bloco (4+ espacos no nivel de modulo)
        # Detecta: linha com indentacao mas que nao segue def/class/if/try/etc
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if indent > 0 and stripped.startswith(('from ', 'import ')):
            # Verificar se linha anterior NAO eh def/class/if/try/with/for/else/elif/except
            prev = new_lines[-1].rstrip() if new_lines else ''
            if not prev.endswith(':') and not prev.endswith('('):
                new_lines.append(stripped)  # desindenta
                i += 1
                continue
        new_lines.append(line)
        i += 1
    
    new_text = ''.join(new_lines)
    if new_text != original:
        try:
            ast.parse(new_text)
            f.write_text(new_text, encoding='utf-8', newline='\n')
            fixed_files.append(f.name)
        except SyntaxError as e:
            error_files.append(f'{f.name}: {e}')

print(f'Corrigidos: {fixed_files}')
print(f'Erros remanescentes: {error_files}')

# Verificacao final
remaining = []
for f in src.rglob('*.py'):
    try:
        ast.parse(f.read_text(encoding='utf-8-sig'))
    except SyntaxError as e:
        remaining.append(f'{f.name}:{e.lineno}')
print(f'Erros de sintaxe restantes: {remaining if remaining else "NENHUM"}')