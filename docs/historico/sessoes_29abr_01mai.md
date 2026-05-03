# Histórico de Sessões — 29/abr → 01/mai 2026

> Conteúdo movido de `CLAUDE.md` para reduzir contexto carregado em cada conversa. Detalhe cronológico das sprints; consultar quando precisar reconstituir motivação de uma decisão ou bug fix específico. Para decisões vinculantes, ver `CLAUDE.md` seção "Decisões Arquiteturais".

## Bugs de produção catalogados em `Melhorias.md`
- P1-P7 + O1 ✅ DONE 28/abr
- P9 (DB stuck status=10) ✅ MITIGADO 29/abr via `_watch_pending_orders_loop` (detection ~10s vs 5min reconcile) + EXTENSÃO 30/abr via `_load_pending_orders_from_db` (cobre restart NSSM, validado live: 10 órfãs marcadas <1s)
- P10 (OCO legacy pares perdidos pós-restart) ✅ DONE 29/abr via `_load_oco_legacy_pairs_from_db`
- P11 + P11.2 (futuros UI exchange/alias) ✅ DONE 29/abr via `_resolve_active_contract` em `get_position_v2` + `flatten_ticker`
- P2-futuros (DB não reflete status=8) ✅ DONE 30/abr via fallback `_msg_id_to_local` em `trading_msg_cb` (commit `07c2445`)
- P8 (broker rejeita futuros) ✅ FECHADO 30/abr — era transient broker degradação 29/abr, não bug
- I4 (`/agent/restart` não restartava) ✅ FECHADO 30/abr — causa real foi `nssm AppExit=Exit` em vez de `Restart`. Diagnóstico expandido (`hard_exit.attempt` + `last_error`) provou que `TerminateProcess` sempre funcionou. Fix: `& nssm set FinAnalyticsAgent AppExit Default Restart`. Ciclo completo agora 9s automático.

## Sessão 29/abr UI overhaul (commits `3896aeb` → `90acb2e`)
- Gap compression overnight/weekend no chart (`_compressGaps` + `_timeRealMap` + `_realToCompressed`); `fitContent()` mostra todos os bars
- Backend `/marketdata/candles/{ticker}` faz `UNION ohlc_1m + ohlc_1m_from_ticks` + resolve aliases futuros (`WDOFUT → WDOK26 + WDOM26`)
- `_doRefresh` SSE comprime timestamps com `_compressIncomingTime`
- Bollinger Bands calculadas **client-side** sobre `_bars2` (era backend `/indicators` daily, não alinhava com candles 5m)
- 4 indicadores novos: Estocástico Lento (14·3·3), ATR (Wilder), VWAP intraday overlay, IFR (label dual RSI/IFR)
- `/static/sw_kill.html` reset de SW + caches via UI
- Carteira: coluna Horário (`created_at` HH:MM:SS), linha branca tracejada zero no chart Rentabilidade

### Operacional 29/abr
- `profit_subscribed_tickers` semeada com **373 tickers** (366 equities IBOV/B3 + 7 futuros: WDO/WIN/DOL/IND/BGI/OZM/CCM)
- `tick_to_ohlc_backfill_job` diário 21h BRT (00h UTC): **DELETE + INSERT** do dia inteiro (substitui rows incoerentes pelo continuous aggregate)

## Sessão 30/abr OHLC + bugs hardening + drag UI (14 commits `5ad447d` → `a7b52aa`)
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

## Sessão 30/abr pós-pregão estendida (8 commits `fdd81f9` → `0a40bf0`)
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

## Sessão 01/mai full day (58 commits, ~11.5h) — feriado Trabalho
Histórico cronológico via `git log --since=2026-05-01`. Pontos vinculantes:
- **Robô de Trade R1.1→R3.3 completo** — `auto_trader_worker.py` (asyncio loop, kill switch, dry_run env), `domain/robot/{risk,strategies}.py` (Risk Engine vol-target Kelly 0.25x + ATR Wilder + max_positions + circuit_breaker DD<-2%), `MLSignalsStrategy` (consome `/api/v1/ml/signals` + cache 60s), `TsmomMlOverlayStrategy` (concordance momentum 252d on-the-fly + ML signal — divergem → SKIP), pairs trading completo (Engle-Granger screening offline + decision logic + service layer + worker integration + dual-leg dispatcher + position persistence + naked_leg→Pushover critical). UI `/robot` (read-only + kill switch) + `/pairs` (z-score real-time + drilldown history) + entries no sidebar. Service `auto_trader` em `docker-compose.override.yml` (`AUTO_TRADER_ENABLED=false` default).
- **E1.1 Research classifier** (`9fc4da9`) — Anthropic SDK + Haiku 4.5 + prompt caching; tabela `email_research` semeada por classify offline. Inicialmente concebido p/ Gmail; abordagem Gmail descartada em 02/mai — fetcher concreto aguarda nova fonte de dados.
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

## Sessão 01/mai noite — limpeza profunda (16 commits `3ddb2c9` → `9883af7`, ~6h)
Detalhamento em `memory/project_session_01mai_cleanup_deep.md`. Pontos vinculantes:
- **Reduções**: profit_agent.py 7058l→4426l (-37%); app.py 1427l→681l (-52%); import_route.py 982l→275l (-72%); wallet_repo.py 1395l→1228l; predict_mvp.py 1026l→902l. **9 módulos novos** extraídos (`profit_agent_db.py`, `profit_agent_types.py`, `profit_agent_http.py`, `profit_agent_watch.py`, `profit_agent_oco.py`, `wallet_models.py`, `predict_mvp_schemas.py`, `import_parsers.py`, `startup/routers.py`).
- **Padrão de extração** (preserva API pública): `ProfitAgent` mantém métodos como **stubs** delegando pra funções top-level que recebem `agent` por parâmetro. State compartilhado (`_oco_pairs`, `_db`, `_stop_event`, etc.) acessado via `agent.X`. `self._oco_monitor_loop()` continua funcionando externamente.
- **ProfitDLL/ untracked** (commit `3ddb2c9`): -94MB do repo (PDF Manual + exemplos C#/C++/Delphi/Python movidos pra `D:/Projetos/references/ProfitDLL/`). `.gitignore` consolidado.
- **scripts archived**: 38 patches/fixes/diagnósticos one-shot em `scripts/_archive/` + README explicativo.
- **ruff check**: 227 erros → 0 (`per-file-ignores` expandido em `scripts/**` p/ E701/E702/SIM/UP/B/C/RUF; demais arquivos zero).
- **UI: sidebar collapse/expand** (`94b436c`): clicar header agrupa/desagrupa; persiste em `localStorage.fa_sb_groups_collapsed`. Font reduzida 16→13px (`.fa-sidebar .fa-sb-link` specificity vence inline). Aplicado às 39 páginas via Decisão 16.
- **Bug regressão crítica fixado** (`d364655`): `_static = pathlib.Path(__file__).parent / "static"` em `startup/routers.py` apontava pra `startup/static/` (não existe) após o move. **Todas as 39 páginas servidas via `_html()`** retornaram 404 com `<h1>X.html não encontrado</h1>`. Fix: `parent.parent`. Lição: refator que move `__file__`-dependent paths exige smoke navigation (não pega via unit test). Memória `feedback_file_path_refactor_smoke.md`.
- **Routine `sidebar-pattern-audit`** (`trig_012VG1XwoP392yy1JCehuEpQ`) Seg 09h BRT semanal — varre páginas privadas faltando pattern Decisão 16, abre PR auto se achar match.
- **1697 testes verdes** em todas etapas. Smoke `create_app()` validado via Playwright (428 routes registradas).

## Done recentes (28/abr → 02/mai) — apêndice movido de CLAUDE.md em 03/mai

Detalhe completo em `memory/project_*.md` + `git log`. Lista canônica:

- ✅ **Sessão 02/mai — 12 commits, 6 bugs latentes críticos pré-smoke**: TSMOM bars 5m→daily (`/candles_daily` UNION endpoint); pairs z-score 5m→daily (`fetch_daily_closes`); Postgres robot_* zumbi DROP (alembic ts_* registry-only — Decisão 23); cointegration_screen Fintz-only stale 6mo → UNION cross-source (PETR3-PETR4 visível); features_daily stale 6mo → UNION cross-source (PETR4 SELL falso → real); ANBIMA pipeline stale → novo `yield_curves_refresh_job` 21h BRT + bug raiz builder fixed (separar range output do range séries). ML signals: 14 BUY / 1 SELL / 26 HOLD com features de 2026-04-30. 1499 tests verdes.
- ✅ R5 **survivorship bias fechado** (02/mai) — tabela `b3_delisted_tickers` populada (1863 CVM placeholders + 449 FINTZ tickers reais), `DelistedTickerRepo` + `DelistingInfo`, engine `run_backtest` aceita `delisting_date`/`last_known_price` (force-close + truncamento), `BacktestService` aceita `delisting_resolver` opcional, demo `--respect-delisting`. 19 tests novos. Caminho via Fintz delta (884 tickers histórico Fintz cruzados com `profit_subscribed_tickers`) substituiu plano original PDF IBOV — mais barato, cobertura superior.
- ✅ Robô R1.1→R3.3 (TSMOM ∩ ML overlay + pairs cointegrados B3) — 01/mai
- ✅ E1.1 Research classifier (Anthropic SDK + Haiku 4.5 + prompt caching) — source-agnostic, fetcher pendente — 01/mai
- ✅ I1 Fases A+B.1+B.2 — Engine WSL2 + volumes ext4 nativo — 01/mai
- ✅ C1 producer Kafka `market_data.ticks.v1` (Avro) — 01/mai
- ✅ R5 backtest harness (slippage + DSR + walk-forward + history endpoint) — 30/abr
- ✅ C5 handshake `_source` + `_client_order_id` (Passo 7) — 30/abr
- ✅ Limpeza profunda: profit_agent 7058→4426l, app.py 1427→681l, ruff 227→0 — 01/mai noite
- ✅ Bugs fechados: P1-P11 + P2-futuros + I4 (NSSM AppExit=Restart)
- ✅ R4 ORB WINFUT scaffold (03/mai) — `ORBStrategy` em `domain/robot/strategies.py` + registry + 5 unit tests; SKIP até implementação real (defer 7-10d)

## Decisão 22 — histórico de fases (movido de CLAUDE.md)

I1 Fase A done 01/mai (commit `ab0ea8b`). Fase B.1 cutover live 01/mai (commit `950ac35`). Fase B.2 done 01/mai (commit `ffcd06c`) — volumes Postgres+Timescale em ext4 nativo. Runbook: `docs/runbook_wsl2_engine_setup.md`.
