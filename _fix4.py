f = r'src/finanalytics_ai/interfaces/api/static/dashboard.html'
c = open(f, encoding='utf-8').read()
before = c.count('initDaySeparators(bars)')
# Remove duplicata seja qual for o separador de linha
import re
c = re.sub(r'initDaySeparators\(bars\);[\s\n]+initDaySeparators\(bars\);', 'initDaySeparators(bars);', c)
after = c.count('initDaySeparators(bars)')
open(f,'w',encoding='utf-8').write(c)
print(f'calls: {before} -> {after}')
