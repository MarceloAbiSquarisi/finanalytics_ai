f = r'src/finanalytics_ai/interfaces/api/static/dashboard.html'
c = open(f, encoding='utf-8').read()

# 1. Remove o setTimeout de fitContent que causa re-render desnecessario
c = c.replace(
    'priceChart.timeScale().fitContent();\n  setTimeout(()=>{ if(priceChart) priceChart.timeScale().fitContent(); }, 200);',
    'priceChart.timeScale().fitContent();'
)

# 2. Adiciona debounce no drawDaySeparators para evitar redesenho excessivo por tick
OLD_SUB = 'priceChart.timeScale().subscribeVisibleLogicalRangeChange(function(){setTimeout(drawDaySeparators,16);});'
NEW_SUB = '''var _sepTimer=null;
  priceChart.timeScale().subscribeVisibleLogicalRangeChange(function(){
    if(_sepTimer)clearTimeout(_sepTimer);
    _sepTimer=setTimeout(drawDaySeparators,60);
  });'''
c = c.replace(OLD_SUB, NEW_SUB)

open(f,'w',encoding='utf-8').write(c)
print('fitContent duplo removido:', 'setTimeout(()=>{ if(priceChart)' not in c)
print('debounce 60ms:', '_sepTimer' in c)
