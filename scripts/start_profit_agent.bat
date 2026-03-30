@echo off
cd /d D:\Projetos\finanalytics_ai_fresh
set PYTHONPATH=src
set PYTHONUTF8=1
set "PROFIT_DLL_PATH=C:\Nelogica\ProfitDLL.dll"
set "PROFIT_AGENT_PORT=8001"
set "PROFIT_SUBSCRIBE_TICKERS=PETR4,VALE3,ITUB4,BBDC4,ABEV3,WEGE3,WINFUT,WDOFUT"
set "PROFIT_LOG_FILE=D:\Projetos\finanalytics_ai_fresh\logs\profit_agent.log"
set "PROFIT_TIMESCALE_DSN=postgresql://finanalytics:timescale_secret@localhost:5433/market_data"

echo [%date% %time%] Iniciando profit_agent
"C:\Users\marce\.local\bin\uv.exe" run python -m finanalytics_ai.workers.profit_agent
echo [%date% %time%] Agent encerrado com codigo %ERRORLEVEL%
exit /b %ERRORLEVEL%