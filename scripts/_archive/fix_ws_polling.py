# fix_ws_polling.py - substitui ws com polling via docker exec psql
path = r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\interfaces\api\routes\marketdata.py"

with open(path, encoding='utf-8') as f:
    content = f.read()

OLD_WS = "# __ WebSocket Live Ticks __"
idx = content.find(OLD_WS)
if idx == -1:
    print("Marcador nao encontrado - adicionando ao final")
    idx = len(content)
    prefix = content
else:
    prefix = content[:idx]

NEW_WS = (
    "# __ WebSocket Live Ticks __\n"
    "import asyncio as _asyncio\n\n"
    "@router.websocket('/live/ws/ticks/{ticker}')\n"
    "async def ws_ticks(websocket: WebSocket, ticker: str, interval: float = 0.5):\n"
    "    await websocket.accept()\n"
    "    t = ticker.upper()\n"
    "    last_ts = None\n"
    "    try:\n"
    "        while True:\n"
    "            rows = _live_query(\n"
    "                f\"SELECT ticker,exchange,ts::text AS ts,price::text AS price,quantity,volume::text AS volume \"\n"
    "                f\"FROM ticks WHERE ticker='{t}' ORDER BY ts DESC LIMIT 1\"\n"
    "            )\n"
    "            if rows:\n"
    "                r = rows[0]\n"
    "                if r.get('ts') != last_ts:\n"
    "                    last_ts = r.get('ts')\n"
    "                    for k in ('price','volume'):\n"
    "                        try: r[k] = float(r[k])\n"
    "                        except: pass\n"
    "                    import json as _json\n"
    "                    await websocket.send_text(_json.dumps(r))\n"
    "            await _asyncio.sleep(interval)\n"
    "    except Exception:\n"
    "        pass\n"
    "    finally:\n"
    "        try: await websocket.close()\n"
    "        except: pass\n\n"
    "@router.websocket('/live/ws/tickers')\n"
    "async def ws_all_tickers(websocket: WebSocket, interval: float = 1.0):\n"
    "    await websocket.accept()\n"
    "    try:\n"
    "        while True:\n"
    "            rows = _live_query(\n"
    "                \"SELECT DISTINCT ON (ticker) ticker,exchange,price::text AS last_price,ts::text AS last_ts \"\n"
    "                \"FROM ticks WHERE ticker!='__warmup__' ORDER BY ticker,ts DESC\"\n"
    "            )\n"
    "            for r in rows:\n"
    "                try: r['last_price'] = float(r['last_price'])\n"
    "                except: pass\n"
    "            import json as _json\n"
    "            await websocket.send_text(_json.dumps(rows))\n"
    "            await _asyncio.sleep(interval)\n"
    "    except Exception:\n"
    "        pass\n"
    "    finally:\n"
    "        try: await websocket.close()\n"
    "        except: pass\n"
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(prefix.rstrip() + '\n\n' + NEW_WS)
print("OK - WebSocket reescrito com polling psql")
