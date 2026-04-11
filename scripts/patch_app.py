import sys

path = r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\interfaces\api\app.py"

with open(path, encoding="utf-8") as f:
    txt = f.read()

old = 'app.include_router(marketdata_routes.router, tags=["Market Data"])'
new = old + '\n    app.include_router(live_market_routes.router, tags=["Live Market Data"])'

if "live_market_routes.router" in txt:
    print("JA EXISTE — nada a fazer")
elif old in txt:
    txt2 = txt.replace(old, new, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt2)
    print("OK — include_router adicionado")
else:
    print("PATTERN NAO ENCONTRADO — conteudo atual:")
    for i, line in enumerate(txt.splitlines()):
        if "include_router" in line and "market" in line.lower():
            print(f"  linha {i+1}: {line}")
