f = r'src/finanalytics_ai/interfaces/api/static/dashboard.html'
c = open(f, encoding='utf-8').read()

# 1. Adiciona finally para liberar o guard
c = c.replace(
    "  } catch(e) {}\n}\n(function(){ setRefreshInterval(-1); })()",
    "  } catch(e) { console.warn('_doRefresh error:', e); }\n  finally { _refreshInProgress = false; }\n}\n(function(){ setRefreshInterval(-1); })()"
)

# 2. Aumenta intervalo de 200ms para 1000ms (por tick = 1s)
c = c.replace(
    '_refreshTimer = setInterval(_doRefresh, ms === -1 ? 200 : ms)',
    '_refreshTimer = setInterval(_doRefresh, ms === -1 ? 1000 : ms)'
)

open(f,'w',encoding='utf-8').write(c)
print('finally:', 'finally { _refreshInProgress' in c)
print('1000ms:', '? 1000 :' in c)
