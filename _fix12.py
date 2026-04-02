f = r'src/finanalytics_ai/interfaces/api/static/dashboard.html'
c = open(f, encoding='utf-8').read()

# Substitui fitContent simples por versao com initDaySeparators depois
old = '  priceChart.timeScale().fitContent();'
new = '''  priceChart.timeScale().fitContent();
  // Separadores de dia apos render
  setTimeout(function(){if(typeof initDaySeparators==='function'&&typeof bars!=='undefined')initDaySeparators(bars);},250);'''

# Substitui apenas a primeira ocorrencia (no renderPriceChart)
idx = c.find(old)
if idx >= 0:
    c = c[:idx] + new + c[idx+len(old):]
    print('OK fitContent atualizado na linha aprox:', c[:idx].count(chr(10))+1)
else:
    print('NAO ENCONTRADO')

open(f,'w',encoding='utf-8').write(c)
