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
11. ~~Resample ohlc_1m → N-min bars~~ — **DONE 20/abr** (5/15/30/60m via `resample_ohlc.py`, endpoint `/api/v1/marketdata/bars/{ticker}`)
12. ~~Histórico de signals + scheduler~~ — **DONE 20/abr** (`signal_history`, snapshot diário 18:30 BRT, dashboard sub-tabs Live/Hist/Mudanças)
13. ~~Investment accounts spec (titular/CPF/apelido) + master CRUD~~ — **DONE 20/abr** (incluindo validação CPF DV, FK portfolio NOT NULL/RESTRICT)
14. ~~Prometheus + Grafana versionados em docker/~~ — **DONE 20/abr** (provisioning, removeu `docker run` manual)
15. ~~GPU compute em container (torch+cu124)~~ — **DONE 21/abr** (api/worker/worker_v2 com `cuda.is_available()=True`)
16. ~~Sprint U8 — Hub frontend + observabilidade~~ — **DONE 21/abr** (cleanup scheduler 23h BRT + correlation_id Kafka cross-service + 3 painéis Grafana dead_letter)
17. ~~Sprint UX — RBAC backend + UI portfolios CRUD + alertas indicador + sidebar shared~~ — **DONE 21/abr** (helper `auth_guard.js`, hub admin-only via `_require_admin`, página `/alerts`, soft-delete portfolios via `is_active`, `portfolio_name_history`, `sidebar.js` auto-replace em 38 páginas; commits `49f2ca5`, `5e8ebb1`, `ef71e6a`, `2b59225`, `00b21d6`, `9d2e07f`)
18. ~~Sprint UI 21/abr — Helper-driven UI completa~~ — **DONE 21/abr** — 24 helpers em `static/`, 9 commits (`848aaf2`→`afd7ecb`): toast queue/pause, FAModal Promise, FAErr global boundary, FATable auto-init, FAEmpty CTAs, FALoading skeletons, FAA11y skip-link/focus-trap, FAPrint stylesheets, FACharts theming, FAForm validation, FAI18n PT/EN scaffold + sidebar i18n, FATheme dark/light toggle, FALocale PT/EN switcher, PWA (manifest+sw.js), 343 cores hex→var, 11 fetch boilerplate→FAErr.fetchJson. Decisões 16-19 imutaveis. Ver `STATIC_HELPERS.md`.
19. Aguardando arquivo Nelogica 1m (~2 dias) → rodar `runbook_import_dados_historicos.md`

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
