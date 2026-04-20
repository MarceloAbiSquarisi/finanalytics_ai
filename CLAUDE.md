# FinAnalytics AI — Contexto para Claude Code

## Visão Geral
Sistema de análise financeira com DayTrade via ProfitDLL (Nelogica).
Stack: FastAPI :8000 (Docker) + profit_agent :8002 (Windows host) + TimescaleDB :5433 + Redis.

## Estrutura Principal
```
D:\Projetos\finanalytics_ai_fresh\
├── src\finanalytics_ai\
│   ├── interfaces\api\
│   │   ├── app.py                    # Factory FastAPI — registra todos os routers
│   │   ├── routes\agent.py           # Proxy httpx → profit_agent :8002
│   │   ├── routes\marketdata.py      # Market data routes
│   │   └── static\dashboard.html    # Dashboard SPA (149KB)
│   ├── workers\
│   │   └── profit_agent.py           # Agente Windows — DLL wrapper HTTP server :8002
│   └── config.py                     # Settings via pydantic-settings
├── scripts\
│   ├── backfill_history.py           # Coleta histórica de ticks
│   ├── populate_daily_bars.py        # Agrega ticks OU ohlc_1m (--source) → profit_daily_bars
│   ├── import_historical_1m.py       # Importer externo CSV/Parquet → ohlc_1m
│   ├── calibrate_ml_thresholds.py    # Grid search th_buy/th_sell por ticker
│   ├── retrain_top20_h21.py          # Retreina MVPs no horizon=21d
│   ├── copom_fetch.py / _label_selic / _finetune / _infer  # Pipeline BERTimbau COPOM
│   └── migrate_to_timescale.py       # Migra Fintz PG → TimescaleDB
├── .env                              # Variáveis de ambiente
├── docker-compose.yml                # API + TimescaleDB + Redis
└── pyproject.toml                    # Dependências (uv/poetry)
```

## Serviços

| Serviço | Porta | Onde roda |
|---------|-------|-----------|
| FastAPI (finanalytics_api) | :8000 | Docker container |
| profit_agent | :8002 | Windows host (fora do Docker) |
| TimescaleDB | :5433 | Docker container |
| Redis | :6379 | Docker container |

## Credenciais de Dev (.env)
```
PROFIT_DLL_PATH=C:\Nelogica\profitdll.dll
PROFIT_ACTIVATION_KEY=1834404599450006070
PROFIT_USERNAME=marceloabisquarisi@gmail.com
PROFIT_TIMESCALE_DSN=postgresql://finanalytics:timescale_secret@localhost:5433/market_data
PROFIT_AGENT_URL=http://host.docker.internal:8002
PROFIT_SIM_BROKER_ID=15011
PROFIT_SIM_ACCOUNT_ID=216541264267275
PROFIT_SIM_ROUTING_PASSWORD=o)u$EVq4SU$[MdZN
```

## Comandos Frequentes

### Iniciar o profit_agent (Windows — terminal separado)
```powershell
cd D:\Projetos\finanalytics_ai_fresh
.venv\Scripts\python.exe src\finanalytics_ai\workers\profit_agent.py
```

### Deploy no container (sem rebuild)
```powershell
docker cp src\finanalytics_ai\interfaces\api\routes\agent.py finanalytics_api:/app/src/finanalytics_ai/interfaces/api/routes/agent.py
docker cp src\finanalytics_ai\interfaces\api\app.py finanalytics_api:/app/src/finanalytics_ai/interfaces/api/app.py
docker cp src\finanalytics_ai\interfaces\api\static\dashboard.html finanalytics_api:/app/src/finanalytics_ai/interfaces/api/static/dashboard.html
docker restart finanalytics_api
```

### Backfill histórico
```powershell
.venv\Scripts\python.exe scripts\backfill_history.py --start 2026-01-02 --end 2026-04-11 --delay 2
```

### Status do banco
```powershell
docker exec finanalytics_timescale psql -U finanalytics -d market_data -c "SELECT ticker, COUNT(DISTINCT trade_date::date) AS dias, MAX(trade_date::date) AS fim FROM market_history_trades GROUP BY ticker ORDER BY ticker;"
```

### Testes rápidos via PowerShell
```powershell
# Health
Invoke-RestMethod "http://localhost:8002/health"
Invoke-RestMethod "http://localhost:8000/api/v1/agent/health"

# Posição
Invoke-RestMethod "http://localhost:8002/position/PETR4?env=simulation&type=1"

# Enviar ordem
Invoke-RestMethod -Method POST "http://localhost:8002/order/send" -ContentType "application/json" -Body '{"env":"simulation","order_type":"market","order_side":"buy","ticker":"PETR4","exchange":"B","quantity":100,"price":-1,"is_daytrade":true}'

# OCO
Invoke-RestMethod -Method POST "http://localhost:8002/order/oco" -ContentType "application/json" -Body '{"env":"simulation","ticker":"PETR4","exchange":"B","quantity":100,"take_profit":52.00,"stop_loss":47.00,"stop_limit":46.50,"order_side":"sell","is_daytrade":true}'
```

## Endpoints do profit_agent (:8002)

```
GET  /health                    → ok:true
GET  /status                    → conexão, ticks, ordens
GET  /quotes                    → cotações em tempo real
GET  /orders                    → lista ordens do banco
POST /order/send                → envia ordem (limit/market/stop)
POST /order/cancel              → cancela por local_order_id
POST /order/cancel_all          → cancela todas
POST /order/change              → altera preço/qty (SendChangeOrderV2)
POST /order/oco                 → OCO: TP (limit) + SL (stop-limit) vinculados
POST /order/zero_position       → zera posição (SendZeroPositionV2)
GET  /oco/status/{tp_id}        → status par OCO (ativo/executado/encerrado)
GET  /positions                 → posição via banco
GET  /positions/dll             → EnumerateAllOrders (DLL) + reconcilia banco
GET  /positions/assets          → EnumerateAllPositionAssets (DLL)
GET  /position/{ticker}         → GetPositionV2 (DLL) ?type=1 (DayTrade) | 2 (Swing)
GET  /orders/reconcile          → reconcilia ordens DB vs DLL
POST /collect_history           → coleta histórico de ticks
```

## Proxy FastAPI (:8000)
Todos os endpoints acima acessíveis via `/api/v1/agent/...` (resolve bloqueio Kaspersky).

## profit_agent.py — Arquitetura

### Classes principais
- `ProfitAgent` — wrapper da DLL + HTTP server (BaseHTTPRequestHandler)
- `TConnectorAccountIdentifier`, `TConnectorAssetIdentifier` — structs ctypes
- `TConnectorOrder`, `TConnectorTradingAccountPosition` — structs de ordens/posições

### Métodos críticos
```python
agent._send_order_legacy(params)   # envia ordem via SendOrder DLL
agent.send_oco_order(params)       # OCO manual (TP + SL)
agent.get_oco_status(tp_id, env)   # status do par
agent._oco_monitor_loop()          # thread daemon 500ms auto-cancela
agent.get_positions_dll(env)       # EnumerateAllOrders — assinatura CORRETA:
                                   # POINTER(TConnectorAccountIdentifier), c_ubyte, c_long, callback
agent.enumerate_position_assets()  # EnumerateAllPositionAssets
agent.get_position_v2(ticker, ...)  # GetPositionV2 — ok=False é NORMAL, dados na struct
agent.cancel_order(params)         # SendCancelOrderV2
agent.change_order(params)         # SendChangeOrderV2
```

### Bugs conhecidos / gotchas da DLL
- `GetPositionV2` retorna `ok=False` mas dados estão corretos na struct — não tratar como erro
- `EnumerateAllOrders`: primeiro param DEVE ser `POINTER(TConnectorAccountIdentifier)`, não `c_int, c_wchar_p`
- Callbacks DEVEM ser armazenados em `self._gc_*` para evitar garbage collection
- DLL é 64-bit — Python deve ser 64-bit
- Callbacks rodam na ConnectorThread — não chamar funções DLL dentro de callbacks
- `open_side=200` na posição = zerada (valor byte residual da DLL)

### Order Types (TConnectorOrderType)
- `1` = Market (cotMarket)
- `2` = Limit (cotLimit)
- `4` = StopLimit (cotStopLimit)

### Order Side
- `1` = Buy (cosBuy)
- `2` = Sell (cosSell)

### Order Status
- `0` = New, `1` = PartialFilled, `2` = Filled, `4` = Canceled, `8` = Rejected, `10` = PendingNew

## Dashboard (dashboard.html)
SPA em vanilla JS, 3500+ linhas. Painel DayTrade no lado direito:

- **Aba Ordem**: compra/venda limit/market/stop → `/api/v1/agent/order/send`
- **Aba OCO**: TP+SL com polling automático → `/api/v1/agent/order/oco`
- **Aba Pos.**: GetPositionV2 + lista ativos abertos → `/api/v1/agent/position/{ticker}`
- **Aba Ordens**: lista com auto-refresh 5s + cancelar individual
- **Aba Conta**: CRUD de contas + seletor de conta ativa → `/api/v1/accounts/...`

Funções JS chave: `executeTrade()`, `sendOCO()`, `refreshOrders()`, `loadDLLPosition()`, `dtTab(tab)`

### Fluxo de credenciais (conta ativa → DLL)
1. Dashboard envia ordem para FastAPI proxy (`/api/v1/agent/order/send`)
2. Proxy (`agent.py`) resolve conta ativa via `AccountService.get_active()`
3. Proxy injeta `_account_broker_id`, `_account_id`, `_routing_password`, `_sub_account_id` no body
4. profit_agent (`_get_account()`) detecta campos injetados e usa em vez dos env vars
5. Fallback: sem conta ativa → profit_agent usa `PROFIT_SIM_*` / `PROFIT_PROD_*` do `.env`

## Banco de Dados

### TimescaleDB (market_data)
Tabelas principais:
- `market_history_trades` — ticks históricos (hypertable, partição por trade_date)
- `ohlc_1m` — bars 1m (hypertable 27 chunks, 3.5M rows; `source` ∈ {brapi, external_1m, nelogica_1m})
- `profit_daily_bars` — barras diárias OHLCV (geradas por `populate_daily_bars.py`)
- `fintz_cotacoes_ts` — OHLCV diário Fintz (1.32M rows, 200+ tickers, 2010→2025; **read-only**)
- `profit_orders` — ordens enviadas via DLL
- `profit_history_tickers` — tickers configurados para backfill (active=True/False)
- `trading_accounts` — contas de corretora (CRUD, conta ativa para ordens)
- `ticker_ml_config` — calibração ML por ticker (118 rows: th_buy/th_sell/best_sharpe/horizon_days)
- `copom_documents` / `copom_sentiment` — pipeline BERTimbau COPOM (vazio até BCB recuperar)

### Candle fallback chain (`candle_repository.py`)
1. `profit_daily_bars` — pré-agregado, 8 tickers DLL (Jan→Abr/2026)
2. `ohlc_1m` — bars 1m (brapi 3.5M rows + import externo `nelogica_1m`), agrega on-the-fly p/ daily
3. `market_history_trades` — agrega ticks on-the-fly (~69 dias)
4. `profit_ticks` — ticks real-time
5. `fintz_cotacoes_ts` — stocks only (exclui futuros), 200+ tickers, 2010→2025

### Estado atual dos dados (Abr/2026)

**DLL Profit (ticks + daily bars)**:
| Ticker | Dias | Completo |
|--------|------|----------|
| ABEV3  | 69   | ✅ |
| BBDC4  | 69   | ✅ |
| ITUB4  | 69   | ✅ |
| PETR4  | 64   | ✅ |
| VALE3  | 64   | ✅ |
| WDOFUT | 69   | ✅ |
| WEGE3  | 69   | ✅ |
| WINFUT | 16   | ⚠️ backfill parcial |

**Fintz (fintz_cotacoes_ts)**: 1.319.764 rows, 200+ tickers, 2010-01-04 → 2025-12-30

## Pendências Técnicas

1. ~~`SetOrderCallback → TConnectorOrder`~~ — **DONE** (callback recebe `POINTER(TConnectorOrder)` com status real)
2. ~~Multi-conta MVP~~ — **DONE** (`user_account_id` auto-populado como `{env}:{broker_id}:{account_id}`)
3. ~~Multi-conta CRUD API + UI de seleção de contas~~ — **DONE** (Sprint MC: CRUD + seletor UI; Sprint MC-2: proxy injeta credenciais da conta ativa no profit_agent)
4. ~~Sprint OHLC — Unificação Fintz + DLL~~ — **DONE** (migração 1.32M rows, daily bars, fallback chain 4 níveis)
5. ~~Fintz sync~~ — **CANCELADO** (Fintz freezada; sem mais sync)
6. ~~Migração ticks externos → 1m bars~~ — **DONE 20/abr** (`import_historical_1m.py`, `populate_daily_bars --source 1m`, fallback chain agora 5 níveis)
7. ~~ML calibração + retreino h21~~ — **DONE 20/abr** (118 tickers calibrados em `ticker_ml_config`, 116 pickles MVP h21)
8. ~~`/api/v1/ml/signals` batch + dashboard tab~~ — **DONE 20/abr**
9. ~~DI1 realtime worker~~ — **DONE 20/abr** (subscribe + Kafka publisher + Grafana 3 painéis)
10. ~~BERTimbau COPOM scaffold~~ — **DONE 20/abr** (pipeline end-to-end validado em sintético; aguarda BCB API recuperar)
11. Aguardando arquivo Nelogica 1m (~2 dias) → rodar `runbook_import_dados_historicos.md`

## Convenções do Projeto

- **Logging**: `structlog` no FastAPI, `logging` padrão no profit_agent
- **Async**: FastAPI usa `asyncio`; profit_agent usa threads (DLL é síncrona)
- **Deploy**: `docker compose build api && docker compose up -d api` (rebuild completo; `docker cp` apenas para hotfix rápido)
- **Sem frameworks pesados**: sem Django, sem ORM pesado
- **Injeção de dependência manual**: sem FastAPI `Depends` em excesso
- **Tipagem**: type hints em todo código novo

## Git
```
Remote: https://github.com/MarceloAbiSquarisi/finanalytics_ai
Branch: master
Últimos commits (20/abr):
  2b558ba feat(1m): adapta pipeline para bars 1-minuto (substitui ticks externos)
  e3e47e2 feat(copom): pipeline BERTimbau sentiment end-to-end
  dbd10e8 feat(dashboard): aba Signals mostra ML signals calibrados
  ebcc6c0 feat(mvp-h21): retreino top-20 em horizon 21d
  e78f1b9 feat(signals): /api/v1/ml/signals batch + paineis DI1 Grafana
  833f47b feat(predict_mvp): integra thresholds calibrados com signal BUY/SELL/HOLD
  8be756e feat(di1-realtime): worker funcional — subscribe + Kafka publisher
  eacf748 feat(day1): import_historical_ticks + calibrate_ml_thresholds + paineis RF
```
