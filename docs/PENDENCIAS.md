# Pendências do projeto — leia primeiro

> **Para Claude/agente:** este é o primeiro arquivo a consultar em qualquer sessão. Contém pendências priorizadas + carryover de sessões anteriores. Atualizar ao fim de cada sessão (mover concluídas pra `## Done recente` e depois pra `docs/historico/`).

Última atualização: **2026-05-06 12:30 BRT** (sessão fechada com agent stopped — root cause externo)

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

- [ ] **Aguardando arquivo Nelogica 1m** → rodar `docs/runbook_import_dados_historicos.md`. Inclui treinar pickles h3/h5 para `predict_ensemble` multi-horizon real (hoje só h21 existe).
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
