# fix_live_v2.py - sem caracteres especiais
path = r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\interfaces\api\routes\marketdata.py"

NEW_LIVE = (
    "\n\n# __ Live Market Data __\n"
    "_LIVE_CONTAINER = 'finanalytics_timescale'\n"
    "_LIVE_USER = 'finanalytics'\n"
    "_LIVE_DB = 'market_data'\n"
    "_VALID_RES = {'1','5','15','60','D'}\n\n"
    "def _live_query(sql):\n"
    "    import subprocess\n"
    "    r = subprocess.run(['docker','exec',_LIVE_CONTAINER,'psql','-U',_LIVE_USER,'-d',_LIVE_DB,"
    "'--no-psqlrc','-t','-A','--csv','-c',sql],capture_output=True,text=True,timeout=15)\n"
    "    if r.returncode != 0 or not r.stdout.strip(): return []\n"
    "    lines = [l for l in r.stdout.strip().splitlines() if l]\n"
    "    if not lines: return []\n"
    "    hdr = lines[0].split(',')\n"
    "    return [dict(zip(hdr,l.split(','))) for l in lines[1:]]\n\n"
    "@router.get('/live/tickers')\n"
    "def live_tickers():\n"
    "    rows = _live_query(\"SELECT DISTINCT ON (ticker) ticker,exchange,price::text AS last_price,ts::text AS last_ts FROM ticks WHERE ticker!='__warmup__' ORDER BY ticker,ts DESC\")\n"
    "    for r in rows:\n"
    "        try: r['last_price']=float(r['last_price'])\n"
    "        except: pass\n"
    "    return rows\n\n"
    "@router.get('/live/ohlc/{ticker}/latest')\n"
    "def live_ohlc_latest(ticker:str,resolution:str=Query('1')):\n"
    "    from fastapi import HTTPException\n"
    "    t=ticker.upper()\n"
    "    rows=_live_query(f\"SELECT ticker,exchange,ts::text AS ts,resolution,open::text,high::text,low::text,close::text,volume::text,quantity,trade_count FROM ohlc WHERE ticker='{t}' AND resolution='{resolution}' ORDER BY ts DESC LIMIT 1\")\n"
    "    if not rows: raise HTTPException(404,detail='Sem dados')\n"
    "    r=rows[0]\n"
    "    [r.update({k:float(r[k])}) for k in ('open','high','low','close','volume') if r.get(k)]\n"
    "    return r\n\n"
    "@router.get('/live/ohlc/{ticker}')\n"
    "def live_ohlc(ticker:str,resolution:str=Query('1'),limit:int=Query(100,ge=1,le=500)):\n"
    "    from fastapi import HTTPException\n"
    "    if resolution not in _VALID_RES: raise HTTPException(400,detail='Resolucao invalida')\n"
    "    t=ticker.upper()\n"
    "    rows=_live_query(f\"SELECT ticker,exchange,ts::text AS ts,resolution,open::text,high::text,low::text,close::text,volume::text,quantity,trade_count FROM ohlc WHERE ticker='{t}' AND resolution='{resolution}' ORDER BY ts DESC LIMIT {limit}\")\n"
    "    if not rows: raise HTTPException(404,detail='Sem OHLC')\n"
    "    [r.update({k:float(r[k])}) for r in rows for k in ('open','high','low','close','volume') if r.get(k)]\n"
    "    return {'ticker':t,'resolution':resolution,'count':len(rows),'bars':list(reversed(rows))}\n\n"
    "@router.get('/live/ticks/{ticker}')\n"
    "def live_ticks(ticker:str,limit:int=Query(100,ge=1,le=1000)):\n"
    "    from fastapi import HTTPException\n"
    "    t=ticker.upper()\n"
    "    rows=_live_query(f\"SELECT ticker,exchange,ts::text AS ts,price::text AS price,quantity,volume::text AS volume FROM ticks WHERE ticker='{t}' ORDER BY ts DESC LIMIT {limit}\")\n"
    "    if not rows: raise HTTPException(404,detail='Ticker nao encontrado')\n"
    "    [r.update({k:float(r[k])}) for r in rows for k in ('price','volume') if r.get(k)]\n"
    "    return {'ticker':t,'count':len(rows),'ticks':rows}\n"
)

with open(path, encoding='utf-8') as f:
    content = f.read()

for marker in ['# __ Live Market Data __', '# Live Market Data', '_LIVE_CONTAINER', '_LIVE_DSN', '_VALID_RES']:
    idx = content.find(marker)
    if idx != -1:
        content = content[:idx] + NEW_LIVE.lstrip()
        print(f"Replaced from marker: {marker}")
        break
else:
    content = content.rstrip() + NEW_LIVE
    print("Appended at end")

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Done")
