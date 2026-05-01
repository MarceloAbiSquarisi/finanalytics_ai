import pathlib

f = pathlib.Path('src/finanalytics_ai/interfaces/api/app.py')
t = f.read_text(encoding='utf-8')
old = "        return _html('screener.html')"
new = "        return _html('screener.html')\n\n    @app.get('/ml', response_class=HTMLResponse, include_in_schema=False)\n    async def serve_ml() -> HTMLResponse:\n        return _html('ml.html')"
t2 = t.replace(old, new, 1)
if t2 == t:
    # tenta com aspas duplas
    old2 = '        return _html("screener.html")'
    new2 = '        return _html("screener.html")\n\n    @app.get("/ml", response_class=HTMLResponse, include_in_schema=False)\n    async def serve_ml() -> HTMLResponse:\n        return _html("ml.html")'
    t2 = t.replace(old2, new2, 1)
f.write_text(t2, encoding='utf-8', newline='\n')
print('OK' if t2 != t else 'FALHOU')
