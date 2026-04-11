# patch_chunk_warmup.py
# Adiciona pre-criacao automatica de chunk no lifespan do app.py

path = r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\interfaces\api\app.py"

WARMUP_CODE = '''        # ── Chunk warmup automatico (garante chunk do dia no TimescaleDB) ──────
        try:
            import subprocess as _sp
            _sp.run([
                "docker","exec","finanalytics_timescale",
                "psql","-U","finanalytics","-d","market_data","--no-psqlrc","-c",
                "INSERT INTO ticks (ticker,exchange,ts,trade_number,price,quantity,volume,trade_type) "
                "VALUES ('__warmup__','B',now(),0,1.0,1,1.0,0) ON CONFLICT DO NOTHING; "
                "DELETE FROM ticks WHERE ticker='__warmup__';"
            ], capture_output=True, timeout=10)
            logger.info("timescale.chunk.warmup.ok")
        except Exception as _wex:
            logger.warning("timescale.chunk.warmup.failed", error=str(_wex))
        # ── Fim chunk warmup ─────────────────────────────────────────────────
'''

with open(path, encoding='utf-8') as f:
    content = f.read()

if 'chunk.warmup' in content:
    print("JA EXISTE — nada a fazer")
else:
    # Injeta logo apos timescale_ok = True
    target = '        timescale_ok = True\n        logger.info("timescale.connected")'
    replacement = '        timescale_ok = True\n        logger.info("timescale.connected")\n' + WARMUP_CODE
    if target in content:
        content = content.replace(target, replacement, 1)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        print("OK — chunk warmup automatico adicionado ao lifespan")
    else:
        # Tenta variante sem aspas duplas no logger
        target2 = "        timescale_ok = True"
        idx = content.find(target2)
        if idx != -1:
            content = content[:idx + len(target2)] + '\n' + WARMUP_CODE + content[idx + len(target2):]
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            print("OK — chunk warmup adicionado (variante)")
        else:
            print("ERRO: ponto de injecao nao encontrado")
            # Mostra contexto
            idx2 = content.find("timescale_ok")
            print(repr(content[idx2:idx2+200]))
