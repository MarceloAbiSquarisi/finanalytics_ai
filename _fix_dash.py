f = r'src/finanalytics_ai/interfaces/api/static/dashboard.html'
c = open(f, encoding='utf-8').read()

# 1. Remove a segunda definicao duplicada de initDaySeparators (linhas 1273+)
import re
# Remove segunda ocorrencia da funcao initDaySeparators
c = re.sub(
    r'\nfunction initDaySeparators\(bars\)\{.*?^\}\n',
    '',
    c,
    count=1,
    flags=re.DOTALL|re.MULTILINE
)

# 2. Remove chamada duplicada de initDaySeparators
c = c.replace(
    'initDaySeparators(bars);\n  initDaySeparators(bars);',
    'initDaySeparators(bars);'
)

# 3. Corrige tickMarkFormatter para BRT (UTC-3)
OLD_TF = "tickMarkFormatter: (t) => { const d = new Date((t - 3*3600)*1000 + 3*3600*1000); return d.toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit',timeZone:'America/Sao_Paulo'}); }"
NEW_TF = "tickMarkFormatter: (t) => { const d = new Date(t * 1000); return d.toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit',timeZone:'America/Sao_Paulo'}); }"
c = c.replace(OLD_TF, NEW_TF)

# 4. Corrige deteccao de dia em BRT na primeira definicao de initDaySeparators
OLD_DAY = "new Date(bars[i-1].time * 1000).toDateString()!==new Date(bars[i].time*1000).toDateString()"
NEW_DAY = "new Date(bars[i-1].time*1000).toLocaleDateString('pt-BR',{timeZone:'America/Sao_Paulo'})!==new Date(bars[i].time*1000).toLocaleDateString('pt-BR',{timeZone:'America/Sao_Paulo'})"
c = c.replace(OLD_DAY, NEW_DAY)

# Tambem na primeira versao (linhas 1233+)
OLD_DAY2 = "var dPrev = new Date(bars[i-1].time * 1000).toDateString();\n    var dCurr = new Date(bars[i].time   * 1000).toDateString();\n    if (dPrev !== dCurr)"
NEW_DAY2 = "var dPrev = new Date(bars[i-1].time*1000).toLocaleDateString('pt-BR',{timeZone:'America/Sao_Paulo'});\n    var dCurr = new Date(bars[i].time*1000).toLocaleDateString('pt-BR',{timeZone:'America/Sao_Paulo'});\n    if (dPrev !== dCurr)"
c = c.replace(OLD_DAY2, NEW_DAY2)

open(f,'w',encoding='utf-8').write(c)
print('initDaySeparators count:', c.count('function initDaySeparators'))
print('initDaySeparators calls:', c.count('initDaySeparators(bars)'))
print('tickMarkFormatter correto:', 't * 1000' in c)
print('BRT locale:', 'America/Sao_Paulo' in c)
