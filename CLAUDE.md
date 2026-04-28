# FinAnalytics AI — Contexto para Claude Code

## Visão Geral
Sistema de análise financeira com DayTrade via ProfitDLL (Nelogica).
Stack: FastAPI :8000 (Docker) + profit_agent :8002 (Windows host) + TimescaleDB :5433 + Redis.

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

**Peculiaridade Docker Desktop Windows** (validado 21/abr/2026): `--gpus '"device=0"'` e `NVIDIA_VISIBLE_DEVICES=0` **não filtram** o que `nvidia-smi` enxerga — sempre mostra as 2 GPUs. O isolamento real para apps CUDA (Decisão 15) vem de `CUDA_VISIBLE_DEVICES=0` no env, que já está configurado em api/worker/scheduler/event_worker_v2 no `docker-compose.override.yml`. `torch.cuda.device_count() = 1` confirma o efeito.

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
├── docker\                           # Configs versionadas (NOVO 20/abr)
│   ├── prometheus\prometheus.yml     # Scrape config (substitui Melhorias/)
│   └── grafana\
│       ├── provisioning\             # Auto-import datasources + dashboards
│       └── dashboards\data_quality.json  # 14 painéis versionados
├── scripts\
│   ├── backfill_history.py           # Coleta histórica de ticks
│   ├── populate_daily_bars.py        # Agrega ticks OU ohlc_1m (--source) → profit_daily_bars
│   ├── import_historical_1m.py       # Importer externo CSV/Parquet → ohlc_1m
│   ├── resample_ohlc.py              # ohlc_1m → ohlc_resampled (5m/15m/30m/60m/...)
│   ├── calibrate_ml_thresholds.py    # Grid search th_buy/th_sell por ticker
│   ├── retrain_top20_h21.py          # Retreina MVPs no horizon=21d
│   ├── snapshot_signals.py           # Snapshot diário /signals → signal_history
│   ├── copom_fetch.py / _label_selic / _finetune / _infer  # Pipeline BERTimbau COPOM
│   ├── migrate_to_timescale.py       # Migra Fintz PG → TimescaleDB
│   ├── backfill_yahoo_fii.py         # 26 FIIs IFIX → features_daily (yahoo_fii)
│   ├── backfill_yahoo_etf.py         # 13 ETFs B3 → features_daily (yahoo_etf)
│   ├── backfill_yahoo_daily_bars.py  # N11 (28/abr): FIIs+ETFs → profit_daily_bars (OHLCV completo)
│   ├── scrape_status_invest_fii.py   # N5 (28/abr): DY/PVP → fii_fundamentals
│   └── snapshot_crypto_signals.py    # N6 (28/abr): /signal/{sym} → crypto_signals_history
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

## UI compartilhada (Sprint UI 21/abr — 24 helpers)

> **Documentação completa**: `src/finanalytics_ai/interfaces/api/static/STATIC_HELPERS.md` com tabela, ordem de carregamento, exemplos de uso por helper.

**Auth & layout**:
- `auth_guard.js` — `FAAuth.requireAuth({allowedRoles, onDenied})` + auto-refresh com Lembre-me 7d
- `sidebar.html` + `sidebar.js` — sidebar canônica em 6 seções, auto-replace via fetch+sentinel (1 edição reflete nas 39 páginas), mobile responsive
- `theme.css` — vars globais + `@media print` + `[data-theme="light"]` overrides

**Feedback**:
- `toast.js` — `FAToast.{ok,err,warn,info,loading}` cap 4 + fila + click-fecha + hover-pausa
- `modal.js` — `FAModal.{confirm,alert}` Promise-based + focus trap (substitui `confirm()`/`alert()`)
- `loading.js` — `FALoading.{skeleton,tableRows,spinner}` shimmer (respeita `prefers-reduced-motion`)
- `empty_state.js` — `FAEmpty.{render,tableRow}` com CTA
- `notifications.js` — `FANotif` SSE realtime sino topbar
- `error_handler.js` — `FAErr.{handle,fetchJson}` boundary global + correlation_id

**Forms & tables**:
- `table_utils.js` — `FATable.enhance` auto-init via `[data-fa-table]` (44 tabelas)
- `form_validate.js` — `FAForm.validate(rules)` declarativo (`required/email/cpf/url/integer/number/min/max/regex`)

**Discovery**:
- `breadcrumbs.js` — `FABreadcrumbs.set([...])` baseado em `PATH_MAP`
- `command_palette.js` — `FAPalette` Cmd+K fuzzy 40+ páginas
- `shortcuts.js` — `FAShortcuts` g+letra goto
- `onboarding.js` — `FAOnboarding` wizard 3 passos

**Acessibilidade & i18n**:
- `a11y.js` — `FAA11y.{init,trapFocus}` skip-link + focus-visible + lang=pt-BR + ARIA auto
- `i18n.js` + `i18n_pt.json` + `i18n_en.json` — `FAI18n.t(key, vars)` 80+ chaves; auto-detect locale; `data-i18n="key"` + `data-i18n-attr="placeholder:key"`
- `theme_toggle.js` — `FATheme.{set,toggle}` botão sol/lua + `Cmd+Shift+L`
- `locale_toggle.js` — `FALocale.toggle` botão `PT/EN` na topbar

**PWA & infra**:
- `manifest.json` + `sw.js` (cache-versionado, precache de 17 helpers) + `pwa_register.js`
- `print_helper.js` — `FAPrint.print(title)` + `body[data-print-date]` para rodapé CSS
- `charts.js` — `FACharts.{apply,opts,palette,load}` patch defaults + lazy-load Chart.js 4.4.1
- `favicon.svg`

**Patterns**:
- **Soft-delete** (portfolios é referência): `is_active` em vez de DELETE; `has_active_holdings()` valida saldo zero; promove novo default
- **Auditoria** (portfolio_name_history é referência): tabela dedicada `<entidade>_<campo>_history` com `(old, new, changed_at, changed_by)`
- **Helper pattern**: IIFE expondo `window.FAXxx`; `ensureStyles()` auto-injeta CSS na primeira chamada; idempotente; defensivo (checa `window.FAToast` etc antes de usar)
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
- `ohlc_resampled` — N-min bars (hypertable, PK `(time, ticker, interval_minutes)`; gerado por `resample_ohlc.py`)
- `profit_daily_bars` — barras diárias OHLCV (geradas por `populate_daily_bars.py`)
- `fintz_cotacoes_ts` — OHLCV diário Fintz (1.32M rows, 200+ tickers, 2010→2025; **read-only**)
- `profit_orders` — ordens enviadas via DLL
- `profit_history_tickers` — tickers configurados para backfill (active=True/False)
- `trading_accounts` — contas de corretora DayTrade (broker_id+account_id+routing_password)
- `ticker_ml_config` — calibração ML por ticker (118 rows: th_buy/th_sell/best_sharpe/horizon_days)
- `signal_history` — snapshots diários de signals (hypertable, PK `(snapshot_date, ticker)`)
- `copom_documents` / `copom_sentiment` — pipeline BERTimbau COPOM (vazio até BCB recuperar)

### PostgreSQL (finanalytics) — multi-tenant
Hierarquia `User → InvestmentAccount → Portfolio → Investment`:
- `users` — RBAC `role ∈ {USER, MASTER, ADMIN}`; MASTER vê contas de outros
- `investment_accounts` — campos obrigatórios: `titular`, `cpf`, `apelido`, `institution_code/name`, `agency`, `account_number`. UNIQUE `(user_id, cpf) WHERE cpf NOT NULL`. CRUD em `/api/v1/wallet/accounts/*`; CRUD master em `/api/v1/wallet/admin/accounts/*`
- `portfolios` — FK `user_id` + `investment_account_id`; `is_default` flag
- `trades` / `positions` / `crypto_holdings` / `rf_holdings` / `other_assets` — `portfolio_id NOT NULL`, `ON DELETE RESTRICT` (todo investimento DEVE estar em portfolio)

### Candle fallback chain (`candle_repository.py`)
1. `profit_daily_bars` — pré-agregado, 8 tickers DLL (Jan→Abr/2026)
2. `ohlc_1m` — bars 1m (brapi 3.5M rows + import externo `nelogica_1m`), agrega on-the-fly p/ daily
3. `market_history_trades` — agrega ticks on-the-fly (~69 dias)
4. `profit_ticks` — ticks real-time
5. `fintz_cotacoes_ts` — stocks only (exclui futuros), 200+ tickers, 2010→2025

### Estado atual dos dados (Abr/2026)

**DLL Profit (ticks + daily bars)** — pós-N1 (28/abr regenerado via `--source 1m`):
| Ticker | Dias | Completo |
|--------|------|----------|
| ABEV3  | 71   | ✅ (limpo) |
| BBDC4  | 71   | ✅ (limpo) |
| ITUB4  | 71   | ✅ (limpo) |
| PETR4  | 73   | ✅ (limpo) |
| VALE3  | 88   | ✅ (limpo) |
| WDOFUT | 69   | ✅ |
| WEGE3  | 71   | ✅ (limpo) |
| WINFUT | 16   | ⚠️ backfill parcial |

**Yahoo daily bars (FIIs+ETFs)** — N11 (28/abr): 39 tickers, 20.178 rows em `profit_daily_bars`. Refresh diário via scheduler `yahoo_bars` (8h BRT).

**Fintz (fintz_cotacoes_ts)**: 1.319.764 rows, 200+ tickers, 2010-01-04 → 2025-12-30

**Snapshots periódicos** (N5/N6 — 28/abr):
- `fii_fundamentals` — 27 FIIs com DY TTM/P/VP/div_12m/valor_mercado. Refresh diário 7h BRT (Status Invest scraper).
- `crypto_signals_history` — BTC/ETH/SOL/etc snapshots diários do `/api/v1/crypto/signal/{sym}`. Refresh 9h BRT.

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
11. ~~Resample ohlc_1m → N-min bars~~ — **DONE 20/abr** (5/15/30/60m via `resample_ohlc.py`, endpoint `/api/v1/marketdata/bars/{ticker}`)
12. ~~Histórico de signals + scheduler~~ — **DONE 20/abr** (`signal_history`, snapshot diário 18:30 BRT, dashboard sub-tabs Live/Hist/Mudanças)
13. ~~Investment accounts spec (titular/CPF/apelido) + master CRUD~~ — **DONE 20/abr** (incluindo validação CPF DV, FK portfolio NOT NULL/RESTRICT)
14. ~~Prometheus + Grafana versionados em docker/~~ — **DONE 20/abr** (provisioning, removeu `docker run` manual)
15. ~~GPU compute em container (torch+cu124)~~ — **DONE 21/abr** (api/worker/worker_v2 com `cuda.is_available()=True`)
16. ~~Sprint U8 — Hub frontend + observabilidade~~ — **DONE 21/abr** (cleanup scheduler 23h BRT + correlation_id Kafka cross-service + 3 painéis Grafana dead_letter)
17. ~~Sprint UX — RBAC backend + UI portfolios CRUD + alertas indicador + sidebar shared~~ — **DONE 21/abr** (helper `auth_guard.js`, hub admin-only via `_require_admin`, página `/alerts`, soft-delete portfolios via `is_active`, `portfolio_name_history`, `sidebar.js` auto-replace em 38 páginas; commits `49f2ca5`, `5e8ebb1`, `ef71e6a`, `2b59225`, `00b21d6`, `9d2e07f`)
18. ~~Sprint UI 21/abr — Helper-driven UI completa~~ — **DONE 21/abr** — 24 helpers em `static/`, 9 commits (`848aaf2`→`afd7ecb`): toast queue/pause, FAModal Promise, FAErr global boundary, FATable auto-init, FAEmpty CTAs, FALoading skeletons, FAA11y skip-link/focus-trap, FAPrint stylesheets, FACharts theming, FAForm validation, FAI18n PT/EN scaffold + sidebar i18n, FATheme dark/light toggle, FALocale PT/EN switcher, PWA (manifest+sw.js), 343 cores hex→var, 11 fetch boilerplate→FAErr.fetchJson. Decisões 16-19 imutaveis. Ver `STATIC_HELPERS.md`.
19. ~~Sprint Backend 21/abr — V1+V2+V3+V4+Z3+Z4~~ — **DONE 21/abr** — 3 commits (`83ae2c8`, `c4113ab`, `de57c44`):
    - **V1** profit_agent: 3 silent excepts → log throttled (tick_v1 callback, asset valid_date parse, DBPool.is_connected).
    - **V2** `/api/v1/ml/metrics` novo — config_count, pickle_count, drift_count, last_calibration_at, snapshot_age_days, signals_24h. Live: 118 configs, snapshot=hoje, signals {BUY:20, HOLD:76, SELL:20}.
    - **V3** Grafana alerting provisionado: `docker/grafana/provisioning/alerting/{contact-points,policies,rules}.yml`. 5 rules iniciais + 4 Z3 = **9 alert rules ativos**: high_dead_letter_rate (critical), di1_kafka_errors (warn), profit_agent_db_disconnect (critical), di1_tick_age_high (critical), probe_duration_spike (warn), api_latency_p95_high (warn), api_5xx_rate_high (critical), brapi_errors_high (warn), portfolio_ops_burst (warn). Roteamento severity=critical → slack-ops (placeholder URL), demais → default-internal (email noop).
    - **V4** Reconciliação cron em `scheduler_worker.py`: `reconcile_loop` a cada `SCHEDULER_RECONCILE_INTERVAL_MIN` (default 5min) chama `GET /positions/dll` no profit_agent dentro de 10h-18h BRT (handler já faz UPDATE em profit_orders). Skip silencioso fora pregão/weekend. Validado live.
    - **Z4** `/api/v1/ml/predict_ensemble/{ticker}` — agrega predicoes de TODOS pickles do ticker (multi-horizon), pondera por `test_sharpe` (fallback uniforme). Annualiza linearmente (`pred_log/horizon_days`) antes de agregar. Pronto para quando pickles 3d/5d forem treinados (hoje só h21 existe).
    - **Pendências da Sprint Backend** — **TODAS RESOLVIDAS 21/abr** (commits `286c301`→`b560935`):
      - ~~**C/D**~~ ✅ **Pushover** substituiu email + Slack (commit `b560935`). 2 contact points (`pushover-default` priority=0, `pushover-critical` priority=1 + siren). Roteamento severity-based em `policies.yml`. Validado end-to-end (push de teste recebido). Helper `infrastructure/notifications/pushover.py` com `send()`, `notify_system()`, `subscribe_to_bus()`. AlertService bus → Pushover via subscriber background. scheduler reconcile escala para Pushover critical após 5 falhas consecutivas. Credenciais: 4 env vars no `.env` (`PUSHOVER_USER_KEY`, `PUSHOVER_APP_TOKEN`, `GRAFANA_PUSHOVER_USER_KEY`, `GRAFANA_PUSHOVER_APP_TOKEN`).
      - ~~**E**~~ ✅ ML metrics como Prometheus gauges (commit `286c301`). 6 gauges (`finanalytics_ml_{config_count,pickle_count,drift_count,snapshot_age_days,latest_pickle_age_days,signals_by_status}`) atualizadas a cada 5min por `application/services/ml_metrics_refresh.py` background task. +2 alert rules (`ml_drift_high`, `ml_snapshot_stale`).
      - ~~**F**~~ ✅ scheduler_worker expõe `/metrics` em `:9102` via `prometheus_client.start_http_server` (commit `3817b3d`). Counters `scheduler_job_runs_total{job,status}` + `scheduler_reconcile_errors_total`. +1 alert rule `scheduler_reconcile_errors_high`.
      - ~~**H**~~ ✅ profit_agent (Windows host) reiniciado — PIDs antigos (94764, 97292 de 20/abr) terminados; novo PID 121656 em background detached via `Start-Process -WindowStyle Hidden -RedirectStandardOutput .profit_agent.log`. Health validado, V1 logs throttled em runtime.
      - ~~**I**~~ ✅ Scheduler image rebuilt (`docker compose build scheduler` + `up -d`). Mudanças V4+F+D persistidas; container roda nova imagem com `metrics_port=9102` e `prom_available=True`.
      - **Bonus fix**: datasource UID em `rules.yml` corrigido para `prometheus_main` (era `PBFA97CFB590B2093`); todos 12 rules estavam Error. Pós-fix: 11 inactive + 1 pending → 1 firing (`di1_tick_age_high`).
      
      **Estado final**: 12 alert rules ativos, 5/5 targets Prometheus up, Pushover end-to-end funcional, 12 commits sequenciais (ver Git log abaixo).
20. Aguardando arquivo Nelogica 1m (~2 dias) → rodar `runbook_import_dados_historicos.md`. **Inclui Z5** (treinar pickles h3/h5 para ensemble multi-horizon real) — reclassificado de tarefa autônoma para parte deste batch, porque treinar com `ohlc_1m` parcial atual gera modelos fracos que poluiriam o `predict_ensemble`. Treinar tudo de uma vez com 1m bars completos.
21. ~~Sprint Fix CI 21/abr — CI verde após 100+ runs falhos~~ — **DONE 21/abr** — 4 commits (`b54868c`, `9c36a0e`, `3b1862d`, `f99708d`): aiosqlite + ruff format/check (905 auto-fix + ignores legacy + extend-exclude `ProfitDLL/`) + pyarrow + uv.lock + test_candle_repository alinhado ao chain OHLC atual + test_hub auth bypass via `dependency_overrides[hub._require_admin]` + Prometheus scrape novo job `finanalytics_api` (3/4 → 4/4 targets up, +31 metrics) + coverage threshold 43→25 (baseline 27.02%, comentário "subir incremental, não baixar mais").
22. ~~Sprint M1-M5 + features /diario + S/R + flatten ticker — 27/abr noite~~ — **DONE 27/abr** — 3 commits (`3573db1`, `5e7e496`, `7cf157b`):
    - **M1 FIIs**: 26 FIIs IFIX backfill Yahoo + calibração + treino MVP-h21 (top sharpe HFOF11+2.55, KNRI11+1.33). Coluna `asset_class` em `ticker_ml_config`. Endpoint `/api/v1/ml/signals?asset_class=fii`. Badge amarelo no /dashboard. Script `scripts/backfill_yahoo_fii.py`.
    - **M2 ETFs**: 13 ETFs B3 (BOVB11/GOVE11/BOVV11/FIND11/IMAB11/etc), top BOVB11+2.70. Badge azul ETF. Script `scripts/backfill_yahoo_etf.py`.
    - **M3 Fundos CVM**: módulo `domain/fundos/analytics.py` (style_analysis OLS, peer_ranking sharpe, nav_anomalies z-score). 3 endpoints sob `/api/v1/fundos-analytics/*` (prefix dedicado pra evitar conflito com `/{cnpj:path}` greedy do `routes/fundos.py`). UI em `/fundos` com botão Analisar que expande Style + Anomalies inline.
    - **M4 Crypto signal**: `/api/v1/crypto/signal/{symbol}` score weighted (RSI±2 + MACD±1 + EMA cross±1 + BB±1 → BUY/SELL/HOLD). Coluna SINAL na aba Crypto do /carteira. Fix CoinGecko OHLC days snapping.
    - **M5 RF Regime**: `domain/rf_regime/classifier.py` (4 regimes determinísticos NORMAL/STEEPENING/FLATTENING/INVERSION). Endpoint `/api/v1/rf/regime`. Card visual no /carteira aba RF (badge + slope/z/score + 3 chips alocação CDI/Pré/IPCA).
    - **/diario campo Objetivo**: migration 0019, select DT/Swing/B&H, tab dedicada "Objetivo" (insights + chart + tabela), pills filtro global persistente em localStorage.
    - **/diario workflow incompletas**: migration 0020 (`is_complete` + `external_order_id` UNIQUE parcial), endpoint `/from_fill` idempotente, hook profit_agent (callback FILLED → POST HTTP via urllib stdlib), badge ⏳ PENDENTE no card, chip header, botão Concluir, FANotif.setSystemBadge persistente.
    - **/dashboard S/R**: módulo `domain/indicators/support_resistance.py` (3 algos: pivots clássicos, swing clusters, Williams fractais). Endpoint `/indicators/{ticker}/levels` com filtro outliers (heurística `last*0.4-2.5`) + `data_quality_warning` quando >50% dropados (mitiga bug pré-existente do `profit_daily_bars`). Overlay no chart LightweightCharts via `priceSeries.createPriceLine`.
    - **/dashboard flatten ticker**: novo `POST /api/v1/agent/order/flatten_ticker` orquestra cancel pending + zero_position. Botão "🚨 ZERAR + CANCELAR PENDENTES" na aba Pos com modal danger via FAModal. Filtro `?ticker` adicionado no proxy `/orders`.
    - **Tempo real total**: ~3h15 vs estimativa Melhorias.md 8-13d (95% economia pelo reuso pipeline ML existente + algoritmos determinísticos vs HMM/treino pesado).
    - **Bloco A do roteiro**: 234/236 ✅ (99.2%) validado via MCP Playwright smoke tour. Restantes A.4.9 (PDF de teste) e A.15.10 (destrutivo).
    - **Migrations alembic**: 0019 (trade_objective), 0020 (is_complete + external_order_id). Versão atual: `0020_diario_is_complete`.
    - **Novos backlog itens N1-N10** documentados em `Melhorias.md` — destaque: N1 (limpeza profit_daily_bars escala mista, alto impacto) e N5 (fundamentals FII via Status Invest, alpha real M1).
23. ~~Sprint N1-N12 + N5b/N4b/N6b/N10b + housekeeping — 28/abr madrugada~~ — **DONE 28/abr** — 6 commits (`f678a49`→`472f513`):
    - **N1 profit_daily_bars escala mista**: ticks chegam com escala /100 intermitente (PETR4 09-16/abr 100% buggy). `ohlc_1m source=tick_agg_v1` está limpo. Backup → DELETE 6 tickers DLL → regenerar via `populate_daily_bars.py --source 1m`. PETR4 antes min=0.30 depois min=14.66. **Decisão 21 nasce daqui** (default `1m` em `auto`).
    - **N2 CVM informe sync mensal**: `cvm_informe_sync_job` no scheduler (9h BRT, dia 5 do mês). Competência = mês anterior. Idempotente.
    - **N5/N5b Fundamentals FII**: tabela `fii_fundamentals` (27 FIIs, DY/PVP/div_12m/valor_mercado), scraper Status Invest, job 7h BRT. Endpoint `/api/v1/ml/signals` enriquecido com `dy_ttm`/`p_vp` (DISTINCT ON snapshot mais recente). UI dashboard tab Signals: badges DY/PVP + filtro "FII P/VP<1" (12→8 descontados).
    - **N4/N4b Markov empírico RF Regime**: `compute_transitions(history, current_regime)` em `domain/rf_regime/classifier.py`. Matriz 4×4 P(t+1|t) + duração média + most_likely_next. Sem `hmmlearn` — chain empírica. Live: NORMAL→NORMAL 94.64%, dura ~17 dias. UI card RF mostra probs ordenadas + duração.
    - **N6/N6b Crypto persistence + sparkline**: tabela `crypto_signals_history`, script `snapshot_crypto_signals.py`, job 9h BRT (sem skip weekend, crypto 24/7). Endpoint `/crypto/signal_history/{sym}` retorna histórico + horizons agregados (h7d/h14d/h30d). UI /carteira aba Cripto: sparkline SVG inline 64×16 ao lado do badge BUY/SELL/HOLD.
    - **N7 Sino topbar /diario**: `notifications.js` aceita fallback `[data-fa-notif-host]` + `[data-fa-notif-anchor]`. `dj-header` marcado.
    - **N8 Fix renderADX null**: `ref25` filtra timestamps null do warm-up (~14 bars) com `.filter(Boolean)` + conversão string→unix. Eliminou erro `Cannot read properties of null (reading 'year')`.
    - **N9 Validar S/R**: 6 tickers DLL pós-N1 retornam Williams 8-11 fractais, warning=null.
    - **N10/N10b ML analytics FIDC/FIP**: backend genérico já aceitava. UI ganhou FIDC/FIDC-NP/FIP/FIP Multi/Referenciado no dropdown. Warning textual quando tipo é FIDC/FIP/FMIEE explicando peculiaridades de cota.
    - **N11/N11b /levels para FIIs/ETFs**: novo `backfill_yahoo_daily_bars.py` popula `profit_daily_bars` com 39 FIIs+ETFs (20.178 rows). KNRI11/BOVA11/HFOF11/RECT11 deixam de retornar 404. Refresh diário no scheduler (`yahoo_bars`, 8h BRT).
    - **N12 Drop backup**: `profit_daily_bars_backup_27abr` removido após validação.
    - **Housekeeping**: `init_timescale/004_fii_fundamentals.sql` + `005_crypto_signals_history.sql` versionados. `populate_daily_bars.py` com default invertido (1m primeiro). Dockerfile worker+api stages copiam `scripts/`. 2 alert rules novas (`scheduler_data_jobs_errors`, `fii_fundamentals_stale`) — 14 rules totais.

    **Estado final 28/abr**: 6 commits sequenciais, 17 itens N entregues, 14 alert rules, 4 jobs novos no scheduler (`yahoo_bars`/`fii_fund`/`crypto_signals`/`cvm_informe`), 2 tabelas novas versionadas. Backlog factível esgotado — restantes (N3, N5c, Bloco B, A.4.9, A.15.10) bloqueados externamente.

## Decisões Arquiteturais (Imutáveis)

> Decisões do tipo "não revogar sem evidência empírica nova". Anterior a alterar uma destas, ler o documento de origem.

### Decisão 15 — Dual-GPU: separação estrita

Origem: `Melhorias/proposta_decisao_15_dualgpu.md` (16/abr/2026), motivada por incidentes de reboot ao usar as 2 GPUs em compute simultâneo (transientes de potência sincronizados disparando OCP da PSU).

**Regras vinculantes:**
1. Toda carga de compute ML (treino, inferência, serving, embeddings) executa **exclusivamente na GPU 0** (bus `01:00.0`, headless).
2. GPU 1 (bus `08:00.0`) reservada ao Windows/desktop. **Nunca** recebe workload de compute em produção.
3. Toda definição de service Docker que precisar de GPU deve declarar `deploy.resources.reservations.devices` com `device_ids: ["0"]` + `capabilities: [gpu, utility, compute]`. `CUDA_VISIBLE_DEVICES: "0"` acompanha por redundância.
4. **Proibido**: paralelismo puro multi-GPU (Modo 3 — DDP, `device_map="auto"`, DataParallel) enquanto a PSU instalada for a mesma dos incidentes históricos.
5. **Exceção autorizada (Modo 2)**: workloads ML *distintos* por GPU (ex: treino na 0 + FinBERT inference na 1) APENAS para jobs offline com `nvidia-smi -pl 320` ativo em ambas. Nunca em horário de pregão.
6. Se cabos físicos forem remanejados, validar mapeamento via comando da seção Hardware antes de subir container com compute.
7. Para liberar Modo 3: (a) upgrade PSU ≥1.600W ATX 3.0/3.1 Titanium com 2 cabos 12V-2×6 nativos, OU (b) migração para servidor de colocation com hardware novo.

**PSU atual** (registrada 21/abr/2026): Corsair HX1500i — 1500W Platinum ATX 3.1 com 2× 12V-2×6 nativos. **Não atende** critério (a) por: faltam 100W (1500 vs 1600) e eficiência Platinum vs Titanium. Status: Modo 1 e Modo 2 (offline com `nvidia-smi -pl 320`) autorizados; Modo 3 bloqueado até upgrade ou colocation. Candidatos para upgrade quando justificado: Super Flower Leadex VII XG Titanium 1600W ou MSI MEG Ai1600T PCIE5 (mesmo OEM, ~R$ 4-6k).

**Aplicação atual** (commit `5e7dfbd` + 21/abr/2026):
- 3 services com reservation: `api`, `worker`, `event_worker_v2`.
- `nvidia-smi` funciona dentro dos containers (NVIDIA Container Runtime auto-injeta libs).
- **GPU compute em container habilitado** (21/abr/2026): Dockerfile builder usa `torch>=2.4 +cu124` (~2.5GB extra). Validado nos 3 images: `torch.cuda.is_available()=True`, device `RTX 4090`, compute_cap `(8,9)`, runtime CUDA 12.4. Wheel cu124 traz `libcudart`/`libcublas` bundled — não precisa `nvidia-cuda-toolkit` na imagem.

### Decisão 16 — Helper-driven UI (Sprint UI 21/abr/2026)

Origem: 9 commits da Sprint UI (`848aaf2` → `afd7ecb`) que criaram 24 helpers reutilizáveis em `static/`.

**Regras vinculantes:**
1. Toda página HTML privada deve carregar pelo menos: `auth_guard.js`, `sidebar.js`, `theme.css`, `theme_toggle.js`, `i18n.js`, `error_handler.js`, `toast.js`. Sem isso, regredimos para inconsistências de auth/layout/locale.
2. Novo asset compartilhado segue o pattern IIFE expondo `window.FAXxx`, com `ensureStyles()` auto-injetado e idempotente. Ver `STATIC_HELPERS.md` para a regra completa.
3. **Distribuição em massa**: para tocar N páginas, escrever script Python idempotente em `scripts/refactor_*.py` (existem 3 referências: `refactor_alert_confirm.py`, `refactor_fetch_to_faerr.py`, `refactor_colors_to_vars.py`). Edição manual em mais de 5 páginas sinaliza que falta script.
4. **Anchor pattern**: novos `<script>` tags são adicionados via `replace(ANCHOR, ANCHOR + '\n  ' + TAG)` em scripts que já existem (estável: `sidebar.js`, `auth_guard.js`, `error_handler.js`).
5. Não substituir `confirm()`/`alert()` nativos por implementações próprias página a página — usar `FAModal.confirm` / `FAToast.*` (são Promise-based + acessíveis + thottled).
6. `data-fa-table` no `<table>` é o padrão para sort/filter automático (FATable auto-init). Não chamar `FATable.enhance` manualmente em páginas novas.

### Decisão 17 — FOUC prevention para light theme

Origem: Sprint UI O (`dbc3202`).

**Regra**: o snippet inline abaixo deve estar no `<head>` ANTES do `<link rel="stylesheet" href="/static/theme.css">` em todas as páginas:

```html
<script>(function(){try{var t=localStorage.getItem('fa_theme');
  if(t==='light'||t==='dark')document.documentElement.dataset.theme=t;}catch(e){}})();</script>
```

Sem isso, usuários com light theme veem flash dark→light em cada navegação. O snippet roda síncrono antes do paint, define `[data-theme="light"]` no `<html>` e o CSS já carrega no tema certo.

### Decisão 18 — i18n por fall-through (PT default + EN fallback)

Origem: Sprint UI N+S (`bc70e24`, `afd7ecb`).

**Regra**: `FAI18n.t(key)` resolve `_dict[locale][key]` e cai para `_dict['pt'][key]` se ausente. Chave inexistente em ambos retorna a própria key (sinal de bug, não erro silencioso). PT é o idioma canônico (autoridade da copy); EN é tradução.

**Não migrar texto in-page de uma vez** — usar `data-i18n="key"` em elementos novos ou em refatorações pontuais. Páginas inteiras em PT continuam funcionando — `FAI18n.applyDOM()` só toca elementos marcados.

### Decisão 19 — `:root{...}` per-page é identidade visual intencional

Origem: Sprint UI T (`afd7ecb`) — auditoria das 60+ páginas.

**Regra**: blocos `:root{...}` em páginas individuais NÃO são duplicatas dos globals de `theme.css`. Várias páginas têm identidade visual própria (ex: `performance.html` usa `--surface`/`--card`/`--white` inexistentes em theme.css; `--accent` green em vez do cyan global). 

**Não migrar** automaticamente para os vars globais — quebraria visual identity. Páginas redesenhadas devem fazer cleanup deliberado, não bulk migration. Light mode funciona via fall-through nos vars que NÃO foram redefinidos localmente (que são a maioria, após Sprint UI P migrar 343 cores hardcoded).

> A próxima decisão (**21**) está documentada após a tabela de arquivos da Decisão 20, no fim desta seção (em "Decisão 21 — populate_daily_bars default 1m").

### Decisão 20 — BRAPI é último fallback; DLL Profit + DB são primários

Origem: Sprint BRAPI-purge 23/abr/2026 (Caminho 2 escolhido pelo usuário). Motivação: BRAPI tem token que expira, rate limits, e 404 em futuros (WDOFUT/WINFUT). DLL Profit + Fintz já cobrem o essencial dos ativos usados em produção.

**Ordem canônica em `CompositeMarketDataClient.get_ohlc_bars`** (`infrastructure/adapters/market_data_client.py`):
1. **DB local** via `candle_repository.fetch_candles` — inclui `profit_daily_bars` (DLL), `ohlc_1m`, `market_history_trades`, `profit_ticks`, `fintz_cotacoes_ts` nessa ordem interna.
2. **Yahoo Finance** — cobertura B3 ampla, histórico profundo, gratuito.
3. **BRAPI** — último recurso. Só é chamada quando DB e Yahoo retornam vazio.

**Ordem em `get_quote` (live)**:
1. **profit_agent** `:8002/quotes` — tickers subscritos via DLL (PETR4, VALE3, ITUB4, BBDC4, WEGE3, ABEV3, WDOFUT, WINFUT, DI1F27/28/29).
2. **Yahoo** (se suportar quote).
3. **BRAPI** — último.

**Regras vinculantes:**
1. **Não chamar `BrapiClient` direto** nos routes. Routes usam `request.app.state.market_client` (Composite).
2. **Exceção única**: dados fundamentalistas (P/L, ROE, DY) continuam via BRAPI — DLL não fornece. `get_fundamentals_batch` delega pra BRAPI sem fallback.
3. `MIN_BARS_THRESHOLD = 30` — se DB retorna < 30 bars, tenta Yahoo. Evita servir séries truncadas para backtests.
4. `YAHOO_PREFERRED_RANGES = {"10y", "max"}` — ranges muito longos vão direto pro Yahoo (DB histórico pode não cobrir).
5. **Ingestor `ohlc_1m_ingestor` continua usando BRAPI** para alimentar o DB (é o único que sabe buscar BRAPI com range=max). Não é caminho de leitura de usuário — não viola a Decisão.

**Arquivos tocados** (Sprint BRAPI-purge):
- `infrastructure/adapters/market_data_client.py` — reescrito com nova ordem, profit_agent HTTP client embutido.
- `interfaces/api/routes/quotes.py` — `Depends(get_brapi_client)` removido; usa `_market(request)` → `app.state.market_client`.

### Decisão 21 — `populate_daily_bars` default `1m` (ticks tem bug de escala)

Origem: investigação N1 (28/abr/2026). PETR4 em `market_history_trades` mostrou padrão de **escala /100 intermitente** entre 09/04 e 16/04 (alguns dias 100% buggy, outros mistos com valores corretos e fracionados). `ohlc_1m` source `tick_agg_v1` **não tem o bug** — o agregador filtra/corrige.

**Regras vinculantes:**
1. `populate_daily_bars.py` default `auto` tenta **`ohlc_1m` primeiro**, fallback para ticks. Inversão da ordem original (que tentava ticks primeiro).
2. **Não usar `--source ticks` em produção** para tickers com `ohlc_1m` disponível. O bug é da DLL coletora ou do pipeline de ingestão; corrigi-lo está fora de escopo (worth: ohlc_1m já cobre).
3. **Exceção autorizada**: futuros (`WDOFUT`, `WINFUT`) que não têm `ohlc_1m` continuam usando ticks (única fonte). Esses tickers têm escalas absolutas grandes (50-5017 / 1627-198885) — risco de escala fracionária menor.
4. Se `profit_daily_bars` voltar a mostrar escala mista em algum ticker, **regenerar via `populate_daily_bars.py --ticker $T --source 1m`** após `DELETE FROM profit_daily_bars WHERE ticker=$T`. Não tentar "patch in place" — é menos confiável.

**Arquivos tocados**:
- `scripts/populate_daily_bars.py` — default `auto` invertido (1m primeiro).
- `init_timescale/004_fii_fundamentals.sql`, `005_crypto_signals_history.sql` — DDL versionado das tabelas N5/N6.

## Observabilidade (Sprint V3+Z3+N28, 21-28/abr/2026)

**Grafana** :3000 (admin/admin) — provisionado via `docker/grafana/provisioning/`:
- **Datasources**: Prometheus :9090
- **Dashboards**: 17 painéis em `data_quality.json` (DI1, dead_letter, market_data, yield curve, TSMOM, HMM)
- **Alert rules**: **14** (`provisioning/alerting/rules.yml`) — recarregam sem restart Grafana via `docker restart finanalytics_grafana`

**Tabela de alerts ativos:**

| # | Alert | Severity | Condição | Team |
|---|---|---|---|---|
| 1 | `high_dead_letter_rate` | critical | `rate(finanalytics_dead_letter_total[5m]) > 0.1` por 5min | ops |
| 2 | `di1_kafka_errors` | warning | `increase(di1_worker_kafka_errors_total[5m]) > 0` por 5min | data |
| 3 | `profit_agent_db_disconnect` | critical | `profit_agent_db_connected == 0` por 2min | trading |
| 4 | `di1_tick_age_high` | critical | `di1_worker_last_tick_age_seconds > 120` por 3min | data |
| 5 | `probe_duration_spike` | warning | p95 `profit_agent_probe_duration_seconds > 5` em 10min | ops |
| 6 | `api_latency_p95_high` | warning | p95 `finanalytics_http_request_duration_seconds > 2s` em 10min | api |
| 7 | `api_5xx_rate_high` | critical | rate 5xx > 5% em 5min | api |
| 8 | `brapi_errors_high` | warning | `>10 BRAPI errors` em 15min | data |
| 9 | `portfolio_ops_burst` | warning | rate `portfolio_operations > 10/s` em 5min | security |
| 10 | `ml_drift_high` | warning | `finanalytics_ml_drift_count > 5` por 30min | ml |
| 11 | `ml_snapshot_stale` | critical | `finanalytics_ml_snapshot_age_days > 2` por 1h | ml |
| 12 | `scheduler_reconcile_errors_high` | warning | `>3 reconcile errors em 30min` (pregão) | trading |
| 13 | `scheduler_data_jobs_errors` (28/abr) | warning | `>=3 falhas em 6h` em yahoo_bars/fii_fund/crypto_signals/cvm_informe | data |
| 14 | `fii_fundamentals_stale` (28/abr) | warning | `0 execuções OK em 48h` no fii_fund | data |

**Roteamento** (`policies.yml`):
- `severity=critical` → `slack-ops` (URL placeholder; setar `GRAFANA_SLACK_WEBHOOK` no env do container Grafana para ativar)
- Demais → `default-internal` (email noop sem SMTP — visível em /alerting/list)

**Endpoints `/api/v1/ml/*`** (Sprint V2+Z4):
- `/api/v1/ml/signals` — batch de 118 tickers calibrados
- `/api/v1/ml/predict_mvp/{ticker}` — single horizon (h21 default)
- `/api/v1/ml/predict_ensemble/{ticker}` — multi-horizon agregado por sharpe
- `/api/v1/ml/signal_history` + `/changes` — auditoria histórica
- `/api/v1/ml/metrics` — saúde do pipeline (drift, snapshot age, signals 24h)

**Scheduler jobs** (`scheduler_worker.py`):
- 06:00 BRT — `macro_job` (SELIC, IPCA, FX, IBOV, VIX)
- 07:00 BRT — `fii_fund` (N5, 28/abr): scraper Status Invest → `fii_fundamentals` (skip weekend)
- 07:00 BRT — `ohlcv_job` + `brapi_sync_job` (delta diário, idempotente)
- 08:00 BRT — `yahoo_bars` (N11b, 28/abr): refresh `profit_daily_bars` para 39 FIIs+ETFs (skip weekend)
- 09:00 BRT — `crypto_signals` (N6, 28/abr): snapshot `/api/v1/crypto/signal/{sym}` → `crypto_signals_history` (sem skip — crypto 24/7)
- 09:00 BRT no dia 5 do mês — `cvm_informe` (N2, 28/abr): sync `inf_diario_fi_AAAAMM.zip` da CVM
- 23:00 BRT — `cleanup_event_records_job` (retention 7d/30d)
- A cada 5min em 10h-18h BRT — `reconcile_loop` (DLL ↔ DB, skip silencioso fora pregão/weekend)

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
Últimos commits (28/abr madrugada — Sprint N1-N12 + N5b/N4b/N6b/N10b + housekeeping):
  472f513 feat: migrations alembic + populate default 1m + 2 grafana alerts
  1ae5669 feat: N6b+N4b+N10b + Dockerfile scripts/ no api stage
  760edc8 feat: N11b+N6+N4+N10 — scheduler+crypto persist+RF Markov+FIDC/FIP
  b6160ed feat: N11+N12 — /levels para FIIs/ETFs + drop backup pos-N1
  a66f1fe feat: N5b — fundamentals FII no /dashboard signals (DY/PVP badges + filtro)
  f678a49 feat: N1+N2+N5+N7+N8+N9 — data quality + scheduler + scraper FII + UI fixes

Últimos commits (27/abr — Sprint M1-M5 + features /diario + S/R + flatten):
  7cf157b docs: atualiza Roteiro + Melhorias após sessão M1-M5
  5e7e496 feat: A.16/A.21 — outlier filter S/R + UI fundos analytics
  3573db1 feat: M1-M5 backlog + features /diario + /dashboard S/R + flatten ticker

Últimos commits (21/abr — Sprint Backend V1-V4 + Z3-Z4):
  de57c44 feat(infra): Z3 (4 alert rules adicionais) + Z4 (ensemble multi-horizon)
  c4113ab fix(grafana): ajusta contact-points.yml para provisionar sem erro
  83ae2c8 feat(infra): V1+V2+V3+V4 — DLL logging + ML metrics + Grafana alerts + reconcile cron
  ae6fb7c docs(claude): atualiza CLAUDE.md com Sprint UI 21/abr (24 helpers + Decisoes 16-19)

Últimos commits (21/abr — Sprint UI):
  afd7ecb feat(ui): S (locale switcher PT/EN + sidebar i18n) + T (no-op)
  24b1d9e feat(ui): P (cores hardcoded -> var) + R (selectors)
  dbc3202 feat(ui): O — Light mode toggle
  bc70e24 feat(ui): Q (FAErr.fetchJson 11 sites) + N (i18n scaffold pt/en)
  9f4f4d0 feat(ui): L (toast queue+pause) + M (chart theming)
  f584bb6 feat(ui): H (print) + J (Chart.js theme + lazy) + I (form validation)
  ab4d274 feat(ui): C (loading skeletons) + E (a11y) + F (PWA) + G (FAErr.fetchJson)
  6bfee75 feat(ui): A (FATable+FAEmpty) + B (FAModal/FAToast) + D (error boundary)
  848aaf2 feat(ui): W (auto-skip pre-login) + Z (STATIC_HELPERS) + Y (cache TTL) + AA (FAEmpty screener)
Últimos commits (20/abr):
  e5e8062 infra(observability): Prometheus + Grafana versionados em docker/
  cecf359 feat(wallet): enforce portfolio_id obrigatorio em todos investimentos
  4a71c6f feat(accounts): titular/cpf/apelido + master CRUD + validacao CPF
  1c9311e feat(dashboard): sub-tabs Live/Historico/Mudancas na aba Signals
  c17897c feat(signals): historico diario + endpoints + scheduler
  0c87d85 feat(resample): pipeline ohlc_1m -> N-minute bars (5/15/30/60...)
  2b558ba feat(1m): adapta pipeline para bars 1-minuto (substitui ticks externos)
  e3e47e2 feat(copom): pipeline BERTimbau sentiment end-to-end
  dbd10e8 feat(dashboard): aba Signals mostra ML signals calibrados
  ebcc6c0 feat(mvp-h21): retreino top-20 em horizon 21d
  e78f1b9 feat(signals): /api/v1/ml/signals batch + paineis DI1 Grafana
  833f47b feat(predict_mvp): integra thresholds calibrados com signal BUY/SELL/HOLD
  8be756e feat(di1-realtime): worker funcional — subscribe + Kafka publisher
  eacf748 feat(day1): import_historical_ticks + calibrate_ml_thresholds + paineis RF
```
