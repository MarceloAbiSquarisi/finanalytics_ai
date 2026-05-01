# FinAnalytics AI — Contexto para Claude Code

## Visão Geral
Sistema de análise financeira com DayTrade via ProfitDLL (Nelogica).
Stack: FastAPI :8000 (Docker) + profit_agent :8002 (Windows host) + TimescaleDB :5433 + Redis.

**Runtime Docker (desde 01/mai/2026)**: rodando em **Docker Engine direto em WSL2 Ubuntu-22.04** (não Docker Desktop). I1 Fases A+B.1+B.2 todas completas — volumes Postgres+Timescale em `/home/abi/finanalytics/data/{postgres,timescale}/` (ext4 nativo, performance 10-50x vs `/mnt/e/` NTFS+9P). Backups originais em `/mnt/e/finanalytics_data/docker/{postgres,timescale}/` ficam intocados até ~08/mai antes de delete. Dockerd ouve TCP 127.0.0.1:2375; `docker context wsl-engine` ativo no PowerShell. Docker Desktop **autostart desativado em 01/mai** (HKCU Run key removida) — abrir manualmente quando precisar do fallback `default` context.

## Hardware

| Componente | Configuração |
|---|---|
| CPU | Intel i9-14900K (24 cores / 32 threads, 6 GHz) |
| RAM | 196 GB |
| **GPU 0** | **NVIDIA RTX 4090** 24 GB — bus PCIe `01:00.0` — **HEADLESS, dedicada a compute** |
| **GPU 1** | NVIDIA RTX 4090 24 GB — bus PCIe `08:00.0` — monitor principal Windows |
| Driver | NVIDIA 591.86, CUDA 13.1, compute cap 8.9 (Ada Lovelace) |
| Storage | E:\ 2 TB NVMe (bind mounts dos containers) |
| PSU | **Corsair HX1500i** — 1500W, 80+ Platinum, ATX 3.1, fully modular, 2× 12V-2×6 nativos |

**Validação do mapeamento** (após cabos físicos remanejados):
```bash
docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 \
  nvidia-smi --query-gpu=index,pci.bus_id,name --format=csv
# Esperado:
# 0, 00000000:01:00.0, NVIDIA GeForce RTX 4090
# 1, 00000000:08:00.0, NVIDIA GeForce RTX 4090
```

**Peculiaridade Docker Desktop Windows**: `--gpus '"device=0"'` e `NVIDIA_VISIBLE_DEVICES=0` **não filtram** o que `nvidia-smi` enxerga. O isolamento real para apps CUDA (Decisão 15) vem de `CUDA_VISIBLE_DEVICES=0` no env, configurado em api/worker/scheduler/event_worker_v2.

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
├── docker\                           # Configs versionadas
│   ├── prometheus\prometheus.yml
│   └── grafana\
│       ├── provisioning\             # Auto-import datasources + dashboards + alerting
│       └── dashboards\               # Painéis JSON versionados
├── scripts\                          # Backfill, calibração ML, snapshots, scrapers
├── .env
├── docker-compose.yml
└── pyproject.toml
```

## Serviços

| Serviço | Porta | Onde roda |
|---------|-------|-----------|
| FastAPI (finanalytics_api) | :8000 | Docker container (Engine WSL2) |
| profit_agent | :8002 | Windows host (NSSM service `FinAnalyticsAgent`) — bind `0.0.0.0:8002` desde 01/mai (env `PROFIT_AGENT_BIND` p/ override) |
| TimescaleDB | :5433 | Docker container (Engine WSL2) |
| Redis | :6379 | Docker container (Engine WSL2) |
| dockerd (WSL2) | :2375 (loopback) | WSL Ubuntu-22.04 systemd service |

**Network bridges relevantes**:
- WSL2 gateway (= IP do Windows host visto de dentro do WSL): `172.17.80.1` (estável dentro de uma sessão; verificar com `wsl -d Ubuntu-22.04 -- ip route show default` após `wsl --shutdown` ou reboot Windows)
- `docker-compose.wsl.yml` mapeia `host.docker.internal:172.17.80.1` direto (o `:host-gateway` do Engine WSL2 puro resolve pra docker bridge interna `172.18.0.1`, não pro Windows host)
- Firewall Windows tem regra inbound TCP 8002 da subnet `172.17.80.0/20` (`Get-NetFirewallRule | Where-Object DisplayName -eq "Profit Agent WSL Inbound"`)

## Credenciais de Dev (.env)
```
PROFIT_DLL_PATH=C:\Nelogica\profitdll.dll
PROFIT_ACTIVATION_KEY=1834404599450006070
PROFIT_USERNAME=marceloabisquarisi@gmail.com
PROFIT_TIMESCALE_DSN=postgresql://finanalytics:timescale_secret@localhost:5433/market_data
PROFIT_AGENT_URL=http://host.docker.internal:8002
PROFIT_SIM_BROKER_ID=15011
PROFIT_SIM_ACCOUNT_ID=216541264267275
PROFIT_SIM_ROUTING_PASSWORD=o)u$$EVq4SU$$[MdZN
```

## Comandos Frequentes

### Iniciar o profit_agent (Windows)
Roda como serviço NSSM `FinAnalyticsAgent`. Para restart preferir `/agent/restart` via API (sudo `admin123`) — funciona end-to-end em ~9s desde fix NSSM `AppExit=Restart` (sessão 30/abr); fallback `Restart-Service FinAnalyticsAgent` (admin). Manual standalone só pra debug:
```powershell
cd D:\Projetos\finanalytics_ai_fresh
.venv\Scripts\python.exe src\finanalytics_ai\workers\profit_agent.py
```

### Subir/parar a stack completa (Engine WSL2)
Sempre passar os 3 compose files (main + override + wsl). Volumes Postgres+Timescale agora em ext4 nativo (`/home/abi/finanalytics/data/`), demais volumes ainda em `/mnt/e/finanalytics_data/`. **Importante**: rodar `compose` de dentro do WSL bash — PowerShell direto resolve paths como Windows-absolute e quebra (gotcha #6 sessão 01/mai):

```bash
# Up (dentro do WSL Ubuntu-22.04)
cd /mnt/d/Projetos/finanalytics_ai_fresh
DATA_DIR_HOST=/mnt/e/finanalytics_data \
  docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.wsl.yml up -d

# Down
docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.wsl.yml down
```

```powershell
# Comandos diretos (docker ps/exec/logs) funcionam no PS via context wsl-engine
docker ps
docker logs finanalytics_api --tail 50

# Reativar Docker Desktop fallback (autostart desativado desde 01/mai):
& "C:\Program Files\Docker\Docker\Docker Desktop.exe"
docker context use default
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
GET  /ticks/{ticker}             → últimos N trades do ticker
GET  /summary                    → snapshot OHLC diário via DailyCallback
GET  /book/{ticker}              → depth book
GET  /orders                    → lista ordens do banco
POST /order/send                → envia ordem (limit/market/stop)
POST /order/cancel              → cancela por local_order_id
POST /order/cancel_all          → cancela todas
POST /order/change              → altera preço/qty (SendChangeOrderV2)
POST /order/oco                 → OCO: TP (limit) + SL (stop-limit) vinculados
POST /order/zero_position       → zera posição (SendZeroPositionV2)
POST /order/flatten_ticker      → cancel pending + zero_position
GET  /oco/status/{tp_id}        → status par OCO
GET  /positions                 → posição via banco
GET  /positions/dll             → EnumerateAllOrders (DLL) + reconcilia banco
GET  /positions/assets          → EnumerateAllPositionAssets (DLL)
GET  /position/{ticker}         → GetPositionV2 (DLL) ?type=1 (DayTrade) | 2 (Swing)
GET  /orders/reconcile          → reconcilia ordens DB vs DLL
POST /collect_history           → coleta histórico de ticks
GET  /metrics                   → Prometheus exposition
POST /agent/restart             → restart via NSSM (requer sudo_token)
```

Todos acessíveis via `/api/v1/agent/...` no proxy FastAPI :8000 (resolve bloqueio Kaspersky).

### Handshake C5 (trading-engine ↔ profit_agent) — 30/abr
`/order/send` aceita 2 campos opcionais no body usados pelo `finanalyticsai-trading-engine` (R-06):
- `_source: "trading_engine"` → persiste em `profit_orders.source`. Usado por `_maybe_dispatch_diary` para suprimir o hook do diário (engine mantém journal próprio em `trading_engine_orders.trade_journal`; sem supressão = duplicata na unified VIEW).
- `_client_order_id: "<chave_deterministica>"` → persiste em `profit_orders.cl_ord_id` (callback DLL preserva via `WHERE cl_ord_id IS NULL`). Resposta ecoa `cl_ord_id` para o engine fechar reconcile sem segunda tabela de mapping.

Spec viva: `c5_handoff_for_finanalyticsai.md`. Schema migration: `init_timescale/002_profit_agent_schema.sql` + `alembic/versions/ts_0003_profit_orders_source.py`. Implementação Passo 7 + Passo 1 em commit `fdd81f9`. Passos 2-6 (VIEW + UI pill) bloqueados pela migration do engine; agente agendado p/ 21/mai abre PR pareado.

## profit_agent.py — Arquitetura

### Classes principais
- `ProfitAgent` — wrapper da DLL + HTTP server (BaseHTTPRequestHandler)
- `TConnectorAccountIdentifier`, `TConnectorAssetIdentifier` — structs ctypes
- `TConnectorOrder`, `TConnectorTradingAccountPosition` — structs de ordens/posições

### Métodos críticos
```python
agent._send_order_legacy(params)   # envia ordem via SendOrder DLL
agent.send_oco_order(params)       # OCO manual (TP + SL)
agent.get_oco_status(tp_id, env)   # status par OCO
agent._oco_monitor_loop()          # thread daemon 500ms auto-cancela
agent.get_positions_dll(env)       # EnumerateAllOrders — assinatura CORRETA:
                                   # POINTER(TConnectorAccountIdentifier), c_ubyte, c_long, callback
agent.enumerate_position_assets()  # EnumerateAllPositionAssets
agent.get_position_v2(ticker, ...) # GetPositionV2 — ok=False é NORMAL, dados na struct
agent.cancel_order(params)         # SendCancelOrderV2
agent.change_order(params)         # SendChangeOrderV2
agent._hard_exit()                 # kernel32.TerminateProcess (mata DLL ConnectorThread)
agent._kill_zombie_agents(...)     # netstat scan + taskkill no boot
```

### Bugs conhecidos / gotchas da DLL
- `GetPositionV2` retorna `ok=False` mas dados estão corretos na struct — não tratar como erro
- `EnumerateAllOrders`: primeiro param DEVE ser `POINTER(TConnectorAccountIdentifier)`, não `c_int, c_wchar_p`
- Callbacks DEVEM ser armazenados em `self._gc_*` para evitar garbage collection
- DLL é 64-bit — Python deve ser 64-bit
- Callbacks rodam na ConnectorThread — não chamar funções DLL dentro de callbacks
- `open_side=200` na posição = zerada (valor byte residual da DLL)
- **Broker subconnection blips** (P1): broker rejeita SendOrder/SendChangeOrder com `code=3 status=8 msg=Cliente não está logado`. Mitigado via auto-retry em `trading_msg_cb` (max 3 attempts, 5s delay, fallback `msg_id→local_id`).
- **TConnectorOrder callback layout** (P4 fix): callback declarado como `TConnectorOrderIdentifier` 24B (Delphi passa 24B, não os 152B do TConnectorOrder). Status/ticker/qty completos vêm via `EnumerateAllOrders` reconcile, não via callback.
- `r.OrderID.LocalOrderID` em `trading_msg_cb` vem 0 em alguns codes. Use fallback `_msg_id_to_local` mapping populado em `_send_order_legacy`.
- `os._exit(0)` não termina processo limpo — DLL ConnectorThread C++ bloqueia. Sempre usar `_hard_exit()` (`kernel32.TerminateProcess`).

### Order Types / Side / Status
- Type: `1` = Market, `2` = Limit, `4` = StopLimit
- Side: `1` = Buy, `2` = Sell
- Status: `0` = New, `1` = PartialFilled, `2` = Filled, `4` = Canceled, `8` = Rejected, `10` = PendingNew

### Validity / Time In Force
- DDL: `profit_orders.validity_type VARCHAR(8) DEFAULT 'GTC'` + `validity_date TIMESTAMPTZ`
- DLL ProfitDLL não expõe ValidityType no SendOrder — enforcement é local via `gtd_enforcer_loop` no scheduler (60s, cancela GTD expirada via `/order/cancel`; fallback `status=8 + error='gtd_expired_cancel_failed'`)

### Resilience patterns para broker degradado (29/abr)

> Operam em condições de simulator/broker real degradado: callback de status final falha, ordens stuck, sessão broker piscando.

- **`_get_last_price(ticker)`** — cache `_last_prices` com fallback `profit_ticks` (last 5min). Trail engaging funciona mesmo com cache vazio pós-restart NSSM ou callback inativo. Resolve alias futuros automaticamente.
- **`_watch_pending_orders_loop`** (mitigação P9) — após `_send_order_legacy`, registra `local_id` em `self._pending_orders`. Loop @5s polla DLL via `EnumerateAllOrders`, detecta status final em ~10s (vs 5min do reconcile). Marca `status=8 error='watch_orphan_no_dll_record'` se DLL não enumera + DB pendente após 60s.
- **`_persist_trail_hw_if_moved`** — `trail_high_water` persistido no DB a cada movimento favorável (não só em `active`). Sobrevive restart; `_load_oco_state_from_db` recarrega valor corrente.
- **`trail.tick` instrumentation** — log heartbeat 1/15s por group: `last/hw/sl`. `trail.no_price` 1/30s quando feed dead.
- **P7 fallback cooldown 30s** — se `cancel_order` ou `_send_order_legacy` falham no fallback do trail, marca `lv["_trail_fallback_cooldown_until"]`; suprime tentativas seguintes por 30s. Anti log spam + load no broker degradado.
- **`_kill_zombie_agents` conservativo** — apenas detecta + log, **não mata**. Em ambiente com 2+ NSSM services brigando por :8002, o kill agressivo causava loop infinito. Port bind decide; ops desabilita NSSM duplicado.
- **`_load_oco_legacy_pairs_from_db`** (P10 fix) — strategy_id encoded `oco_legacy_pair_<tp_id>_sl` permite reload `_oco_pairs` no boot. Pares OCO legacy sobrevivem restart.
- **`_resolve_active_contract` ubíquo** — `get_position_v2` + `flatten_ticker` + `_send_order_legacy` + `_subscribe` aceitam alias `WDOFUT/WINFUT` e resolvem para contrato vigente (`WDOK26/WINM26`) com `exchange="F"`. Aplicação não precisa saber qual o contrato corrente.

## UI compartilhada

> **Documentação completa**: `src/finanalytics_ai/interfaces/api/static/STATIC_HELPERS.md` — tabela dos ~25 helpers, ordem de carregamento, exemplos de uso.

**Patterns vinculantes**:
- **Soft-delete** (portfolios é referência): `is_active` em vez de DELETE; `has_active_holdings()` valida saldo zero; promove novo default
- **Auditoria** (portfolio_name_history é referência): tabela dedicada `<entidade>_<campo>_history` com `(old, new, changed_at, changed_by)`
- **Helper pattern**: IIFE expondo `window.FAXxx`; `ensureStyles()` auto-injeta CSS na primeira chamada; idempotente; defensivo
- **Bulk distribution**: novos `<script>` tags adicionados via Python ancorando em script existente conhecido (ex: `sidebar.js`)

**Topbar (esq → dir)**: logo · email/avatar · `PT/EN` · `🌙/☀️` · `Sair`

**Rotas FastAPI específicas**:
- `/static/{filename}` — whitelist `.js/.css/.svg/.png/.ico/.json` + `_ALLOWED_PARTIALS={sidebar.html}`; cache 1h (svg 1d)
- `/sw.js` — root scope; `Service-Worker-Allowed: /`; `Cache-Control: no-store`
- `/manifest.json` — root scope; cache 1d

## Dashboard (dashboard.html)
SPA em vanilla JS, 3500+ linhas. Painel DayTrade no lado direito:
- **Aba Ordem**: compra/venda limit/market/stop → `/api/v1/agent/order/send`
- **Aba OCO**: TP+SL com polling automático → `/api/v1/agent/order/oco`
- **Aba Pos.**: GetPositionV2 + lista ativos abertos + botão flatten_ticker (zerar+cancel)
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
- `ohlc_1m` — bars 1m (hypertable; `source` ∈ {brapi, external_1m, nelogica_1m, tick_agg_v1})
- `ohlc_resampled` — N-min bars (hypertable, PK `(time, ticker, interval_minutes)`)
- `profit_daily_bars` — barras diárias OHLCV
- `fintz_cotacoes_ts` — OHLCV diário Fintz (1.32M rows, 200+ tickers, 2010→2025-12-30; **read-only**, freezada)
- `profit_orders` — ordens enviadas via DLL (inclui `validity_type`/`validity_date`)
- `profit_history_tickers` — tickers configurados para backfill (active=True/False)
- `trading_accounts` — contas DayTrade (broker_id+account_id+routing_password)
- `ticker_ml_config` — calibração ML por ticker (118 rows: th_buy/th_sell/best_sharpe/horizon_days/asset_class)
- `signal_history` — snapshots diários de signals (hypertable)
- `fii_fundamentals` — DY TTM/PVP/div_12m/valor_mercado (27 FIIs, refresh 7h BRT)
- `crypto_signals_history` — snapshots BTC/ETH/SOL/etc (refresh 9h BRT)
- `copom_documents` / `copom_sentiment` — pipeline BERTimbau COPOM (vazio até BCB recuperar)
- **Robô** (Alembic `ts_0004`, sessão 01/mai):
  - `robot_strategies` — registry de strategies (config JSONB + account_id + enabled)
  - `robot_signals_log` — auditoria de toda decisão do worker (envio ou skip)
  - `robot_orders_intent` — espelho compacto de ordens originadas pelo robô (separa de `profit_orders` manual; liga via `local_order_id`)
  - `robot_risk_state` — estado diário de risco + kill switch (`paused`)

### PostgreSQL (finanalytics) — multi-tenant
Hierarquia `User → InvestmentAccount → Portfolio → Investment`:
- `users` — RBAC `role ∈ {USER, MASTER, ADMIN}`; MASTER vê contas de outros
- `investment_accounts` — campos obrigatórios: `titular`, `cpf`, `apelido`, `institution_code/name`, `agency`, `account_number`. UNIQUE `(user_id, cpf) WHERE cpf NOT NULL`
- `portfolios` — FK `user_id` + `investment_account_id`; `is_default` flag; **cardinalidade 1:1 com conta** (refactor 25/abr)
- `trades` / `positions` / `crypto_holdings` / `rf_holdings` / `other_assets` — `portfolio_id NOT NULL`, `ON DELETE RESTRICT`
- `trade_journal` — Diário de Trade (qualitativa+quantitativa). Inclui `trade_objective` ∈ {daytrade,swing,buy_hold} (Alembic 0019), `is_complete` BOOL + `external_order_id` UNIQUE (Alembic 0020). Hook `_maybe_dispatch_diary` no profit_agent cria entry pré-preenchida em FILLED.
- `backtest_results` — histórico de runs grid_search/walk_forward (Alembic 0021). UNIQUE `config_hash` (SHA256) para UPSERT idempotente. Colunas escalares (sharpe, drawdown, deflated_sharpe, prob_real) + JSONB completo p/ drilldown.
- `email_research` — research bulletins parseados pelo classifier E1.1 (Alembic 0022, sessão 01/mai). `(msg_id UNIQUE, ticker, sentiment, target_price, source, received_at, raw_excerpt)`. Anthropic SDK + Haiku 4.5 com prompt caching.
- `cointegrated_pairs` — pairs Engle-Granger screening (Alembic 0023, sessão 01/mai). `(asset_a, asset_b, beta_hedge, alpha, half_life, p_value, lookback_days, screened_at)`. Job `cointegration_screen_job` 06:30 BRT diário popula.
- `robot_pair_positions` — posições abertas do dispatcher de pairs (Alembic 0024, sessão 01/mai). Liga `(pair_key, leg_a_local_id, leg_b_local_id, entry_zscore, target_zscore, status)` p/ rastrear naked-leg recovery.

### Candle fallback chain (`candle_repository.py`)
1. `profit_daily_bars` — pré-agregado, 8 tickers DLL + 39 FIIs/ETFs Yahoo
2. `ohlc_1m` — bars 1m, agrega on-the-fly p/ daily
3. `market_history_trades` — agrega ticks on-the-fly
4. `profit_ticks` — ticks real-time
5. `fintz_cotacoes_ts` — stocks only (exclui futuros)

## Pendências Técnicas

**Ativas**:
- Aguardando arquivo Nelogica 1m → rodar `runbook_import_dados_historicos.md`. Inclui treinar pickles h3/h5 para `predict_ensemble` multi-horizon real (hoje só h21 existe).
- **Survivorship bias** (R5 último item aberto): coletar lista de tickers delistados B3 (CVM/B3 scraping) + tabela `b3_delisted_tickers (ticker, delisting_date, reason)` + integração no candle fallback chain.
- **Smoke live robô R1.5+R2+R3** Segunda 04/mai 11h BRT — routine `trig_013JvZLcbANEuRf8rSYiFhK5` agendada. Pré-req: seed `robot_strategies` com config_json + `AUTO_TRADER_ENABLED=true` + `AUTO_TRADER_DRY_RUN=false`.
- **C5 Passos 2-6** (VIEW unified + UI pill manual/engine) bloqueados pela migration do trading-engine R-06; agente `trig_01VDzH3xriAC777KZku42SbK` p/ 21/mai abre PR pareado.
- **E1.2 Gmail OAuth integration** (defer próxima sessão): conectar classifier E1.1 ao MCP `claude_ai_Gmail` p/ polling automático.

**Done (sessão 01/mai)**:
- ✅ R1.1-R1.5 + R2 + R3.1 + R3.2.A + R3.2.B.1-3 + R3.3 — robô de trade completo (TSMOM ∩ ML overlay + pairs cointegrados B3)
- ✅ E1.1 Gmail classifier (Anthropic SDK + Haiku 4.5 + prompt caching) — pronto offline, falta conectar OAuth (E1.2)
- ✅ I1 Fases A+B.1+B.2 — Engine WSL2 + volumes ext4 nativo
- ✅ C1 contratos + producer Kafka `market_data.ticks.v1` (Avro) — base de event-driven
- ✅ R5 follow-up — `/history` endpoint + DSR walk-forward + slippage ADV-aware (commits 30/abr noite)
- ✅ Perf `/api/v1/ml/signals` 30s+ → 2.5s via `_load_latest_features_bulk` (DISTINCT ON)

**Roadmap futuro** (documentado em `Melhorias.md`):
- **R4** ORB WINFUT + filtro DI1 — defer ~7-10d
- **E2-E3** Leitura de Gmail: notas corretagem reconciliation (E2) | pipeline genérico (E3)

**Histórico de sprints concluídas**: ver `git log` + `memory/project_*.md` (sprints 15-27 movidas para fora do CLAUDE.md). Bugs de produção catalogados em `Melhorias.md`:
- P1-P7 + O1 ✅ DONE 28/abr
- P9 (DB stuck status=10) ✅ MITIGADO 29/abr via `_watch_pending_orders_loop` (detection ~10s vs 5min reconcile) + EXTENSÃO 30/abr via `_load_pending_orders_from_db` (cobre restart NSSM, validado live: 10 órfãs marcadas <1s)
- P10 (OCO legacy pares perdidos pós-restart) ✅ DONE 29/abr via `_load_oco_legacy_pairs_from_db`
- P11 + P11.2 (futuros UI exchange/alias) ✅ DONE 29/abr via `_resolve_active_contract` em `get_position_v2` + `flatten_ticker`
- P2-futuros (DB não reflete status=8) ✅ DONE 30/abr via fallback `_msg_id_to_local` em `trading_msg_cb` (commit `07c2445`)
- P8 (broker rejeita futuros) ✅ FECHADO 30/abr — era transient broker degradação 29/abr, não bug
- I4 (`/agent/restart` não restartava) ✅ FECHADO 30/abr — causa real foi `nssm AppExit=Exit` em vez de `Restart`. Diagnóstico expandido (`hard_exit.attempt` + `last_error`) provou que `TerminateProcess` sempre funcionou. Fix: `& nssm set FinAnalyticsAgent AppExit Default Restart`. Ciclo completo agora 9s automático.

**Sessão 29/abr UI overhaul** (commits `3896aeb` → `90acb2e`):
- Gap compression overnight/weekend no chart (`_compressGaps` + `_timeRealMap` + `_realToCompressed`); `fitContent()` mostra todos os bars
- Backend `/marketdata/candles/{ticker}` faz `UNION ohlc_1m + ohlc_1m_from_ticks` + resolve aliases futuros (`WDOFUT → WDOK26 + WDOM26`)
- `_doRefresh` SSE comprime timestamps com `_compressIncomingTime`
- Bollinger Bands calculadas **client-side** sobre `_bars2` (era backend `/indicators` daily, não alinhava com candles 5m)
- 4 indicadores novos: Estocástico Lento (14·3·3), ATR (Wilder), VWAP intraday overlay, IFR (label dual RSI/IFR)
- `/static/sw_kill.html` reset de SW + caches via UI
- Carteira: coluna Horário (`created_at` HH:MM:SS), linha branca tracejada zero no chart Rentabilidade

**Operacional 29/abr**:
- `profit_subscribed_tickers` semeada com **373 tickers** (366 equities IBOV/B3 + 7 futuros: WDO/WIN/DOL/IND/BGI/OZM/CCM)
- `tick_to_ohlc_backfill_job` diário 21h BRT (00h UTC): **DELETE + INSERT** do dia inteiro (substitui rows incoerentes pelo continuous aggregate)

**Sessão 30/abr OHLC + bugs hardening + drag UI** (14 commits `5ad447d` → `a7b52aa`):
- **OHLC limpo**: `ohlc_1m_from_ticks` recriado com `WHERE EXTRACT(hour FROM time) BETWEEN 13 AND 20` (UTC) — exclui heartbeats overnight + leilão pre-abertura + after-market que poluíam chart com OHLC estático. Refill 7M+ ticks. Validado: 0 bars 12/21 UTC pós-recreate.
- **Endpoint admin OHLC rebuild**: `POST /api/v1/admin/ohlc/rebuild` (require_master) + UI aba "🛠️ Sistema" em `/admin` com form date+ticker → DELETE+INSERT do dia. Endpoint reutilizável quando aparecer ruído P9-like no futuro.
- **`tick_to_ohlc_backfill_job` 2 bugs**: (1) env `TICK_TO_OHLC_BACKFILL_HOUR` interpretado como UTC mas `_next_run_utc` esperava local BRT → renomeado pra `TICK_TO_OHLC_BACKFILL_HOUR_BRT=21`. (2) `target_date=now(UTC).date()` rodando 03 UTC processava dia errado → trocado por `now(UTC) - 12h` que cai sempre dentro do dia BRT correto.
- **CI verde** (após meses vermelho): ruff format 37 arquivos + 28 fixes auto + 1 manual + skipif Windows nos `test_profit_agent_fixes` + market_data_client tests alinhados com Decisão 20.
- **`profit_agent_validators.py` novo módulo puro**: `validate_attach_oco_params` + `trail_should_immediate_trigger` extraídos pra unit test em CI Linux (sem ctypes WINFUNCTYPE Windows-only). 20 unit tests cobertura.
- **Drag-to-modify TP/SL** (U1 ressuscitado via abordagem A): SVG overlay `#order-handles-svg` absolute por cima do canvas — handles 70x14 verde/vermelho na borda direita. Mouse events vêm direto pra nós sem competir com canvas listener interno do lightweight-charts. Validado live (Playwright MCP): drag TP 49.20→47.50 + drag SL 47.50→48.24 ambos mandando `change_order` ao DLL.
- **Day-dividers chart** (`#day-dividers-svg` z-index 5, atrás dos handles z-10): linha vertical tracejada `rgba(180,200,230,.45)` + label DD/MM no topo em cada virada de dia UTC. Re-renderiza em pan/zoom. SW v100→v101 bumped pra invalidar cache do dashboard.html.
- **`stop_price` reconcile fix**: enum_orders agora lê `o.StopPrice` da DLL + UPDATE adiciona `stop_price=CASE` (antes só `price`). Bug encontrado validando drag SL.
- **NSSM `AppExit=Restart`**: ciclo completo `/agent/restart` em 9s automático — antes precisava PS elevado manual pq `AppExit=Exit` deixava service Stopped após `TerminateProcess`.
- Master é solo dev confirmado (só Marcelo nos últimos 14 dias) → reformat massivo + bumps versão sem disrupção.

**Sessão 30/abr pós-pregão estendida** (8 commits `fdd81f9` → `0a40bf0`):
- **C5 handshake `_source` + `_client_order_id`** (`fdd81f9`): `_send_order_legacy` aceita campos no body de `:8002/order/send`; persiste em `profit_orders.source`/`cl_ord_id` (Alembic `ts_0003`); `_maybe_dispatch_diary` early-returns + log `diary.suppressed_engine_origin` quando `source='trading_engine'`. Resposta ecoa `cl_ord_id` p/ engine fechar reconcile sem 2ª tabela. Spec: `c5_handoff_for_finanalyticsai.md`. Smoke validado live PETR4 simulation (cl_ord_id=`smoke_c5:PETR4:...`). Passos 2-6 (VIEW unified + UI pill manual/engine) bloqueados pela migration do trading-engine R-06; agente agendado `trig_01VDzH3xriAC777KZku42SbK` p/ 21/mai abre PR pareado.
- **Documentação `diario_de_trade.md`** (`88b18f2`): inventário completo do módulo (schema 30+ colunas, endpoints REST, hook DLL, UI 6 abas, heatmap mensal Stormer, workflow incomplete→complete, sino topbar, 28 tests). 13 seções.
- **I3 rebuild containers** (`992d06d`): `api worker event_worker_v2 scheduler ohlc_ingestor` — bug bonus `ohlc_ingestor` em loop `Restarting(255)` há tempo indeterminado por image pré-27/abr sem migrations 0019-0020. Rebuild resolveu. **I2 housekeeping**: 1848 logs legacy `profit_agent-2026XXXXX.log` (65.7MB) zipados em `_archive_logs/` (6.44MB ratio 10x).
- **R5 backtest harness** (`df73263`, `5a938bf`, `0a40bf0`):
  - `domain/backtesting/slippage.py` — futuros 2 ticks/lado (WDO=0.5, WIN=5.0, IND/DOL/DI/CCM/BGI/OZM); ações 0.05%/lado. `apply_slippage_model=True` default em `run_backtest`.
  - `domain/backtesting/metrics.py` — Deflated Sharpe Ratio (LdP 2014 + Bailey 2014). SR_0 = sigma×f(N), com f(N) = (1-γ)Φ⁻¹(1-1/N) + γΦ⁻¹(1-1/Ne). Probit Beasley-Springer-Moro sem scipy.
  - `OptimizationResult.deflated_sharpe` traz `{deflated_sharpe, prob_real, e_max_sharpe}` sobre best candidate.
  - `infrastructure/database/repositories/backtest_repo.py` + Alembic `0021_backtest_results` — UPSERT idempotente por SHA256 do config completo.
  - `scripts/backtest_demo_dsr.py` (CLI demo + flag `--persist`). Validado live: PETR4 RSI 30 trials → DSR z=0.31 prob=62% (sinal fraco); VALE3 MACD 48 trials → DSR z=-0.52 prob=30% (overfitting provável — SR observado ABAIXO de E[max|H0]).
  - 49 unit tests novos (slippage 13 + DSR 18 + repo 17 + 1 fix). 199+ regressão verde.
- **R5 follow-up fechado 30/abr noite** (`3c60baa` + `978482e`): endpoint `/api/v1/backtest/history` (GET list/filter, GET/{hash} drilldown, DELETE) consumido pela UI `backtest.html:2456-2535`; auto-persist em `OptimizerService` + `WalkForwardService`; DSR walk-forward por fold OOS + agregado (`WalkForwardResult.deflated_sharpe` + `WalkForwardFold.oos_dsr`) com `num_is_trials` como N e `len(oos_bars)-1` como T; slippage ADV-aware sqrt-impact capado em 5x. 92 unit tests R5 verdes. **Único item R5 ainda aberto**: survivorship bias (precisa coleta de delistados B3).

**Sessão 01/mai full day (58 commits, ~11.5h)** — feriado Trabalho. Histórico cronológico via `git log --since=2026-05-01`. Pontos vinculantes:
- **Robô de Trade R1.1→R3.3 completo** — `auto_trader_worker.py` (asyncio loop, kill switch, dry_run env), `domain/robot/{risk,strategies}.py` (Risk Engine vol-target Kelly 0.25x + ATR Wilder + max_positions + circuit_breaker DD<-2%), `MLSignalsStrategy` (consome `/api/v1/ml/signals` + cache 60s), `TsmomMlOverlayStrategy` (concordance momentum 252d on-the-fly + ML signal — divergem → SKIP), pairs trading completo (Engle-Granger screening offline + decision logic + service layer + worker integration + dual-leg dispatcher + position persistence + naked_leg→Pushover critical). UI `/robot` (read-only + kill switch) + `/pairs` (z-score real-time + drilldown history) + entries no sidebar. Service `auto_trader` em `docker-compose.override.yml` (`AUTO_TRADER_ENABLED=false` default).
- **E1.1 Gmail classifier** (`9fc4da9`) — Anthropic SDK + Haiku 4.5 + prompt caching; tabela `email_research` semeada por classify offline; OAuth integration (E1.2) defer.
- **C1 producer Kafka** (`ef31d26`) — `profit_agent` publica `market_data.ticks.v1` (Avro) em Kafka. Base de event-driven async pra futura ingest pipeline.
- **I1 Fase B.2** (`ffcd06c`) — volumes Postgres+Timescale migrados pra `/home/abi/finanalytics/data/{postgres,timescale}/` (ext4 nativo). Backups originais em `/mnt/e/finanalytics_data/docker/{postgres,timescale}/` ficam até ~08/mai antes de delete (rollback fácil = trocar paths). Runbook completo `docs/runbook_wsl2_engine_setup.md` (Fase A+B.1+B.2+troubleshooting).
- **P2-futuros** (`1af8279`) — `compute_trading_result_match` em `profit_agent_validators.py` adiciona match por `message_id` quando `local_id`+`cl_ord_id` chegam zerados (broker rejeita futuros instantâneos com struct corrompida).
- **Perf `/api/v1/ml/signals`** (`dfccc57`) — 30s+ → 2.5s via `_load_latest_features_bulk` (DISTINCT ON em vez de N queries serializadas).
- **Refactor**: `MLSignalsStrategy._fetch_bars` delega pro `HttpCandleFetcher.fetch_bars` (extract `infrastructure/adapters/http_candle_fetcher.py`, commit `a565667` + `e53d676`). `auto_trader_dispatcher` chama proxy `:8000` (não `:8002` direto) p/ usar `AccountService` injection; handshake C5 `_source='auto_trader'` + `cl_ord_id='robot:<sid>:<tkr>:<act>:<min_iso>'` determinístico p/ idempotência; OCO automático quando TP+SL fornecidos.
- **Trade-engine UI** (`9cb7dfb`) — página read-only `/trade-engine` monitorando o `finanalyticsai-trading-engine` externo.
- **Scheduler**: novo job `cointegration_screen_job` 06:30 BRT diário (`1c6dce7`); validado live `next_utc=2026-05-02T09:30:00Z`.
- **Endpoints novos** (`prefix /api/v1/`):
  - `/robot/{status,strategies,signals_log,pause,resume}` — read-only + kill switch
  - `/pairs/{active,zscores,zscores/{pair_key}/history,positions}` — pairs trading state
  - `/ml/signals` agora retorna em 2.5s (era 30s+)
- **Gotchas WSL2 importantes** (memorial completo em `memory/project_session_01mai_full.md`): (a) `host-gateway` NÃO resolve pra Windows host em Engine WSL2 puro (resolve pra docker bridge interna), use `172.17.80.1` direto; (b) WSL gateway IP estável dentro da sessão WSL mas pode mudar após `wsl --shutdown` ou reboot Windows; (c) `docker compose` rodando do PowerShell direto resolve paths como Windows-absolute e quebra — sempre rodar de dentro do WSL bash; (d) Alembic tem 2 heads (Postgres `0xxx` + Timescale `ts_0xxx`), `alembic upgrade head` falha — usar revision específica; (e) `bind 0.0.0.0:8002` no profit_agent é necessário pra Engine WSL2 alcançar via WSL gateway.
- **PAIRS_DSN bug** (`5e2afc0`) — worker passava DSN do Timescale pro `PsycopgPairsRepository` quando deveria usar Postgres. Detectado durante pré-validação do auto_trader. Novo env `PAIRS_DSN` (fallback `DATABASE_URL_SYNC` → `DATABASE_URL` → default Postgres) porque `cointegrated_pairs` está em Postgres principal (Alembic 0023) enquanto `robot_strategies/signals_log/orders_intent` estão em Timescale (`ts_0004`).

## Decisões Arquiteturais (Imutáveis)

> Não revogar sem evidência empírica nova. Detalhamento histórico de cada decisão (origem, justificativa, aplicação) em `git log` dos commits que as introduziram.

### Decisão 15 — Dual-GPU: separação estrita
Origem: incidentes de reboot ao usar 2 GPUs em compute simultâneo (transientes de potência → OCP da PSU).

**Regras vinculantes:**
1. Compute ML executa **exclusivamente na GPU 0** (bus `01:00.0`, headless).
2. GPU 1 reservada ao Windows/desktop. **Nunca** recebe workload de compute em produção.
3. Service Docker que precisa de GPU declara `deploy.resources.reservations.devices` com `device_ids: ["0"]` + `capabilities: [gpu, utility, compute]`. `CUDA_VISIBLE_DEVICES: "0"` por redundância.
4. **Proibido**: paralelismo puro multi-GPU (Modo 3 — DDP, `device_map="auto"`, DataParallel) com a PSU atual.
5. **Modo 2 autorizado**: workloads ML *distintos* por GPU APENAS para jobs offline com `nvidia-smi -pl 320` ativo em ambas. Nunca em horário de pregão.
6. Se cabos físicos forem remanejados, validar mapeamento via comando da seção Hardware antes de subir container com compute.
7. Para liberar Modo 3: PSU ≥1.600W ATX 3.0/3.1 Titanium com 2 cabos 12V-2×6 nativos, OU colocation. PSU atual (Corsair HX1500i 1500W Platinum) NÃO atende.

### Decisão 16 — Helper-driven UI

**Regras vinculantes:**
1. Toda página HTML privada deve carregar pelo menos: `auth_guard.js`, `sidebar.js`, `theme.css`, `theme_toggle.js`, `i18n.js`, `error_handler.js`, `toast.js`.
2. Novo asset compartilhado segue pattern IIFE expondo `window.FAXxx`, com `ensureStyles()` auto-injetado e idempotente. Ver `STATIC_HELPERS.md`.
3. **Distribuição em massa**: tocar N páginas → script Python idempotente em `scripts/refactor_*.py`. Edição manual em >5 páginas sinaliza que falta script.
4. **Anchor pattern**: novos `<script>` tags via `replace(ANCHOR, ANCHOR + '\n  ' + TAG)` em scripts já existentes (estável: `sidebar.js`, `auth_guard.js`, `error_handler.js`).
5. Não substituir `confirm()`/`alert()` nativos por implementações próprias página a página — usar `FAModal.confirm` / `FAToast.*`.
6. `data-fa-table` no `<table>` é o padrão para sort/filter automático (FATable auto-init).

### Decisão 17 — FOUC prevention para light theme
Snippet inline no `<head>` ANTES do `<link rel="stylesheet" href="/static/theme.css">` em todas as páginas:
```html
<script>(function(){try{var t=localStorage.getItem('fa_theme');
  if(t==='light'||t==='dark')document.documentElement.dataset.theme=t;}catch(e){}})();</script>
```

### Decisão 18 — i18n por fall-through (PT default + EN fallback)
`FAI18n.t(key)` resolve `_dict[locale][key]` e cai para `_dict['pt'][key]` se ausente. Chave inexistente em ambos retorna a própria key. PT é canônico; EN é tradução. Não migrar texto in-page de uma vez — usar `data-i18n="key"` em elementos novos.

### Decisão 19 — `:root{...}` per-page é identidade visual intencional
Blocos `:root{...}` em páginas individuais NÃO são duplicatas dos globals de `theme.css`. Várias páginas têm identidade visual própria. **Não migrar** automaticamente para vars globais — quebraria visual identity.

### Decisão 20 — BRAPI é último fallback; DLL Profit + DB são primários
Ordem em `CompositeMarketDataClient.get_ohlc_bars` (`infrastructure/adapters/market_data_client.py`):
1. **DB local** (candle_repository — fallback chain interno acima)
2. **Yahoo Finance**
3. **BRAPI** — último recurso

Ordem em `get_quote` (live): profit_agent `:8002` → Yahoo → BRAPI.

**Regras vinculantes:**
1. **Não chamar `BrapiClient` direto** nos routes. Usar `request.app.state.market_client` (Composite).
2. **Exceção única**: fundamentalistas (P/L, ROE, DY) continuam via BRAPI — DLL não fornece.
3. `MIN_BARS_THRESHOLD = 30` — DB com < 30 bars cai pro Yahoo.
4. `YAHOO_PREFERRED_RANGES = {"10y", "max"}` — ranges longos vão direto pro Yahoo.
5. **Ingestor `ohlc_1m_ingestor` continua usando BRAPI** para alimentar DB. Não viola a Decisão.

### Decisão 21 — `populate_daily_bars` default `1m` (ticks tem bug de escala)
Origem: ticks em `market_history_trades` mostram escala /100 intermitente. `ohlc_1m source=tick_agg_v1` está limpo.

**Regras vinculantes:**
1. `populate_daily_bars.py` default `auto` tenta `ohlc_1m` primeiro, fallback para ticks.
2. **Não usar `--source ticks` em produção** para tickers com `ohlc_1m` disponível.
3. **Exceção**: futuros (`WDOFUT`, `WINFUT`) sem `ohlc_1m` continuam usando ticks.
4. Se voltar a aparecer escala mista, regenerar via `populate_daily_bars.py --ticker $T --source 1m` após `DELETE FROM profit_daily_bars WHERE ticker=$T`. Não tentar "patch in place".

Runbook detalhado: `docs/runbook_profit_daily_bars_scale.md`.

### Decisão 22 — Docker runtime: Engine direto em WSL2 (não Docker Desktop)
Origem: Docker Desktop morre quando user faz logoff Windows; setup precisa rodar 24/7. Engine WSL2 com systemd é independente de sessão.

**Regras vinculantes:**
1. **Runtime canônico**: Docker Engine 29.4.2 dentro de Ubuntu-22.04 WSL2 (`systemctl is-active docker` = active). Volumes Postgres+Timescale em **ext4 nativo** (`/home/abi/finanalytics/data/{postgres,timescale}/`, 10-50x perf vs NTFS+9P, Fase B.2 done 01/mai). Demais volumes (`prometheus`, `grafana`, `pgadmin`, etc.) ainda em `/mnt/e/finanalytics_data/` — não foram migrados pq não são caminho crítico de IO.
2. **PowerShell**: `docker context use wsl-engine` apontando pra `tcp://127.0.0.1:2375`. **Docker Desktop autostart desativado em 01/mai** — abrir manualmente quando precisar do `default` context.
3. **profit_agent bind 0.0.0.0:8002** desde 01/mai (era 127.0.0.1) — Engine WSL2 puro precisa pra alcançar via WSL gateway. Override via env `PROFIT_AGENT_BIND` se quiser restringir.
4. **`docker-compose.wsl.yml` é OBRIGATÓRIO** ao subir a stack — converte paths NTFS `E:/` pra `/mnt/e/`, mapeia `host.docker.internal:172.17.80.1` (não `:host-gateway` — esse resolve pra docker bridge interna em Engine WSL2 puro).
5. **Firewall Windows** tem regra `Profit Agent WSL Inbound` permitindo TCP 8002 da subnet `172.17.80.0/20`. Se WSL gateway IP mudar (após `wsl --shutdown` ou reboot), atualizar regra **e** o `docker-compose.wsl.yml`.
6. **Smoke test após qualquer mudança de stack**:
   ```powershell
   docker context show  # wsl-engine
   docker ps  # 17 containers
   curl http://localhost:8000/api/v1/agent/health  # {"ok":true}
   ```
7. **Imagens stale**: rebuilds via `docker compose build api worker` (~5min com cache). NÃO usar `--no-cache` casual — pode falhar transient em pip install torch+prophet (2GB re-download).

Histórico: I1 Fase A done 01/mai (commit `ab0ea8b`). Fase B.1 cutover live 01/mai (commit `950ac35`). Fase B.2 done 01/mai (commit `ffcd06c`) — volumes Postgres+Timescale em ext4 nativo. Runbook completo: `docs/runbook_wsl2_engine_setup.md`.

## Observabilidade

**Grafana** :3000 (admin/admin) — provisionado via `docker/grafana/provisioning/`:
- Datasources: Prometheus :9090
- Dashboards JSON em `docker/grafana/dashboards/` (data_quality, profit_agent_health)
- **15 alert rules** em `provisioning/alerting/rules.yml` (recarregam via `docker restart finanalytics_grafana`)

**Roteamento** (`policies.yml`): `severity=critical` → `pushover-critical` (priority=1+siren); demais → `pushover-default` (priority=0). Credenciais: 4 env vars no `.env` (`PUSHOVER_USER_KEY`, `PUSHOVER_APP_TOKEN`, `GRAFANA_PUSHOVER_USER_KEY`, `GRAFANA_PUSHOVER_APP_TOKEN`).

**Endpoints `/api/v1/ml/*`**:
- `/signals` — batch de 118 tickers calibrados (filtrar por `?asset_class=fii|etf`)
- `/predict_mvp/{ticker}` — single horizon (h21 default)
- `/predict_ensemble/{ticker}` — multi-horizon agregado por sharpe
- `/signal_history` + `/changes` — auditoria histórica
- `/metrics` — saúde do pipeline (drift, snapshot age, signals 24h)

**Scheduler jobs** (`scheduler_worker.py`):
- 06:00 BRT — `macro_job` (SELIC, IPCA, FX, IBOV, VIX)
- 07:00 BRT — `fii_fund` (Status Invest → `fii_fundamentals`, skip weekend)
- 07:00 BRT — `ohlcv_job` + `brapi_sync_job` (delta diário, idempotente)
- 08:00 BRT — `yahoo_bars` (39 FIIs+ETFs → `profit_daily_bars`, skip weekend)
- 09:00 BRT — `crypto_signals` (snapshot → `crypto_signals_history`, sem skip)
- 09:00 BRT no dia 5 do mês — `cvm_informe` (sync `inf_diario_fi_AAAAMM.zip` da CVM)
- 23:00 BRT — `cleanup_event_records_job` + `cleanup_stale_pending_orders_job`
- A cada 5min em 10h-18h BRT — `reconcile_loop` (DLL ↔ DB)
- A cada 60s — `gtd_enforcer_loop` (cancela ordens GTD expiradas)

**Métricas profit_agent** (Prometheus em `:8002/metrics`):
- `profit_agent_order_callbacks_total` — counter (DLL viva)
- `profit_agent_last_order_callback_age_seconds` — gauge
- `profit_agent_oco_groups_active` — gauge
- `profit_agent_oco_trail_adjusts_total` / `profit_agent_oco_trail_fallbacks_total` — counters
- Scheduler: `:9102/metrics` — `scheduler_job_runs_total{job,status}` + `scheduler_reconcile_errors_total`

## Convenções do Projeto

- **Logging**: `structlog` no FastAPI, `logging` padrão no profit_agent
- **Async**: FastAPI usa `asyncio`; profit_agent usa threads (DLL é síncrona)
- **Deploy**: `docker compose build api && docker compose up -d api` (rebuild completo; `docker cp` apenas para hotfix rápido)
- **Sem frameworks pesados**: sem Django, sem ORM pesado
- **Injeção de dependência manual**: sem FastAPI `Depends` em excesso
- **Tipagem**: type hints em todo código novo

## Git
- Remote: https://github.com/MarceloAbiSquarisi/finanalytics_ai
- Branch padrão: `master`
- Histórico de sprints e commits: usar `git log` (não duplicar aqui)
