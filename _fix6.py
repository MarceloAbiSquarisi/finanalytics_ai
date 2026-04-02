f = r'src/finanalytics_ai/interfaces/api/static/dashboard.html'
c = open(f, encoding='utf-8').read()

# 1. Remove a segunda definicao de setRefreshInterval + _doRefresh (a ruim)
# Ela comeca com: var _refreshTimer = null;\nfunction setRefreshInterval
import re
# Remove desde "var _refreshTimer = null;" ate o fim do segundo _doRefresh
bad = re.search(
    r'var _refreshTimer = null;\s*\nfunction setRefreshInterval.*?^\}\n',
    c, re.DOTALL | re.MULTILINE
)
if bad:
    c = c[:bad.start()] + c[bad.end():]
    print('Bloco ruim removido')
else:
    print('AVISO: bloco nao encontrado')

# 2. Corrige _tsC/_ts/_ts2 para subtrair 3h (BRT = UTC-3)
# A funcao converte ts ISO -> unix. Adiciona -3*3600 ao resultado
for old, new in [
    ("return new Date(ts).getTime()/1000;}return new Date(ts).getTime()/1000;}",
     "return new Date(ts).getTime()/1000 - 10800;}return new Date(ts).getTime()/1000 - 10800;}"),
    ("return Date.UTC(+dv[2],+dv[1]-1,+dv[0],+h[0],+h[1],+h[2]?+h[2]:0)/1000;}return new Date(ts).getTime()/1000;}",
     "return Date.UTC(+dv[2],+dv[1]-1,+dv[0],+h[0],+h[1],+h[2]?+h[2]:0)/1000 - 10800;}return new Date(ts).getTime()/1000 - 10800;}"),
]:
    if old in c:
        c = c.replace(old, new)

# Fix mais simples: substitui todas as ocorrencias da funcao _ts
c = re.sub(
    r'(function _ts[C2]?\(ts\)\{.*?return new Date\(ts\)\.getTime\(\)/1000;?\})',
    lambda m: m.group(0).replace('getTime()/1000;', 'getTime()/1000-10800;').replace('getTime()/1000}', 'getTime()/1000-10800}'),
    c
)
c = re.sub(
    r'(Date\.UTC\([^)]+\)/1000)',
    r'\1-10800',
    c
)

open(f,'w',encoding='utf-8').write(c)
print('_doRefresh count:', c.count('async function _doRefresh'))
print('setRefreshInterval count:', c.count('function setRefreshInterval'))
print('-10800 count:', c.count('-10800'))
