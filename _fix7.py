f = r'src/finanalytics_ai/interfaces/api/static/dashboard.html'
c = open(f, encoding='utf-8').read()

# 1. Fix initDaySeparators - cancela subscription anterior antes de criar nova
old_sub = '''  drawDaySeparators();
  var _sepTimer=null;
  priceChart.timeScale().subscribeVisibleLogicalRangeChange(function(){
    if(_sepTimer)clearTimeout(_sepTimer);
    _sepTimer=setTimeout(drawDaySeparators,60);
  });'''
new_sub = '''  drawDaySeparators();
  // Cancela listener anterior para evitar acumulo de listeners
  if(window._sepUnsub){try{window._sepUnsub();}catch(e){}}
  var _sepTimer=null;
  var _sepCb=function(){if(_sepTimer)clearTimeout(_sepTimer);_sepTimer=setTimeout(drawDaySeparators,60);};
  window._sepUnsub=priceChart.timeScale().subscribeVisibleLogicalRangeChange(_sepCb);'''
c = c.replace(old_sub, new_sub)

# 2. Fix fitContent - chama apos um frame de rendering
old_fit = 'priceChart.timeScale().fitContent(); // mostra maximo de dados'
new_fit = 'requestAnimationFrame(function(){if(priceChart)priceChart.timeScale().fitContent();}); // mostra maximo de dados'
c = c.replace(old_fit, new_fit)

# 3. Fix loadChartAndIndicators - mostra candles imediatamente, indicadores assincronos
# Separa o fetch de candles do fetch de indicadores
old_load = "const _pr2=await fetch('/api/v1/marketdata/candles/'+ticker"
# Nao modifica a logica principal, apenas garante que priceSeries.setData ocorre antes dos indicadores
# Isso ja acontece na logica atual - o problema e o fitContent

open(f,'w',encoding='utf-8').write(c)
print('_sepUnsub:', '_sepUnsub' in c)
print('requestAnimationFrame:', 'requestAnimationFrame' in c)
