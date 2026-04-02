f = r'src/finanalytics_ai/interfaces/api/static/dashboard.html'
lines = open(f, encoding='utf-8').readlines()

# Encontra e mostra contexto em torno do problema
for i,l in enumerate(lines[2890:2920], start=2891):
    print(f'{i}: {l}', end='')
