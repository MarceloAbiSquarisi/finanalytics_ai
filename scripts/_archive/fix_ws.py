# fix_ws.py - corrige assinatura dos endpoints WebSocket
path = r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\interfaces\api\routes\marketdata.py"

with open(path, encoding='utf-8') as f:
    content = f.read()

# Fix 1: import WebSocket se nao existir
if 'from fastapi import' in content and 'WebSocket' not in content:
    content = content.replace(
        'from fastapi import',
        'from fastapi import WebSocket,',
        1
    )
elif 'WebSocket' not in content:
    content = 'from fastapi import WebSocket\n' + content

# Fix 2: adiciona tipo WebSocket aos handlers
content = content.replace(
    'async def ws_ticks(websocket, ticker: str):',
    'async def ws_ticks(websocket: WebSocket, ticker: str):'
)
content = content.replace(
    'async def ws_all_tickers(websocket):',
    'async def ws_all_tickers(websocket: WebSocket):'
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

# Verifica
if 'WebSocket' in content and 'ws_ticks(websocket: WebSocket' in content:
    print("OK - WebSocket tipado corretamente")
else:
    print("VERIFICAR: " + ("WebSocket import ok" if "WebSocket" in content else "falta import"))
