f = r'src/finanalytics_ai/interfaces/api/static/dashboard.html'
c = open(f, encoding='utf-8').read()

# 1. Move initDaySeparators para DEPOIS do fitContent + delay
c = c.replace(
    'priceSeries.setData(bars);\n  initDaySeparators(bars);',
    'priceSeries.setData(bars);'
)

# 2. Chama initDaySeparators apos fitContent com delay para nao interferir
c = c.replace(
    'requestAnimationFrame(function(){if(priceChart)priceChart.timeScale().fitContent();}); // mostra maximo de dados',
    'requestAnimationFrame(function(){if(priceChart){priceChart.timeScale().fitContent();setTimeout(function(){initDaySeparators(bars);},300);}});'
)

open(f,'w',encoding='utf-8').write(c)
print('initDaySeparators apos fitContent:', 'fitContent();setTimeout(function(){initDaySeparators' in c)
print('setData sem init:', 'setData(bars);\n  initDaySeparators' not in c)
