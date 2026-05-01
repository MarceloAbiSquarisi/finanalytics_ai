# fix_ws_sse.py - troca WebSocket por SSE (Server-Sent Events)
path = r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\interfaces\api\routes\marketdata.py"

with open(path, encoding='utf-8') as f:
    content = f.read()

# Remove tudo a partir do marcador WS
marker = "# __ WebSocket Live Ticks __"
idx = content.find(marker)
if idx == -1:
    print("Marcador nao encontrado, adicionando ao final")
    idx = len(content)

SSE_CODE = (
    "# __ SSE Live Ticks __\n"
    "from fastapi.responses import StreamingResponse\n"
    "import asyncio as _aio, json as _json\n\n"
    "@router.get('/live/sse/tickers', summary='SSE stream de precos ao vivo')\n"
    "async def sse_tickers(interval: float = 1.0):\n"
    "    async def gen():\n"
    "        while True:\n"
    "            try:\n"
    "                rows = await _aio.to_thread(_live_query,\n"
    "                    \"SELECT DISTINCT ON (ticker) ticker,exchange,price::text AS last_price,ts::text AS last_ts \"\n"
    "                    \"FROM ticks WHERE ticker!='__warmup__' ORDER BY ticker,ts DESC\"\n"
    "                )\n"
    "                for r in rows:\n"
    "                    try: r['last_price'] = float(r['last_price'])\n"
    "                    except: pass\n"
    "                yield f'data: {_json.dumps(rows)}\\n\\n'\n"
    "            except Exception:\n"
    "                yield 'data: []\\n\\n'\n"
    "            await _aio.sleep(interval)\n"
    "    return StreamingResponse(gen(), media_type='text/event-stream',\n"
    "        headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})\n\n"
    "@router.get('/live/sse/ticks/{ticker}', summary='SSE stream de ticks de um ticker')\n"
    "async def sse_ticks(ticker: str, interval: float = 0.5):\n"
    "    t = ticker.upper()\n"
    "    async def gen():\n"
    "        last_ts = None\n"
    "        while True:\n"
    "            try:\n"
    "                rows = await _aio.to_thread(_live_query,\n"
    "                    f\"SELECT ticker,exchange,ts::text AS ts,price::text AS price,quantity,volume::text AS volume \"\n"
    "                    f\"FROM ticks WHERE ticker='{t}' ORDER BY ts DESC LIMIT 1\"\n"
    "                )\n"
    "                if rows:\n"
    "                    r = rows[0]\n"
    "                    if r.get('ts') != last_ts:\n"
    "                        last_ts = r.get('ts')\n"
    "                        for k in ('price','volume'):\n"
    "                            try: r[k] = float(r[k])\n"
    "                            except: pass\n"
    "                        yield f'data: {_json.dumps(r)}\\n\\n'\n"
    "            except Exception:\n"
    "                pass\n"
    "            await _aio.sleep(interval)\n"
    "    return StreamingResponse(gen(), media_type='text/event-stream',\n"
    "        headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})\n"
)

content = content[:idx] + SSE_CODE
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("OK - SSE endpoints adicionados")
