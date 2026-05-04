# Operações — comandos frequentes

## Iniciar / restart o profit_agent (Windows)

Roda como serviço NSSM `FinAnalyticsAgent`. Para restart preferir `/agent/restart` via API (sudo `admin123`) — funciona end-to-end em ~9s desde fix NSSM `AppExit=Restart` (sessão 30/abr) + import `_hard_exit` (fix 04/mai). Fallback `Restart-Service FinAnalyticsAgent` (admin).

Manual standalone só pra debug:
```powershell
cd D:\Projetos\finanalytics_ai_fresh
.venv\Scripts\python.exe src\finanalytics_ai\workers\profit_agent.py
```

## Subir/parar a stack completa (Engine WSL2)

Sempre passar os 3 compose files (main + override + wsl). Volumes Postgres+Timescale agora em ext4 nativo (`/home/abi/finanalytics/data/`), demais volumes ainda em `/mnt/e/finanalytics_data/`. **Importante**: rodar `compose` de dentro do WSL bash — PowerShell direto resolve paths como Windows-absolute e quebra (gotcha #6 sessão 01/mai).

```bash
# Up (dentro do WSL Ubuntu-22.04)
cd /mnt/d/Projetos/finanalytics_ai_fresh
DATA_DIR_HOST=/mnt/e/finanalytics_data \
  docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.wsl.yml up -d

# Down
docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.wsl.yml down

# Recreate forçando nova imagem (após rebuild)
docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.wsl.yml up -d --force-recreate <service>
```

```powershell
# Comandos diretos (docker ps/exec/logs) funcionam no PS via context wsl-engine
docker ps
docker logs finanalytics_api --tail 50

# Reativar Docker Desktop fallback (autostart desativado desde 01/mai):
& "C:\Program Files\Docker\Docker\Docker Desktop.exe"
docker context use default
```

## Rebuild de imagem worker (após código alterar)

⚠️ `docker compose up -d` NÃO atualiza código baked na imagem. Sempre `build` antes de recreate quando alterar `src/` que workers/scripts importam.

```bash
cd /mnt/d/Projetos/finanalytics_ai_fresh
docker compose build worker auto_trader  # ~5min com cache
docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.wsl.yml up -d --force-recreate auto_trader
```

## Deploy hotfix no container (sem rebuild — só pra fix rápido em api)

```powershell
docker cp src\finanalytics_ai\interfaces\api\routes\agent.py finanalytics_api:/app/src/finanalytics_ai/interfaces/api/routes/agent.py
docker cp src\finanalytics_ai\interfaces\api\app.py finanalytics_api:/app/src/finanalytics_ai/interfaces/api/app.py
docker cp src\finanalytics_ai\interfaces\api\static\dashboard.html finanalytics_api:/app/src/finanalytics_ai/interfaces/api/static/dashboard.html
docker restart finanalytics_api
```

## Backfill / status banco

```powershell
.venv\Scripts\python.exe scripts\backfill_history.py --start 2026-01-02 --end 2026-04-11 --delay 2
docker exec finanalytics_timescale psql -U finanalytics -d market_data -c "SELECT ticker, COUNT(DISTINCT trade_date::date) AS dias, MAX(trade_date::date) AS fim FROM market_history_trades GROUP BY ticker ORDER BY ticker;"
```

## Testes rápidos

```powershell
Invoke-RestMethod "http://localhost:8000/api/v1/agent/health"
Invoke-RestMethod -Method POST "http://localhost:8002/order/send" -ContentType "application/json" -Body '{"env":"simulation","order_type":"market","order_side":"buy","ticker":"PETR4","exchange":"B","quantity":100,"price":-1,"is_daytrade":true}'
```

OCO + posição: ver bodies de exemplo em `runbook_profit_agent.md`.

## Robô — pause / resume

```powershell
# Login + sudo + pause
$login = Invoke-RestMethod -Method POST "http://localhost:8000/api/v1/auth/login" -ContentType "application/json" -Body '{"email":"marceloabisquarisi@gmail.com","password":"admin123"}'
$h = @{Authorization="Bearer $($login.access_token)"}
$sudo = Invoke-RestMethod -Method POST "http://localhost:8000/api/v1/auth/sudo" -ContentType "application/json" -Headers $h -Body '{"password":"admin123","ttl_minutes":15}'
$hsudo = @{Authorization="Bearer $($login.access_token)"; "X-Sudo-Token"=$sudo.sudo_token}
Invoke-RestMethod -Method PUT "http://localhost:8000/api/v1/robot/pause" -ContentType "application/json" -Headers $hsudo -Body '{"reason":"manual_kill"}'
Invoke-RestMethod -Method PUT "http://localhost:8000/api/v1/robot/resume" -ContentType "application/json" -Headers $hsudo
```
