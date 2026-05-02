# FinAnalytics AI — Contexto para Claude Code

## Visão Geral
Sistema de análise financeira com DayTrade via ProfitDLL (Nelogica).
Stack: FastAPI :8000 (Docker) + profit_agent :8002 (Windows host) + TimescaleDB :5433 + Redis.

**Runtime Docker (desde 01/mai/2026)**: **Docker Engine direto em WSL2 Ubuntu-22.04** (não Docker Desktop). Volumes Postgres+Timescale em ext4 nativo (`/home/abi/finanalytics/data/`). Dockerd em TCP `127.0.0.1:2375`; `docker context wsl-engine` ativo no PS. Docker Desktop autostart OFF (abrir manualmente p/ fallback `default`). Backups antigos em `/mnt/e/finanalytics_data/docker/{postgres,timescale}/` até ~08/mai. Detalhe: Decisão 22.

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

**Validação do mapeamento GPU** (após remanejar cabos): `docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi --query-gpu=index,pci.bus_id,name --format=csv` — esperado GPU 0 em `01:00.0`.

**Isolamento CUDA**: `--gpus device=0` e `NVIDIA_VISIBLE_DEVICES` **não filtram** `nvidia-smi`. Use `CUDA_VISIBLE_DEVICES=0` no env (configurado em api/worker/scheduler/event_worker_v2). Ver Decisão 15.

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

### Backfill / status banco
```powershell
.venv\Scripts\python.exe scripts\backfill_history.py --start 2026-01-02 --end 2026-04-11 --delay 2
docker exec finanalytics_timescale psql -U finanalytics -d market_data -c "SELECT ticker, COUNT(DISTINCT trade_date::date) AS dias, MAX(trade_date::date) AS fim FROM market_history_trades GROUP BY ticker ORDER BY ticker;"
```

### Testes rápidos
```powershell
Invoke-RestMethod "http://localhost:8000/api/v1/agent/health"
Invoke-RestMethod -Method POST "http://localhost:8002/order/send" -ContentType "application/json" -Body '{"env":"simulation","order_type":"market","order_side":"buy","ticker":"PETR4","exchange":"B","quantity":100,"price":-1,"is_daytrade":true}'
```
OCO + posição: ver bodies de exemplo em `docs/runbook_profit_agent.md`.

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

### Handshake C5 (trading-engine ↔ profit_agent)
`/order/send` aceita 2 campos opcionais no body p/ origens externas (engine/auto_trader):
- `_source` → persiste em `profit_orders.source`; suprime hook do diário se `'trading_engine'` (engine mantém journal próprio).
- `_client_order_id` → persiste em `cl_ord_id`; resposta ecoa para reconcile sem 2ª tabela.

Spec: `c5_handoff_for_finanalyticsai.md`. Migration: `alembic/versions/ts_0003_profit_orders_source.py`.

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

### Resilience patterns para broker degradado

> Operam em condições de simulator/broker real degradado: callback final falha, ordens stuck, sessão piscando.

- **`_get_last_price`** — cache `_last_prices` + fallback `profit_ticks` (last 5min). Trail funciona pós-restart NSSM/callback inativo. Resolve alias futuros.
- **`_watch_pending_orders_loop`** (P9) — registra `local_id` em `_pending_orders`; loop @5s polla DLL `EnumerateAllOrders`, detecta status final em ~10s (vs 5min reconcile). Marca `status=8 error='watch_orphan_no_dll_record'` se DLL+DB divergem após 60s.
- **`_persist_trail_hw_if_moved`** — `trail_high_water` persistido a cada movimento favorável; sobrevive restart via `_load_oco_state_from_db`.
- **P7 cooldown 30s** — falha em `cancel_order`/`_send_order_legacy` no fallback do trail → suprime tentativas por 30s (anti log spam).
- **`_kill_zombie_agents` conservativo** — só detecta + log, não mata. Port bind decide; ops desabilita NSSM duplicado.
- **`_load_oco_legacy_pairs_from_db`** (P10) — strategy_id `oco_legacy_pair_<tp_id>_sl` permite reload `_oco_pairs` no boot.
- **`_resolve_active_contract`** ubíquo — `get_position_v2`/`flatten_ticker`/`_send_order_legacy`/`_subscribe` aceitam alias `WDOFUT/WINFUT` → resolvem contrato vigente (`WDOK26/WINM26`) com `exchange="F"`.

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
- **Smoke live robô R1.5+R2+R3** Segunda 04/mai 11h BRT — routine `trig_013JvZLcbANEuRf8rSYiFhK5` agendada. Pré-req: seed `robot_strategies` com config_json + `AUTO_TRADER_ENABLED=true` + `AUTO_TRADER_DRY_RUN=false`.
- **C5 Passos 2-6** (VIEW unified + UI pill manual/engine) bloqueados pela migration do trading-engine R-06; agente `trig_01VDzH3xriAC777KZku42SbK` p/ 21/mai abre PR pareado.
- **E1 fetcher concreto** — classifier `ResearchClassifier` + worker scaffold prontos; aguardando definição da fonte de dados p/ implementar `ResearchFetcher`.

**Done recentes (28/abr → 02/mai)** — detalhe em `docs/historico/sessoes_29abr_01mai.md` + `docs/runbook_survivorship_bias.md`:
- ✅ R5 **survivorship bias fechado** (02/mai) — tabela `b3_delisted_tickers` populada (1863 CVM placeholders + 449 FINTZ tickers reais), `DelistedTickerRepo` + `DelistingInfo`, engine `run_backtest` aceita `delisting_date`/`last_known_price` (force-close + truncamento), `BacktestService` aceita `delisting_resolver` opcional, demo `--respect-delisting`. 19 tests novos, 1488 verdes total. Caminho via Fintz delta (884 tickers histórico Fintz cruzados com `profit_subscribed_tickers`) substituiu plano original PDF IBOV — mais barato, cobertura superior.
- ✅ Robô R1.1→R3.3 (TSMOM ∩ ML overlay + pairs cointegrados B3) — 01/mai
- ✅ E1.1 Research classifier (Anthropic SDK + Haiku 4.5 + prompt caching) — source-agnostic, fetcher pendente — 01/mai
- ✅ I1 Fases A+B.1+B.2 — Engine WSL2 + volumes ext4 nativo — 01/mai
- ✅ C1 producer Kafka `market_data.ticks.v1` (Avro) — 01/mai
- ✅ R5 backtest harness (slippage + DSR + walk-forward + history endpoint) — 30/abr
- ✅ C5 handshake `_source` + `_client_order_id` (Passo 7) — 30/abr
- ✅ Limpeza profunda: profit_agent 7058→4426l, app.py 1427→681l, ruff 227→0 — 01/mai noite
- ✅ Bugs fechados: P1-P11 + P2-futuros + I4 (NSSM AppExit=Restart) — ver histórico

**Roadmap futuro** (em `Melhorias.md`):
- **R4** ORB WINFUT + filtro DI1 — defer ~7-10d
- **E2-E3** Pipeline de research/notas (notas corretagem reconciliation E2 | pipeline genérico E3) — aguardando fonte de dados

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
