f = r'src/finanalytics_ai/interfaces/api/static/dashboard.html'
c = open(f, encoding='utf-8').read()

# 1. Corrige _tsC: tem -10800 duplicado (subtrai 6h em vez de 3h)
c = c.replace(
    'return Date.UTC(+dv[2],+dv[1]-1,+dv[0],+h[0],+h[1],+h[2]?+h[2]:0)/1000-10800 - 10800;}return new Date(ts).getTime()/1000 - 10800;}',
    'return Date.UTC(+dv[2],+dv[1]-1,+dv[0],+h[0],+h[1],+h[2]?+h[2]:0)/1000-10800;}return new Date(ts).getTime()/1000-10800;}'
)

# 2. Remove fallback BRAPI em loadChartAndIndicators
# Se Profit falhar, nao vai para BRAPI - lanca erro
c = c.replace(
    "}} if(!_hd2)_hd2=await api('/api/v1/quotes/'+ticker+'/history?range='+range+'&interval='+interval); const histData=_hd2;",
    "}} if(!_hd2)throw new Error('Sem dados do Profit Agent para '+ticker); const histData=_hd2;"
)

# 3. Remove o setInterval de linha 2608 que usa quotes/history (BRAPI)
# Esse loop e duplicata do _doRefresh e usa BRAPI
import re
c = re.sub(
    r"// Auto-refresh candle mais recente a cada 1s\nsetInterval\(async function\(\).*?}, \d+\);",
    "// Auto-refresh feito pelo _doRefresh via /last endpoint",
    c,
    flags=re.DOTALL
)

# 4. No catch do loadChartAndIndicators, remove referencia a histData.bars (undefined no catch)
c = c.replace(
    "renderADX(indData.adx, histData.bars.map(function(b){return b.time;}));\n    updateSignals(indData);\n    toast('BRAPI indisponivel -- modo demo', 'err');",
    "renderADX(indData.adx, bars.map(function(b){return b.time;}));\n    updateSignals(indData);\n    toast('Profit Agent indisponivel -- modo demo', 'err');"
)

open(f,'w',encoding='utf-8').write(c)
print('duplo -10800 corrigido:', '-10800 - 10800' not in c)
print('fallback BRAPI removido:', 'quotes/history' not in c)
print('setInterval BRAPI removido:', 'Auto-refresh feito' in c)
