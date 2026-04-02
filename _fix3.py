f = r'src/finanalytics_ai/interfaces/api/static/dashboard.html'
c = open(f, encoding='utf-8').read()

# 1. Corrige tickMarkFormatter - timestamps ja estao em BRT, so formata HH:MM direto
OLD_TF = "tickMarkFormatter: (t) => { const d = new Date(t * 1000); return d.toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit',timeZone:'America/Sao_Paulo'}); }"
NEW_TF = "tickMarkFormatter: (t) => { const d = new Date(t * 1000); const h = String(d.getUTCHours()).padStart(2,'0'); const m = String(d.getUTCMinutes()).padStart(2,'0'); return h+':'+m; }"
c = c.replace(OLD_TF, NEW_TF)

# 2. Corrige separadores - usa UTC date (timestamps ja em BRT)
OLD_DAY = "new Date(bars[i-1].time*1000).toLocaleDateString('pt-BR',{timeZone:'America/Sao_Paulo'})!==new Date(bars[i].time*1000).toLocaleDateString('pt-BR',{timeZone:'America/Sao_Paulo'})"
NEW_DAY = "Math.floor(bars[i-1].time/86400)!==Math.floor(bars[i].time/86400)"
c = c.replace(OLD_DAY, NEW_DAY)

OLD_DAY2 = "var dPrev = new Date(bars[i-1].time*1000).toLocaleDateString('pt-BR',{timeZone:'America/Sao_Paulo'});\n    var dCurr = new Date(bars[i].time*1000).toLocaleDateString('pt-BR',{timeZone:'America/Sao_Paulo'});\n    if (dPrev !== dCurr)"
NEW_DAY2 = "var dPrev = Math.floor(bars[i-1].time/86400);\n    var dCurr = Math.floor(bars[i].time/86400);\n    if (dPrev !== dCurr)"
c = c.replace(OLD_DAY2, NEW_DAY2)

# 3. Remove segunda chamada duplicada de initDaySeparators
c = c.replace('initDaySeparators(bars);\n  initDaySeparators(bars);','initDaySeparators(bars);')

# 4. Adiciona fitContent apos um breve delay para garantir render completo
c = c.replace(
    'priceChart.timeScale().fitContent(); // mostra maximo de dados',
    'priceChart.timeScale().fitContent();\n  setTimeout(()=>{ if(priceChart) priceChart.timeScale().fitContent(); }, 200);'
)

open(f,'w',encoding='utf-8').write(c)
print('calls:', c.count('initDaySeparators(bars)'))
print('tickMark UTC:', 'getUTCHours' in c)
print('sep math:', 'Math.floor(bars[i-1].time/86400)' in c)
