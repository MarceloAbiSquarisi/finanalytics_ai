# Pendências do projeto — leia primeiro

> **Para Claude/agente:** este é o primeiro arquivo a consultar em qualquer sessão. Contém pendências priorizadas + carryover de sessões anteriores. Atualizar ao fim de cada sessão (mover concluídas pra `## Done recente` e depois pra `docs/historico/`).

Última atualização: **2026-05-07 21:30 BRT** (R5 sweep 12 runs + trade-level DSR — id=11 emerge como deployment candidate com prob_real=72%)

### Done 07/mai noite (sessão R5 sweep + trade-level DSR)

Continuação direta do harness end-to-end (3 commits adicionais):

- ✅ **R5 param sweep** (commit `fa29413`): `scripts/r5_sweep.py` (7 single-axis: th/retrain/horizon/tvol) + `scripts/r5_sweep_combo.py` (3 combos defensivos). 10 runs novos no DB (id=3..12). Sequencial — paralelizar saturaria CPU (vide `feedback_zombie_python_container.md`).

  **Sensitivity ranking vs filter baseline (id=2 sharpe=1.351, prob=55.7%):**
  - **retrain=42** (id=5): sharpe→1.865 (+38%), prob→91.8%, ret_sum 4132%→8203% — winner credível (mesmo skew/kurt do baseline).
  - **horizon=42** (id=8): sharpe→1.940, prob_under=99.8% **suspeito** (PETR4 underlying skew=11.86 kurt=218 distorce Mertens).
  - **horizon=10** (id=7): sharpe→1.781, prob=86.6%, BMEB4 19 trades.
  - **vol=0.015** (id=9): sharpe=igual, dd_avg 23.6%→18.6% (-5pp).
  - **th=0.05/0.15** (id=3,4): sharpe Δ ≤ +0.23, return cai com seletividade.
  - **retrain=126** (id=6): essencialmente idêntico ao baseline.

  **Combos (sweep #2):**
  - **id=10 retr42+vol015**: sharpe=1.832, prob=90.9%, dd=18.7%, ret=5475% — efeitos somam linearmente (sharpe do retrain + dd do vol). Best deployment dentro do sweep.
  - id=11 h10+vol015: sharpe=1.795, prob=87.1%, dd=19.9%, n_trades=19.
  - id=12 h42+vol015: prob_under=99.7% mantém suspeita.

- ✅ **Trade-level DSR** (commit `71e3652`): `scripts/r5_trade_level_dsr.py` re-roda WF do best_ticker de cada run, extrai `trade.pnl_pct`, computa DSR completo com:
  - `annualization_factor = 252/avg_duration_days`
  - `sample_size = num_trades`
  - `skew, kurt = momentos amostrais dos pnl_pct`

  **Findings reveladores:**
  - Trade-level skew/kurt são **muito menores** que underlying (PETR4: 11.86/218 → 1.25/3.08). Estratégia naturalmente filtra outliers via thresholds + holding.
  - **id=12 não é puro artifact** — trade-level confirma 92% mas com N=7 trades só (insuficiente).
  - **id=2 filter baseline é PIOR que parecia**: prob_trade=21% (vs 55.7% under). Without winner config, é noise.
  - **id=11 (h=10+vol015)** emerge como melhor candidato: prob_trade=72% com N=19 trades (sample size adequado).
  - Confiável só com `n_trades ≥ 17`. CSMG3/PETR4 com 7-8 trades têm Mertens unreliable (T pequeno → sigma minúsculo → z explode pra 100%).

**Conclusão R5 deployment-ready:**
- **Prefer id=11** (h=10, retrain=63, target_vol=0.015): sharpe=1.795, dd_avg=19.87%, prob_trade=72%, sample size adequado.
- Próximo nível pra subir prob_real: rolling-origin walk-forward com 3-5 folds (cada fold com universo + Neff + DSR independente, agregar).

**Memórias persistentes adicionais:**
- Trade-level DSR é o "número honesto" mas exige `n_trades ≥ 17` pra Mertens funcionar.
- Underlying-DSR superestima prob_real em horizons longos (kurt explode).
- Effects orthogonal: retrain ↑ sharpe; vol-target ↓ drawdown; combinam aditivamente.

### Done 07/mai tarde-noite (sessão R5 harness)

R5 multi-ticker walk-forward completo end-to-end com correção Bailey/LdP de multiple-testing bias:

- ✅ **R5 harness MVP** (commit `ec877a8`): `scripts/r5_harness.py` agrega N tickers via `mlstrategy_backtest_wf.run_wf_for_ticker()` (refatorado pra ser callable). Smoke 5 tickers em 45s.

- ✅ **Pickles h3/h5 top-20**: 18 pickles gerados (9 ações × 2 horizontes), `predict_ensemble` agora retorna 4 horizontes (1d/3d/5d/21d, peso uniforme 0.25 cada). 11 FIIs/ETFs abortaram com `train < 50 rows` (Yahoo 2y insuficiente). Workaround: `OMP_NUM_THREADS=4` + `/tmp/models_out` + `docker cp`.

- ✅ **Universo full 87 tickers** (run id=1 baseline): sharpe_max=1.375 (CSMG3), dd_avg=33.7%, dd_max=99.5% (GFSA3), 12.2 min total. dsr_proxy_raw=0 (E[max]_N=87=2.48 inalcançável — falso negativo).

- ✅ **Neff correction LdP** (commit `4ddcf2c`): `_load_test_returns_matrix` + `_compute_neff` calculam mean_corr + N_eff_var (Mertens) + N_eff_eig (participation ratio = (Σλ)²/Σλ², Bailey/LdP). Sobre 87 tickers: ρ̄=0.224, **N_eff_eig=10.7**, N_eff_var=4.3. Aplica `deflated_sharpe()` full no best_ticker (skew/kurt do underlying).

- ✅ **Filtros min_close + vol-targeting** (commit `857508c`):
  - `--min-close 1.00` filtra penny stocks (0/87 excluídos no run atual).
  - `--target-vol 0.02`: pos_size = clip(target/train_vol_21d, 0.1, 1.0). Median pos=0.71. GFSA3=0.363, IRBR3=0.367.
  - **Resultado** (run id=2 filter): sharpe_max=1.351 (BBSE3), **dd_avg 33.7→23.6%, dd_max 99.5→66.2%**. GFSA3 dd 99.5→36.1% e ret +264→+446% (path dependency win). DSR full BBSE3: skew=-0.96 kurt=14.1, prob_real=55.7% (vs 70.4% baseline com CSMG3 — skew negativo penaliza).

- ✅ **Persistência DB** (commit `bfff89d`): migration `0027_r5_harness_runs` cria `r5_runs` + `r5_ticker_results` em `finanalytics`. Schema separado de `backtest_results` (que é per-(ticker, config)). Idempotência via `generated_at`. Script `scripts/r5_ingest.py` glob-aware. 2 runs ingestados validados via SQL cross-run query.

- ✅ **API + UI /r5** (commits `22c7155` + `e351a69`):
  - 5 endpoints `require_master`: list runs, run detail, run tickers (sortable), ticker history (cross-run), runs diff.
  - Página `/r5` com lista de runs → drill-down → per-ticker sortable → diff mode (clica "Diff vs ..." + outro run = tabela de Δsharpe/Δret/Δdd).
  - Sidebar/breadcrumbs/command palette/i18n integrados (entre Backtest e Otimizador na seção Análise & ML).
  - Validado via Playwright: tabela 87 tickers renderiza, GFSA3 mostra pos=0.36 dd=36.1%.

**Lições aprendidas (registradas como memórias)**:
- DSR cru com N grande é falso negativo quando trials são correlacionados (B3 stocks ρ̄≈0.22). Sempre corrigir via N_eff_eig.
- Vol-targeting é win unilateral pra deployment: sharpe preservado, drawdown -30% agregado, GFSA3 dobra final equity.
- `kill -9` de dentro do container falha quando processos foram spawnados via `docker exec -d` sob outro uid → restart container resolve.
- Container CPU 1600% sem processos visíveis = procurar zombies via `docker top` (host PIDs) + restart.
- `<faSidebar>` não é um custom element real; sidebar.js auto-replace busca `.fa-sidebar` (aside).

**Próximos increments R5** (não atacados hoje):
1. Survivorship via `b3_delisted_tickers` (tabela existe via 0025, mas vazia — precisa pipeline CVM/B3 pra popular)
2. Param sensitivity sweep (varrer th_buy/th_sell/retrain_days, ingest cada um)
3. Trade-level DSR (skew/kurt das pnl_pct dos trades, não do underlying — mais correto teoricamente)

### Done 07/mai (sessão boot resilience)

- ✅ **profit_agent boot resilience** (commits `d35cb96` `7e72570` `893b8c3`):
  - **P0.1 HTTP server cedo**: sobe ANTES de DLL/DB. `/status` responde com `boot_phase` desde init. API Linux nunca perde controle remoto.
  - **P0.2 Boot watchdog interno**: thread auxiliar mata processo se `phase != ready` em 300s (env `PROFIT_BOOT_TIMEOUT_S`). NSSM restarta.
  - **P1.1 Heartbeat por etapa**: 9 fases instrumentadas (init → starting → loading_dll → dll_initialize_login → wait_market_connected → db_connect → db_setup → subscribe_tickers → ready). `boot_phase_history` exposto via `/status` com elapsed_s por etapa. Diagnóstico em segundos.
  - **P1.2 DB statement_timeout=10s + connect_timeout=5s**: lock contention vira erro rápido em vez de wait forever. Override via `PROFIT_DB_STATEMENT_TIMEOUT_MS`.
  - **P2.1 Subscribe em thread daemon**: loop sequencial em thread separada (NÃO paralelizamos por DLL não documentar thread safety pra SubscribeTicker/PriceDepth — Erro.log 05/mai mostrou 4 AVs em paralelizações). Main parte pra `ready` em 120s mesmo com subscribe travado. Subscribe parcial > boot stuck. `subscribe_progress {total, completed, failed, current}` em `/status`.
  - **P2.2 Healthcheck externo**: `scripts/healthcheck_profit_agent.ps1` (curl :8002/status, 3 tries + cooldown 180s, restart se phase!=ready ou boot_elapsed > 600s). Setup via `scripts/setup_healthcheck_task.ps1` que auto-eleva UAC e registra Scheduled Task como SYSTEM 1×/min.
  - Bug PowerShell encontrado em smoke: Write-Output dentro de função PS poluía return value. Fix: Write-Host (não vai pra output stream).

- ✅ **Job badge UX fix** (commit `5c2573d`): badge não mostra mais verde "done" enganoso quando 100% items deram err. Agora diferencia ok/done c/ N err/falhou (todos err) por counters.

- ⚠ **Smoke WDOFUT 04-06/05** (job #8): 1/3 sucesso real
  - 04/05: ok 521.507 ticks em 5m29s ← backfill comprovadamente funcional
  - 05/05: err ReadError 119s (conexão fechou — provável restart do agent que fiz durante smoke)
  - 06/05: err timeout 365s (>300s+30s buffer pra futures — DLL ocasionalmente demora)
  - Re-tentar vai resolver os 2; foram blips transitórios, não bug estrutural.

### Setup pendente do user (1 click, depois da sessão)

- [ ] Rodar `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\setup_healthcheck_task.ps1` pra ativar o Scheduled Task do healthcheck externo (P2.2). UAC vai pedir aprovação.

### Done na tarde 06/mai (sessão 2)

### Done na tarde 06/mai (sessão 2)

- ✅ **Aba `/admin → 📦 Backfill` implementada** (4 boxes):
  - **Iniciar Backfill**: multiselect tickers (com filtro), date range, force_refetch, ETA estimado
  - **Jobs em andamento**: dashboard com progresso (barra + counters ok/skip/err), auto-refresh 2s enquanto running, ver-items + cancel
  - **Falhas**: filtro por data + ticker, query em `backfill_job_items WHERE status='err'`, botão "Reagendar resultados" (cria job novo só com falhas)
  - **Importar Arquivo**: multipart `UploadFile`, OHLC 1m funcional (CSV/Parquet/JSONL), Tickers placeholder (501)
- 📦 **Tabelas novas**: `backfill_jobs` + `backfill_job_items` em TimescaleDB. SQL idempotente em `init_timescale/007_backfill_jobs.sql`. Migration ts_0005 registry-only (Decisão 23). Aplicar em containers existentes: `python scripts/apply_backfill_migration.py` ou `docker exec finanalytics_timescale psql ... < init_timescale/007_backfill_jobs.sql`.
- 🔌 **Endpoints novos** (todos `require_master`):
  - `POST /api/v1/admin/backfill/jobs` cria + dispara worker async
  - `GET /api/v1/admin/backfill/jobs[/{id}[/items]]`
  - `POST /api/v1/admin/backfill/jobs/{id}/cancel` (worker checa entre items)
  - `GET /api/v1/admin/backfill/failures?date_start&date_end[&ticker]`
  - `GET /api/v1/admin/backfill/tickers` (proxy `:8002/tickers/active`)
  - `POST /api/v1/admin/import/ohlc-1m` multipart
- 🧠 **Worker**: `application/services/backfill_runner.py` — single `asyncio.Lock`, sequencial (DLL serializa), httpx async, skip se `market_history_trades` já tem dado (a menos que `force_refetch`). Limitação conhecida: API restart durante job → items 'running' órfãos (v2 adiciona recovery query).
- 📥 **Importer refatorado**: `application/services/ohlc_importer.py` extraído de `scripts/import_historical_1m.py`. CLI mantém contrato. CSV agora tem auto-detect de separador (vírgula/ponto-e-vírgula/tab) — útil pra Nelogica PT-BR.
- ✅ **Smoke E2E validado**: criar job → worker → items pra err graciosamente (agent down) → failures dashboard mostra → cancel job 5×4=20 items para no item 10 → import CSV 5 linhas vai pra `ohlc_1m` com source `smoke_test`. Cleanup feito.
- ✅ **Folder-import com pasta DINÂMICA** (sessão tarde 06/mai, evolução): aba `/admin → 📦 Backfill` Box 4 tem 2 modos — **📂 Pasta no servidor** (default) e **⬆ Upload manual**.
  - Operador aponta a pasta no momento da importação (input mandatório). Aceita Windows-path `E:\sua\pasta` ou container-path `/host_e/sua/pasta`.
  - Drive `E:` montado amplo em `/host_e` no api container via `docker-compose.wsl.yml`. Para adicionar D: ou C:, estender `HOST_DRIVE_MOUNTS` em `admin_import.py` + volume no compose.
  - Subpastas criadas DENTRO da pasta de origem (auditoria local): OK → `<pasta>/historico/<run_id>/`, falha → `<pasta>/erros/<run_id>/`. run_id = `YYYYMMDD-HHMMSSZ` UTC. Dry-run preserva arquivos.
  - Path traversal protegido: `_validate_under_mount` exige path resolvido sob `/host_e`. Drive não-montado → 400 com mensagem clara. CSV PT-BR (`;` + `,` decimal) auto-detectado.
  - Endpoints: `GET /api/v1/admin/import/inbox?folder=...` (mandatório) lista arquivos. `POST /api/v1/admin/import/ohlc-1m/folder` body `{folder, dry_run, source, column_map, only_tickers, min_price}` processa.
  - Smoke validado: pasta `E:\test_nelogica_dynamic\` arbitrária, 3 CSVs (PT-BR + EN + invalido), 2 movidos pra historico/<run_id>/, 1 com OHLC inconsistente pra erros/<run_id>/, 3 linhas em DB. Cleanup feito.
  - Para volumes muito grandes use modo Pasta (cap 5000 arquivos/chamada vs 50 do upload).
- ✅ **Refinamentos UI/UX no Backfill** (sessão 06/mai, 4ª iteração):
  - **Tickers vêm do DB** (`profit_subscribed_tickers`), não do agent — funciona com agent off (validado: 358 tickers retornados). `?include_inactive=true` opcional.
  - **Caixa de seleção `<select multiple>`** substituiu checkbox-list (mais compacta) com pills dos selecionados embaixo + botão `×` em cada pill pra remover individualmente. Filtro preserva estado entre re-renderizações.
  - **Preview de colunas em mini-tabela**: 2 linhas (header gold + 1ª linha do arquivo), com warning explícito quando colunas todas vazias ("separador pode estar incorreto"). Modo Pasta lê via `/inbox` (server-side, csv.Sniffer); modo Upload usa FileReader client-side.
  - **Barra de progresso indeterminada** (CSS animation `bfProgressSlide`) durante o request de import.
- ✅ **Mapping arquivo→banco + colunas novas** (sessão 06/mai, 5ª iteração):
  - **Tabela de mapeamento UI** abaixo do preview: cada coluna do arquivo vira uma linha com dropdown pra escolher coluna do DB (ou `(ignorar)`). **Auto-detect heurístico** PT-BR/EN cobre Ativo/Ticker→ticker, Data→time, Abertura→open, Máxima→high, Mínima→low, Fechamento→close, Quantidade→quantidade, Volume→volume, Negócios→trades, Aftermarket→aftermarket, VWAP→vwap. Duplicatas marcadas com border vermelho + ⚠.
  - Botões "↻ Auto-detectar" e "{ } texto" (toggle do input textual avançado pra bypass).
  - **Schema `ohlc_1m`** ganhou 2 colunas (`init_timescale/008_ohlc_1m_extra_cols.sql`, alembic `ts_0006`):
    - `aftermarket BOOLEAN` — TRUE se barra negociada em after-market
    - `quantidade BIGINT` — #ações/contratos negociados (distinto de `volume` que é R$ financeiro)
  - **`ohlc_importer.py`** lê esses 2 campos via `parse_bool` (true/1/yes/sim/s/t e equivalentes pt-br) e `to_int`. UPSERT usa `COALESCE(EXCLUDED.col, ohlc_1m.col)` pra não sobrescrever com NULL em re-imports.
  - **Smoke validado**: PETR4 com 3 barras (2 normais + 1 after-market às 19:00), `column_map` PT-BR completo → DB tem `aftermarket=f|f|t`, `quantidade=100000|80000|5000`, `volume=3.85M|3.09M|194K`. Cleanup feito.

## Top priority — pegar antes do próximo smoke real

### Estado atual do sistema (tarde 06/mai)

- 🛑 **profit_agent NSSM STOPPED** (12:29 BRT) — manualmente pra evitar restart-loop infinito noite. Ticks parou de fluir ~11:00 BRT (causa root externa, server Nelogica não pusha mais pra credencial). Próxima sessão: tentar `nssm start FinAnalyticsAgent` com mercado fechado / ou após Windows reboot.
- 🟢 **auto_trader** Up mas paused via kill switch DB (irrelevante enquanto agent down)
- 🔴 **kill switch ON**: `robot_risk_state.paused=True reason=end_of_smoke_05mai_pause_overnight`
- ✅ 8 commits feat/trade-engine-validate-execution-tabs sincados origin (master..HEAD): `69ae21c` UI trade-engine + `58cc960` cleanup tickers + `cb1ed02` pairs lot_size + `f059aa1` docs + `0136990` asyncpg fix + `934b46d` /hub display fix + `2cb0cf0` profit-agent DLLFinalize+watchdog + `8f2ec82` docs final smoke.
- ⏸️ **Backfill 2026-05-05 PAUSED** — state preservado em `E:\finanalytics_data\backfill_resilient_state.json` (ok=2 skip=27 err=0 de 373 tickers, retomar via `pwsh scripts/backfill_supervisor.ps1`)
- ✅ **Smoke do refactor DONE 06/mai 10:25** — todas 5 validações live passaram (08:25 boot até ~11:00 quando entrou em stuck state).
- ✅ **Smoke /hub 06/mai (display + offline)** — display fixado (`934b46d`), bug raiz "agent stuck server-side" identificado mas não resolvível em código (commit `2cb0cf0` deu fix técnico; runbook P11 documenta fallback manual).

### P0 — pendentes pra próximo smoke

- [x] ~~Confirmar posição PETR4 broker = 0~~ — **DONE 05/mai 16h** via /positions/dll fresh (2.3s response, fix #2 confirmado). Smoke validacao pos-fixes: 3 SELLs disparadas com OCOs bilaterais (#3) preenchendo automaticamente, posição final = 0 sem intervencao manual. Cached DB ainda mostra net_qty=-700 stale (callbacks dropped durante bug #2 ativo); não reverte sem reconcile profundo.
- [x] ~~Resume kill switch antes do smoke~~ — **DONE 05/mai**: ciclo paused→active→3 dispatches→paused completo executado. Kill switch volta pra `paused=True smoke_validacao_fixes_done_05mai` ao fim.
- [x] ~~Smoke validação refactor Delphi-aligned~~ — **DONE 06/mai 10:25** (ver Done recente).

### P0 — descoberto durante smoke /hub 06/mai

- [x] ~~**Watchdog cego (counter sobe mesmo com queue.Full)**~~ — **FIX 06/mai** (commit `2cb0cf0`): `_total_ticks_queued` incrementado APENAS após `put_nowait` OK (3 callbacks: V1/V2/HistV2). Watchdog usa esse counter. `/status` retorna ambos `total_ticks` (received) e `total_ticks_queued` (persisted). Heartbeat também mostra ambos pra detectar discrepância. Validado live: pattern `_total_ticks_queued` aparece corretamente em /status.
- [x] ~~**Restart sem recuperação de ticks — fix técnico DLLFinalize**~~ — **FIX 06/mai** (commit `2cb0cf0`): `_self_heal_restart` e `/restart` HTTP handler chamam `DLLFinalize()` com timeout 2s ANTES do `TerminateProcess`. Validado live em vários ciclos: `self_heal.dll_finalize_ok` (~720ms) e fallback `dll_finalize_timeout` ambos funcionam. Runbook P11 documentado em `docs/runbook_profit_agent.md`.
- [ ] **🔴 ATIVO: Server Nelogica não pusha ticks pra credencial 06/mai** — após múltiplas mitigações (DLLFinalize fix, Profit Pro UI logout manual, 5min idle, fresh nssm start), agent permanece em estado UP+connected+0_ticks. Profit Pro UI funciona normal com a mesma credencial (validado pelo log do user que mostrou OrderHistoryCallback Count=88, position callbacks). Hipóteses:
  1. **Rate limit/blacklist server-side** após 15+ logins failed em 2h — só passa com tempo (1-2h+ idle ou no próximo dia)
  2. **DLL state cached em kernel/shared memory** que só Windows reboot limpa
  3. **Algum mismatch específico** entre nossa DLL version + server config

  Próximas ações sugeridas (não tentadas hoje):
  - Restart Windows host completo (limpa kernel objects/COM cache)
  - Esperar até amanhã mercado fechado pra retry com state limpo
  - Contatar suporte Nelogica se persistir após reboot/24h
- [ ] **`zombie_scan_failed` no boot** — `'NoneType' object has no attribute 'splitlines'` consistente em todo boot. Subprocess decode error ('utf-8' can't decode byte 0xe4) — `tasklist`/`wmic` retornando CP1252. Não bloqueia mas pollui log.
- [ ] **Bug latente: V1 callback faz trabalho real** — comentário diz "apenas satisfazem a DLL na init", mas implementação de `_trade_v1_init` faz `self._total_ticks += 1` + `self._db_queue.put_nowait(...)`. Se V1 e V2 ambas firarem, double-counting. Pattern Delphi-aligned 06/mai pode ter agravado isso.

### P1 — qualidade/robustez

- [x] ~~**Pairs sizing não respeita lot_size**~~ — **DONE 06/mai** em `auto_trader_worker.py:_compute_leg_quantities` + 6 testes novos cobrindo lot=100/1/None + cenário smoke 05/mai (qty=93→0). Próxima abertura de pair vai arredondar antes de dispatch.
- [x] ~~**/hub Profit Agent (NELOGICA) section text cut**~~ — **DONE 06/mai** commit `934b46d`. Section escapava de `.main` (estava após `</div></div></div>` que fechavam fa-page-content). Fix: mover pra dentro de `#monitorContent` + remover `<` órfão linha 349.
- [ ] **Trailing stop automático nas posições** — fix 04/mai cobriu OCO estático bilateral; trailing dinâmico (atualizar SL conforme preço caminha a favor) ainda pendente. `validate_attach_oco_params` já aceita `is_trailing/trail_distance/trail_pct` per-level mas dispatcher só passa SL fixo. Defer pra sessão dedicada.
- [ ] **Escapar `$$` no `.env PROFIT_SIM_ROUTING_PASSWORD`** — compose interpreta `$utD_$` como var → senha truncada para `wB#.&5hd!8$`. Irrelevante em sim path (não injeta senha) mas precisa correto antes de production. Trocar pra `wB#.&5hd!8$$utD_$$`.
- [ ] **Lookup automático de `lot_size` por ticker** — hoje hardcoded `100` no config_json. Para futuros (WINFUT/WDOFUT) lote é 1; para BDR alguns são 1, outros 10. Adicionar coluna `tickers.standard_lot` ou tabela de referência. Substitui o context.get("lot_size", 100).
- [ ] **Alerta Grafana se `profit_agent.stderr.log` cresce** — bug `/agent/restart` NameError ficou silencioso 3+ dias porque ninguém olhava stderr. Adicionar regra: stderr > N bytes/hour = critical alert. **Agora possível**: target Prometheus profit_agent finalmente up (fix #4).
- [ ] **Backfill 2y futures** retomar — script `scripts/backfill_2y_futures.py` + `backfill_dashboard.ps1` prontos, com batch INSERT (#5) deve completar em ~1-2 dias 24/7 (vs estimativa anterior de 24 dias).
- [ ] **Merge do PR #8** (`fix/profit-agent-cleanup-regressions`) — 14 commits acumulados 28/abr → 04/mai com fixes críticos do agent + smoke results. Aguardando review.

## Carryover de sessões anteriores

### Ativas

- [x] ~~**Pickles h3/h5 multi-horizon** (carryover Nelogica)~~ — **DONE 07/mai** sessão R5 (ver Done acima). 18 pickles gerados (9 ações), `predict_ensemble` retorna 4 horizontes. 11 FIIs/ETFs abortaram com Yahoo 2y; aguardando arquivo Nelogica para cobrir o restante.
- [ ] **Aguardando arquivo Nelogica 1m completo** → cobrir tickers que falharam no h3/h5 (FIIs/ETFs com <2y Yahoo). Runbook em `docs/runbook_import_dados_historicos.md`.
- [ ] **C5 Passos 2-6** (VIEW unified + UI pill manual/engine) bloqueados pela migration do trading-engine R-06; agente `trig_01VDzH3xriAC777KZku42SbK` p/ 21/mai abre PR pareado.
- [ ] **E1 fetcher concreto** — classifier `ResearchClassifier` + worker scaffold prontos; aguardando definição da fonte de dados p/ implementar `ResearchFetcher`.

### Roadmap futuro (em `Melhorias.md`)

- [ ] **R4** ORB WINFUT + filtro DI1 — scaffold pronto (`ORBStrategy` registrado, retorna SKIP); implementação real defer ~7-10d.
- [ ] **E2-E3** Pipeline de research/notas (notas corretagem reconciliation E2 | pipeline genérico E3) — aguardando fonte de dados.

## Done recente (mover para histórico após 1 semana)

### 2026-05-06 manhã (smoke validação refactor Delphi-aligned com pregão aberto)

5 validações live passaram às 10:00-10:25 BRT (~2h após boot do refactor às 08:25):

- ✅ **Status agent UP** — login_ok+activate_ok+market_connected+routing_connected+db_connected todos True; 387 subscribed; uptime ~5h estável.
- ✅ **Ticks flowing saudável** — 28-46k ticks/min × 180-195 tickers ativos por minuto; total counter 697k → 971k em 5min (~55k tps avg). Refactor não quebrou ingestão.
- ✅ **InvalidTickerCallback firing** — 21 tickers rejeitados durante boot 08:25:11-14 SEM AV nativo: WDOM26/WINM26 (futuros vencidos), AZUL4/EMBR3/ELET3/6/BRFS3/CPLE5-6/MRFG3/PETZ3/PORT3/RDNI3/REAG3/RNEW11/SRNA3/STBP3/ZAMP3/LVTC3 (delisted/M&A/halt), OZMM26 (opção), e XPTO (test garbage no DB). Agent sobreviveu — pre-refactor isso causava AV em `SubscribePriceDepth+0xD1`.
- ✅ **Watchdog ativo + sem reconnect storm** — `dll_watchdog_started` 08:25:14; zero `reconnect_storm`/`no_ticks_market_open`/`login_lost` em 5h+ de mercado aberto. Total ticks crescendo continuamente (stuck-detector implicitly OK).
- ✅ **State decoder limpo** — boot Delphi-aligned: login síncrono via `DLLInitializeLogin`, sem state_cb spam de inicialização (esperado, callbacks registrados pré-wait); zero state transitions desde 08:25 = conexão totalmente estável. Compare com pré-refactor: 02:59-03:01 storm de 50+ transições cstRoteamento em 2min.

**Bug latente descoberto durante validação**:
- `XPTO` ticker no `profit_subscribed_tickers` — provavelmente artefato de teste antigo. Limpar do DB.
- 17 tickers delisted/M&A/halt confirmados pelo callback. Limpar do DB também.
- (NÃO tocar em WDOM26/WINM26 — alias resolver vai re-subscribed pro contrato vigente quando código rolar pro próximo vencimento.)

### 2026-05-06 madrugada (sessão noite 05→06/mai: refactor Delphi-aligned + backfill resilient)

**Root cause de instabilidade DLL identificado** (via Erro.log nativo `C:\Nelogica\Erro.log` + comparacao com `Nelogica/Exemplo Delphi/`):
- 4 crashes consecutivos em `ConnectorMarketDataLibraryU.SubscribePriceDepth+0xD1` com `Read of address 0x270` = struct interno NULL
- Causa: nosso Python registrava `Set*Callback` APOS `_market_connected.wait()`, deixando janela onde DLL recebia eventos sem handler → state corrupt
- Cliente Delphi (estável) registra TODOS callbacks IMEDIATAMENTE após `DLLInitializeLogin` retornar `NL_OK`

**Refactor profit_agent.py (commit `be82bdd`):**
- ✅ `_post_connect_setup()` movido pra ANTES do wait (match Delphi `frmClientU.pas:380-407`)
- ✅ Slot 8 (new_trade) e 13 (progress) do `DLLInitializeLogin` = `None` como Delphi
- ✅ `SetInvalidTickerCallback` adicionado — alimenta `self._invalid_tickers` set; `_subscribe()` pula tickers já rejeitados
- ✅ `SetChangeStateTickerCallback` adicionado — log de frozen/auctioned/halted (visto funcionando no boot 06/mai 09:29)
- ✅ `SetEnabledHistOrder(1)` chamado FIRST como Delphi
- ✅ `_subscribe()` try/except `OSError` (AV nativo → mark invalid; agente não morre)
- ✅ Constants renomeadas (`CONN_STATE_INFO`/`CONN_STATE_ACTIVATION`) com alias backwards-compat
- ✅ TConnInfo decoder no state_cb: `result=1` agora aparece como `ciArLoginInvalid` com mensagem actionable
- ✅ Boot diagnostics: identidade processo + comprimentos credenciais
- ✅ `_dll_watchdog_loop` thread: detecta reconnect storms (≥6 transições/2min × 3 episódios) E no-ticks em mercado aberto (>5min) → `_self_heal_restart` via `_hard_exit` pra NSSM restart

**Backfill resilient infrastructure (commit `bf30bbf`):**
- ✅ `scripts/backfill_resilient.py` — state checkpoint persistente, max 3 attempts/ticker, exit_code=2 quando 5 erros consecutivos, atomic state save, heartbeat 30s, SIGINT graceful
- ✅ `scripts/backfill_supervisor.ps1` — supervisor com Wait-AgentReady 240s + loop max 12 iter; exit 2 → Stop+Start FinAnalyticsAgent + re-run
- ✅ `scripts/backfill_resilient_dashboard.ps1` — dashboard refresh 10s
- ✅ `scripts/backfill_today_subscribed.py` — TIMEOUT_S 300→60, MAX_CONSECUTIVE_ERRORS=5, ABORT event
- ✅ `.gitignore`: Nelogica/ (110MB) + robot_status_*.png

**Bugs descobertos + corrigidos durante a sessão:**
- ✅ `.env PROFIT_PASSWORD` tinha `$$` dobrado errado → server retornava `ciArLoginInvalid` (code 1) — descoberto via novo state_cb decoder. User corrigiu.
- ✅ Cascade de timeouts no backfill (DLL Nelogica não tem `CancelHistoryTrade` — quando server não responde 1 ticker, DLL stuck emitindo `progress=0` indefinidamente, bloqueando próximas chamadas). Solução: timeouts curtos + early-exit + supervisor restart.
- 🔍 **Warsaw Banking Protection ativo** detectado no header do Erro.log — pode estar hookando ProfitDLL em python.exe (Delphi é trusted/whitelisted). Não fixável do nosso lado.

**Sessão pendente smoke do refactor**: validar live com pregão aberto (1) ticks flowing, (2) watchdog effectiveness, (3) InvalidTickerCallback firing, (4) state callback transitions estáveis. User quer disparar separadamente.

### 2026-05-05 (smoke + bugs operacionais descobertos durante load)

**Smoke real B3** (10:30-13:46 BRT, mercado aberto):
- ✅ 7 SELLs PETR4 100 cada via `auto_trader` em LIVE mode + 7 OCOs BUY anexadas (P0 #3 bilateral validado)
- ✅ Cancel order manual via `/order/cancel` (`ret=0 NL_OK`)
- ✅ Send + Reject manual: SELL qty=20 PETR4 → bloqueado por `infer_lot_size` (P0 #1 prova ativa)
- ✅ Kill switch via DB UPSERT `robot_risk_state.paused=true` → auto_trader respeita em <2s do start
- ✅ Resume kill switch → auto_trader volta a dispatching no próximo ciclo
- ⚠️ Posição final não confirmada via /positions/dll (agent travado por bug #2 — fixado depois)

**5 bugs operacionais descobertos durante carga + fixados na mesma sessão:**

- ✅ **Bug #4 Prometheus target down** — `host.docker.internal` resolvia pra subnet errada (`172.18.0.1` docker bridge vs `172.17.80.1` WSL gateway). Métricas do agent isoladas há quem sabe quanto tempo. Fix: `extra_hosts` IP direto + `volumes !override` no `docker-compose.wsl.yml`. Commit `2845273`.
- ✅ **Bug #5 db_writer batch INSERT** — `INSERT` row-by-row via `_db.execute()` saturava queue=50k cap (~50k ticks/min vs 1.5k/sec da DLL). Fix: refactor pra `psycopg2.extras.execute_values` em batches de 1000 + flush a cada 2s. Validado: queue=0 sustentado em mercado aberto. Esperado 5-10x ganho em backfill. Commit `99c8613`.
- ✅ **Bug #3 zero_position INVALID_ARGS** — `SendZeroPositionV2` retorna `-2147483645` quando ticker não tem posição aberta. Fix: pre-check via `get_positions(env)` skip se net_qty=0; post-check trata `ret=-2147483645` como noop por race. Commit `2e9edd0`.
- ✅ **Bug #2 /positions/dll trava** — `get_positions_dll` fazia UPDATEs de reconcile pra cada ordem (~3000 orders × UPDATE com lock contention contra db_writer batch). Fix: `reconcile=True` (default, mantém comportamento do reconcile_loop background); HTTP handler passa `reconcile=False` (read-only path). Validado: 2.2s response (vs 90s+ timeout). Commit `91a9386`.
- ✅ **Bug #1 dry_run "deadlock"** — não era deadlock, era lentidão invisível: httpx.Client SYNC bloqueando event loop async, timeout 10s × N tickers × M strategies, heartbeat só a cada 10 iters = 10min de silêncio aparente. Fix: timeout 10s→5s, cooldown 30s no `_cache_ts` após failure (evita bombardear endpoint nos N tickers seguintes), `logger.info("auto_trader.iter_start")` em cada ciclo. Commit `c49f2b1`.

**Schema/hardening anteriores no dia (durante smoke):**
- ✅ `local_order_id` INTEGER → BIGINT em `robot_signals_log` + `robot_orders_intent` (smoke real revelou overflow `integer out of range`). ALTER aplicado direto em `market_data` (Timescale). `init_timescale/006` atualizado pra novos containers. Commit `0856ebd`.
- ✅ Hardening logging UTF-8 (`sys.stdout/err.reconfigure errors='replace'`) + try/except top-level em `do_GET/do_POST` do http handler. Defesa em profundidade contra bug similar ao Unicode crash de 04/mai. Commit `a279206`.
- ✅ Fix UnicodeEncodeError em 4 chamadas `log.info` (`→`/`substituído` acentuado) que travaram handler `collect_history` 8h+ na sessão 04/mai noite. Commit `d8edafc`.
- ✅ Script + cron diário pra compactar logs profit_agent (NSSM rotation): 6.874 arquivos → 7 `.gz`, 159 MB liberados. Commit `5fb81b7`.
- ✅ NSSM config persistente: `AppThrottle=30000`, `AppRestartDelay=5000`, `AppExit=Restart`, `PYTHONIOENCODING=utf-8`, `PYTHONUTF8=1`, ACL Stop/Start pro user atual (sem precisar admin daqui pra frente).

**Backfill 2y viabilidade**:
- ✅ DLL Nelogica TEM histórico de 2 anos (validado: WINFUT 06/05/2024 → 559k ticks/h, WDOFUT idem em throughput proporcional)
- ⚠️ Estimativa pré-bug-#5 era ~24 dias 24/7. Com batch INSERT agora deve cair pra 1-2 dias.

### 2026-05-04 noite (P1 refactors + P0 testes)
- ✅ P0 testes 1-5 do audit (16 testes novos em `test_ml_signals_strategy.py`, `test_profit_agent_http.py`, `test_auto_trader_dispatcher::TestClOrdId`) — `3275c8b`
- ✅ **P1 refactor 1/3**: `should_retry_rejection` + `message_has_blip_pattern` em validators.py (22 testes cobrindo 6 codes × 3 patterns) — `82e935d`
- ✅ **P1 refactor 2/3**: `resolve_subscribe_list` em validators.py (10 testes; **fecha P0 #4 subscribe race**) — `82e935d`
- ✅ **P1 refactor 3/3**: `parse_order_details` em validators.py (9 testes; testavel via SimpleNamespace mock sem ctypes) — `82e935d`
- ✅ Suite: 1810 → 1851 testes passando

### 2026-05-04 (smoke success day)
- ✅ `/agent/restart` NameError silencioso fix (handler movido 01/mai sem `_hard_exit` import) — `172dbdc`
- ✅ Expandir trading_msg_cb retry pattern (codes 1,3,5,7,9,24 + 6 padrões blip) — `172dbdc`
- ✅ watch_loop fallback retry quando status=8 silencioso — `172dbdc`
- ✅ Refactor performance: poll 5s→1s, retry 5s→1.5s, max attempts 3→5, routing wait 30s→10s — `172dbdc`
- ✅ Senha SIM truncada nos containers (compose `$utD_$` parsing) — diagnosticado, fix defer P1
- ✅ `ohlc_ingestor` stale image (sem migration `0025_b3_delisted_tickers`) — recreated
- ✅ `cointegration_screen` manual rodado — PETR3-PETR4 cointegrado p=0.0002 hoje
- ✅ Drill kill switch (pause/resume) validado — pause em ≤5 ciclos, resume retoma próximo ciclo
- ✅ `GetOrderDetails` em `order_cb` (pattern oficial Nelogica) — expõe text_message + status + traded_qty real-time. **CHAVE pra descobrir root cause** — `0eca81b`
- ✅ Strategies (`MLSignalsStrategy`/`TsmomMlOverlayStrategy`) respeitam lot_size do ticker (B3 stocks=100) — `ad8d7b0`
- ✅ Seed DB `robot_strategies` capital=50000 + lot_size=100 (R$5k notional ÷ 10% max = R$50k mínimo)
- ✅ **Smoke real B3** — 2 SELLs PETR4 100×R$49,4533 fillados pelo robô + manual close BUY 200 → posição zerada, P&L bruto +R$6,99 (`31a684a`)

### 2026-05-03 (pre-flight)
- Pre-flight smoke 04/mai (rebuild ohlc_1m + dry-run + checklist) — `74317a1`
- Filter weekend rows nos aggregadores ticks→daily/1m — `7e87282`

(Histórico anterior 28/abr → 02/mai consolidado em `docs/historico/sessoes_29abr_01mai.md`.)

## Memórias relevantes (em `~/.claude/projects/.../memory/`)

Lições gravadas que se aplicam DIRETAMENTE a essas pendências:

- `feedback_get_order_details_callback.md` — order_cb DEVE chamar GetOrderDetails (P0 #1 só faz sentido com isso já em pé)
- `feedback_agent_restart_silent_nameerror.md` — sempre validar PID via Win32_Process após /restart
- `feedback_agent_subscribe_boot_race.md` — validar `:8002/status` subscribed_tickers após restart de stack (P0 #4)
- `feedback_worker_image_rebuild.md` — `docker compose up -d` não atualiza código baked; rebuild + force-recreate quando alterar src/ (lição reaprendida hoje 16:42)

**Lições gravadas (sessão 05/mai)** — já em `MEMORY.md`:
- `feedback_compose_wsl_extra_hosts.md`
- `feedback_compose_wsl_volume_override.md`
- `feedback_db_writer_lock_contention.md`
- `feedback_zero_position_no_position.md`
- `feedback_async_event_loop_sync_io.md`

## Convenção

- **P0**: bloqueia próximo smoke real. Resolver antes de retomar trades.
- **P1**: melhora robustez/qualidade. Pode ir junto com P0 mas não bloqueia.
- **Ativas/Roadmap**: longer term, sem prazo curto.
- **Done recente**: últimos ~7 dias; mover pra `docs/historico/` quando ficar > 1 semana.

Atualizar este arquivo no fim de cada sessão.
