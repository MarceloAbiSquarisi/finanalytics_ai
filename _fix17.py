f = r'src/finanalytics_ai/interfaces/api/static/dashboard.html'
lines = open(f, encoding='utf-8').readlines()

for i, l in enumerate(lines):
    if "  } catch(e) {}" in l and i+1 < len(lines) and lines[i+1].strip() == '}':
        print(f'Encontrado na linha {i+1}')
        lines[i] = "  } catch(e) { console.warn('refresh err',e); } finally { _refreshInProgress = false; }\n"
        print('Substituido')
        break

open(f, 'w', encoding='utf-8').writelines(lines)
c = open(f, encoding='utf-8').read()
print('finally ok:', 'finally { _refreshInProgress' in c)
