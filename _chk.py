f = r'src/finanalytics_ai/interfaces/api/static/dashboard.html'
c = open(f, encoding='utf-8').read()
lines = c.split('\n')
for i,l in enumerate(lines):
    if 'initDaySeparators(bars)' in l:
        print(f'linha {i+1}: {l.strip()}')
        # contexto
        for j in range(max(0,i-2), min(len(lines),i+3)):
            print(f'  {j+1}: {lines[j][:80]}')
