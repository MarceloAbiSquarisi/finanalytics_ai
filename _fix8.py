f = r'src/finanalytics_ai/interfaces/api/static/dashboard.html'
lines = open(f, encoding='utf-8').readlines()

# Encontra a segunda definicao de _doRefresh (linha ~2930)
# e remove tudo desde ela ate o proximo bloco de codigo significativo
idxs = [i for i,l in enumerate(lines) if 'async function _doRefresh' in l]
print('_doRefresh nas linhas:', [i+1 for i in idxs])

if len(idxs) >= 2:
    start = idxs[1]  # segunda definicao
    # Remove desde a segunda definicao ate o proximo '})();' ou 'function ' no mesmo nivel
    end = start + 1
    brace_depth = 0
    in_func = False
    while end < len(lines):
        l = lines[end]
        if 'async function _doRefresh' in lines[start] and '{' in l:
            in_func = True
        if in_func:
            brace_depth += l.count('{') - l.count('}')
            if brace_depth <= 0 and in_func:
                end += 1
                # Pula linha vazia depois
                while end < len(lines) and lines[end].strip() == '':
                    end += 1
                break
        end += 1
    print(f'Removendo linhas {start+1} a {end}')
    lines = lines[:start] + lines[end:]

open(f, 'w', encoding='utf-8').writelines(lines)
# Verifica
c = open(f, encoding='utf-8').read()
print('_doRefresh count:', c.count('async function _doRefresh'))
print('setData(bars.map linha 2930 removido:', 'priceSeries.setData(bars.map' not in c)
