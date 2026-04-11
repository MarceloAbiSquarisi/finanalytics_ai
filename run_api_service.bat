@echo off
cd /d D:\Projetos\finanalytics_ai_fresh
set DATABASE_URL=postgresql+asyncpg://finanalytics:secret@localhost:5432/finanalytics
set ASYNC_DATABASE_URL=postgresql+asyncpg://finanalytics:secret@localhost:5432/finanalytics
set PROFIT_TIMESCALE_DSN=postgresql://finanalytics:timescale_secret@localhost:5433/market_data
"C:\Users\marce\.local\bin\uv.exe" run uvicorn finanalytics_ai.interfaces.api.run:app --host 0.0.0.0 --port 8000 --no-access-log >> "D:\Projetos\finanalytics_ai_fresh\logs\api.log" 2>&1
