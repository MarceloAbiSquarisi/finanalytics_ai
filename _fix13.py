f = r'src/finanalytics_ai/interfaces/api/static/dashboard.html'
c = open(f, encoding='utf-8').read()

# 1. Remove o select de refresh-sel do HTML
import re
c = re.sub(r'<select id="refresh-sel".*?</select>', '', c)

# 2. Fixa setRefreshInterval para sempre usar -1 (por tick = 200ms)
c = c.replace(
    '(function(){ var s=parseInt(localStorage.getItem(\'fa_refresh_ms\')||\'5000\'); setRefreshInterval(s); })();',
    '(function(){ setRefreshInterval(-1); })(); // sempre por tick'
)

open(f,'w',encoding='utf-8').write(c)
print('select removido:', 'refresh-sel' not in c)
print('sempre por tick:', 'setRefreshInterval(-1)' in c)
