f = r'src/finanalytics_ai/interfaces/api/static/dashboard.html'
c = open(f, encoding='utf-8').read()

# Substitui loadChartAndIndicators para separar candles de indicadores
old = """    const _pr2=await fetch('/api/v1/marketdata/candles/'+ticker+'?resolution='+_res2+'&limit=5000');
    function _tsC(ts){if(typeof ts==='string'&&ts.indexOf('/')>-1){var p=ts.split(' ');var dv=p[0].split('/');var h=p[1]?p[1].split(':'):[0,0,0];return Date.UTC(+dv[2],+dv[1]-1,+dv[0],+h[0],+h[1],+h[2]?+h[2]:0)/1000-10800;}return new Date(ts).getTime()/1000-10800;}
    let _hd2=null; if(_pr2.ok){const _pd2=await _pr2.json();if(_pd2.candles&&_pd2.candles.length>5){_hd2={bars:_pd2.candles.map(c=>({time:_tsC(c.ts),open:+c.open,high:+c.high,low:+c.low,close:+c.close,volume:+(c.volume||0)}))};}} if(!_hd2)throw new Error('Sem dados do Profit Agent para '+ticker); const histData=_hd2;
    const indData=await api('/api/v1/quotes/'+ticker+'/indicators?range='+range);
    indCache = indData; indCache._bars = histData.bars;
    if (!indData.adx) indData.adx = computeADXFromBars(histData.bars, indConfig.adxPeriod);
    renderPriceChart(histData.bars, indData.bollinger);
    renderRSI(indData.rsi, indData.timestamps);
    renderMACD(indData.macd, indData.timestamps);
    renderADX(indData.adx, histData.bars.map(function(b){return b.time;}));
    updateSignals(indData);"""

new = """    const _pr2=await fetch('/api/v1/marketdata/candles/'+ticker+'?resolution='+_res2+'&limit=5000');
    function _tsC(ts){if(typeof ts==='string'&&ts.indexOf('/')>-1){var p=ts.split(' ');var dv=p[0].split('/');var h=p[1]?p[1].split(':'):[0,0,0];return Date.UTC(+dv[2],+dv[1]-1,+dv[0],+h[0],+h[1],+h[2]?+h[2]:0)/1000-10800;}return new Date(ts).getTime()/1000-10800;}
    if(!_pr2.ok) throw new Error('Profit Agent indisponivel para '+ticker);
    const _pd2=await _pr2.json();
    if(!_pd2.candles||_pd2.candles.length<2) throw new Error('Sem candles para '+ticker);
    const histData={bars:_pd2.candles.map(c=>({time:_tsC(c.ts),open:+c.open,high:+c.high,low:+c.low,close:+c.close,volume:+(c.volume||0)}))};
    // Renderiza candles imediatamente
    renderPriceChart(histData.bars, null);
    // Indicadores em paralelo - falha silenciosamente
    let indData={};
    try {
      indData=await api('/api/v1/quotes/'+ticker+'/indicators?range='+range);
      if (!indData.adx) indData.adx = computeADXFromBars(histData.bars, indConfig.adxPeriod);
      renderRSI(indData.rsi, indData.timestamps);
      renderMACD(indData.macd, indData.timestamps);
      renderADX(indData.adx, histData.bars.map(function(b){return b.time;}));
      updateSignals(indData);
    } catch(e2) { console.warn('Indicadores indisponiveis:', e2.message); }
    indCache = indData; indCache._bars = histData.bars;"""

if old in c:
    c = c.replace(old, new)
    print('OK - loadChartAndIndicators separado')
else:
    print('AVISO - padrao nao encontrado, verificar manualmente')

open(f,'w',encoding='utf-8').write(c)
