f = r'src/finanalytics_ai/interfaces/api/static/dashboard.html'
c = open(f, encoding='utf-8').read()

# Adiciona guard para evitar requisicoes concorrentes
old = '''async function _doRefresh() {
  if (!currentTicker || !priceSeries) return;
  try {'''

new = '''var _refreshInProgress = false;
async function _doRefresh() {
  if (!currentTicker || !priceSeries || _refreshInProgress) return;
  _refreshInProgress = true;
  try {'''

old_end = '''  } catch(e) {}
}
(function(){ setRefreshInterval(-1); })(); // sempre por tick'''

new_end = '''  } catch(e) {}
  finally { _refreshInProgress = false; }
}
(function(){ setRefreshInterval(-1); })(); // sempre por tick'''

c = c.replace(old, new)
c = c.replace(old_end, new_end)

open(f,'w',encoding='utf-8').write(c)
print('guard adicionado:', '_refreshInProgress' in c)
print('finally adicionado:', 'finally { _refreshInProgress' in c)
