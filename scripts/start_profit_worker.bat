@echo off
cd /d D:\Projetos\finanalytics_ai_fresh
set PYTHONPATH=src
set PYTHONUTF8=1
echo [%date% %time%] Iniciando profit_market_worker
"C:\Users\marce\.local\bin\uv.exe" run python -m finanalytics_ai.workers.profit_market_worker
echo [%date% %time%] Worker encerrado com codigo %ERRORLEVEL%
exit /b %ERRORLEVEL%
