f = r'src/finanalytics_ai/interfaces/api/static/dashboard.html'
lines = open(f, encoding='utf-8').readlines()

new_lines = []
for i, l in enumerate(lines, start=1):
    # Remove IIFE prematura (linha 2899 - antes de setRefreshInterval ser definida)
    if "(function(){ setRefreshInterval(-1); })(); // sempre por tick" in l:
        print(f'Removendo linha {i}: IIFE prematura')
        continue
    # Remove } solto na linha 2908
    if i == 2908 and l.strip() == '}':
        print(f'Removendo linha {i}: chave solta')
        continue
    # Substitui segunda IIFE para sempre usar -1
    if "(function(){var s=parseInt(localStorage.getItem('fa_refresh_ms')" in l:
        new_lines.append("(function(){ setRefreshInterval(-1); })(); // sempre por tick\n")
        print(f'Linha {i}: IIFE corrigida para -1')
        continue
    new_lines.append(l)

open(f, 'w', encoding='utf-8').writelines(new_lines)
print('Total linhas:', len(new_lines))
