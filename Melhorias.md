# Backlog de Melhorias — FinAnalytics AI

> Lista priorizada do que ainda está ativo. Itens entregues estão em `git log` + memory.
>
> **Última revisão**: 30/abr/2026 — sessão pós-pregão estendida, 14 commits `5ad447d` → `a7b52aa`.

**Histórico de sprints concluídas** (não re-documentar aqui):
- N1-N12 + N5b/N4b/N6b/N10b + housekeeping A-H — DONE 28/abr madrugada
- M1-M5 + features /diario + S/R + flatten — DONE 27/abr noite
- Bugs P1-P7 + O1 (DLL callbacks, broker auth blips, trail fallback, NSSM zombies) — DONE 28/abr (`27e04d3`, `efc4235`, `568e9a3`, `202bdc3`)
- Snapshot signals + ml_pickle_count fix — DONE 29/abr (`7ad0061`)
- **P9 mitigado** + **P10 fix** + **P11/P11.2 fix** + resilience patterns broker degradado — DONE 29/abr (`3896aeb`, `53372e1`, `b153037`, `ee58c06`, `43f3767`)
- **Sessão 30/abr** (`5ad447d` → `a7b52aa`): OHLC filtro 13-20 UTC + admin rebuild endpoint + scheduler bugs + CI verde + `profit_agent_validators.py` + 20 unit tests + P2-futuros + U1 drag SVG + day-dividers chart + I4 fechado (NSSM AppExit=Restart) + P8 fechado + P9 fase 2 boot-load
- **C5 Passo 7 + Passo 1** (`fdd81f9`, 30/abr pós-pregão): handshake `_source="trading_engine"` + `_client_order_id` no body de `:8002/order/send` → persistência em `profit_orders.source`/`cl_ord_id` + supressão de `_maybe_dispatch_diary` para ordens do engine (evita duplicata na unified VIEW). Smoke parcial validado (PETR4 simulation, 16:57 BRT). Passos 2-6 (VIEW + backend→VIEW + UI pill manual/engine) bloqueados pela migration do engine R-06 — agente agendado `trig_01VDzH3xriAC777KZku42SbK` p/ 21/mai abrir PR pareado. Spec: `c5_handoff_for_finanalyticsai.md`.
- **I3 + I2 done** (30/abr pós-pregão): rebuild `api worker event_worker_v2 scheduler ohlc_ingestor` (~6min — `ohlc_ingestor` estava em loop fail há tempo indeterminado por image pre-27/abr sem migrations 0019-0020) + housekeeping logs legacy (1848 arquivos × 65.7MB → zip 6.44MB em `_archive_logs/`).
- **UI overhaul 29/abr noite** (`0b696f1` → `90acb2e`):
  - Gap compression overnight + fitContent + UNION ohlc (`0b696f1`, `7739298`, `c296006`, `32a65e0`, `71eb1e1`)
  - Bollinger client-side + lookup reverso (`28e41ae`, `c3876db`)
  - 4 indicadores novos: Estocástico Lento + ATR + VWAP + IFR (`20b40d3`)
  - Letter-spacing CSS fix global (`1ca102b`, `da5279c`)
  - Subscribe 373 tickers + futuros B3 (`d6a0aa6`, `6c200ce`)
  - `tick_to_ohlc_backfill_job` DELETE+INSERT diário (`6d1450a`, `37dcdef`)
  - SW v100 + sw_kill.html (`c8e83da`, `8381013`)
  - Carteira: coluna Horário + linha zero rentabilidade (`a070483`, `75697ae`)

---

## 🔄 BACKLOG ATIVO

### UI / Dashboard

#### U1 — Drag-to-modify de linhas de ordem TP/SL no chart ✅ DONE 30/abr (Abordagem A — SVG overlay)

**Solução**: SVG `<svg id="order-handles-svg">` absolute position por cima do canvas dentro de `#chart-price`. Handles renderizados como `<g><rect><text>` com pointer-events auto. Os events vêm direto pra nossos listeners sem briga com canvas interno do lightweight-charts.

**Implementação** (`dashboard.html`):
- `updateOrderHandles()` enumera `orderLines`, filtra TP/SL (skip entries), calcula Y via `priceSeries.priceToCoordinate(price)`. Renderiza handle 70x14px na borda direita (perto do priceScale).
- `_onHandleMouseDown` → captura, salva `_dragState` (refPrice, ids, qty, role, startY, startMouseY).
- `_onHandleMouseMove` (document-level) → atualiza Y do rect/text + label preview ("TP 48.20 ↕").
- `_onHandleMouseUp` → `priceSeries.coordinateToPrice(finalY)` → confirm() → POST `/api/v1/agent/order/change` para cada local_id agregado.
- Subscribe `priceChart.timeScale().subscribeVisibleTimeRangeChange()` re-renderiza handles em pan/zoom (skip durante drag ativo via `_dragState` guard).

**Validação live 30/abr 14:09 (Playwright MCP)**:
- 2 handles SVG renderizados (TP @ 49.20 verde, SL @ 47.50 vermelho)
- Drag físico TP → 47.50 disparou: confirm dialog + POST /change → DB price 49.20→47.50 → broker fillou (atravessou mercado) @ 48.60 → cross-cancel SL automático → group `completed`
- Toast: `"TP movido para R$ 47.50 (1/1)"`

**Limitação**: drag só cobre TP/SL. Entry orders simples ainda usam o ✕ + recriar (caso de uso menos comum).

### ML & Sinais

#### Z5 — Multi-horizonte ML (h3/h5/h21) ⭐⭐ aguarda Nelogica
**Custo**: ~1d com pickles + ensemble. **Payoff**: alto (reduz dependência de h21 único).

`predict_ensemble` já existe (Sprint Backend Z4) mas só faz fallback uniforme — pickles h3/h5 não treinados (precisa `ohlc_1m` completo). Quando Nelogica chegar (item 20 do backlog Pendências em CLAUDE.md), treinar.

**Sintoma atual** (29/abr): `ml_drift_high` alert firing porque 145/157 configs calibrados não têm pickle (só 12 tickers tem MVP h21). Resolver = treinar pickles para os 145 restantes. Bloqueado em dados Nelogica.

#### N4-HMM — HMM real para RF Regime ⭐ baixa prioridade
**Custo**: ~3d (lib hmmlearn + treino dos estados + tuning). **Payoff**: marginal sobre o Markov empírico atual.

M5 atual usa regras determinísticas + Markov empírico (entregue N4/N4b 28/abr). HMM permitiria descobrir regimes empíricos + transições probabilísticas. Vale só se houver evidência de que regras determinísticas perdem regimes intermediários relevantes.

#### N6-MH — Crypto multi-horizonte (h1/h6) ⭐ médio
**Custo**: ~2d. **Payoff**: médio (timing de aporte BTC, mas só 1 holding hoje).

Hoje `/api/v1/crypto/signal/{symbol}` é daily. Para sinais multi-horizonte intraday:
- Worker que persiste OHLC CoinGecko (5min) em `crypto_ohlc_5m`
- Indicadores em h1/h6/h24 separadamente
- Endpoint `/crypto/signal/{symbol}/{horizon}`

#### N10 — ML para FIDC/FIP ⭐ futuro
**Custo**: ~2d. **Payoff**: nichado.

M3 entregou peer ranking + style + anomalies para Multimercado/Ações/RF/FII. Estender para FIDC/FIP requer adaptações (estrutura de cota diferente, distribuições periódicas, vencimento das CCBs).

---

## 🤖 Robô de Trade (R1-R5)

#### R1 — auto_trader_worker (execução autônoma de sinais ML) ⭐⭐⭐ alto payoff
**Custo**: ~5-10d MVP (1 strategy + risk + UI básica). **Payoff**: alto (transforma sinais ML calibrados em retorno realizado sem intervenção manual).

90% da infra já existe — sinais ML, OCO multi-level, trailing, GTD, flatten_ticker, prometheus, alert rules. Falta só o "executor" que liga sinal → ordem.

**Arquitetura**:
```
auto_trader_worker (container novo, asyncio)
├─ Strategy Loop (cron 1m/5m/15m, configurável)
│   1. Fetch /api/v1/ml/signals
│   2. Para cada Strategy.evaluate() ativa:
│      a. Risk check (size, DD, max posições, correlation cap)
│      b. ATR-based entry/SL/TP
│      c. POST /agent/order/send + attach OCO
│      d. Log em robot_signals_log
├─ Strategy Registry (plugin) — class Strategy(Protocol).evaluate(...)
├─ Risk Engine
│   - Vol target (sigma 20d) → position size
│   - Kelly fracionário 0.25x
│   - Daily P&L tracker (DB + cache)
│   - Circuit breaker DD>2% intra-day
│   - Max N posições por classe
└─ Kill switch
    - Flag DB robot_risk_state.paused
    - Auto-pause em latência>5s, 5 errors/min
    - Manual via PUT /api/v1/robot/pause
```

**Tabelas novas** (`init_timescale/006_robot_trade.sql`):
- `robot_strategies (id, name, enabled, config_json, account_id, created_at)`
- `robot_signals_log (signal_id, ticker, action, computed_at, sent_to_dll, local_order_id, reason_skipped)`
- `robot_orders_intent` (separado de `profit_orders` — distingue manual×automático)
- `robot_risk_state (date, total_pnl, max_dd, positions_count, paused, paused_at)`

**MVP fim-de-semana**: schema + 1 strategy (R2) + risk vol-target + UI read-only `/robot` + kill switch.

**Status R1 (01/mai/2026)**:
- ✅ R1.1 schema `robot_trade` (4 hypertables) — commit `4acab4a`
- ✅ R1.2 worker scaffold asyncio + dry run — commit `cd738cc`
- ✅ R1.3+R1.4 endpoints + UI `/robot` (read-only + kill switch) — commit `22b5b65`
- ✅ R1.5 Risk Engine (vol-target, ATR, gates) + dispatcher real (POST `/agent/order/send` via proxy `:8000` + handshake C5 `cl_ord_id` deterministico + auto-OCO se TP+SL) + `MLSignalsStrategy` consome `/api/v1/ml/signals`. 38 unit tests verde — commit `53b7c58`
- ✅ R2 TSMOM ∩ ML overlay — `TsmomMlOverlayStrategy` herda `MLSignalsStrategy`, fetch único bars 1y, filtro `sign(ret_252d)` concordante com ML antes de tradar. 10 unit tests novos, 48/48 robot suite verde — commit `8d1c350`
- 🔲 **Smoke test live (próximo pregão)**: subir `auto_trader_worker` em simulação com `AUTO_TRADER_ENABLED=true` + `AUTO_TRADER_DRY_RUN=false`. Pode rodar 1 das 2 strategies (`ml_signals` ou `tsmom_ml_overlay`) p/ 2-3 tickers líquidos (PETR4, VALE3). Validar: signal_log populado com `sent_to_dll=true`, `robot_orders_intent` com `local_order_id` recebido do DLL, OCO atrelado quando TP+SL gerados, kill switch `PUT /api/v1/robot/pause` interrompe novas entradas em <1 ciclo, ordem aparece em `profit_orders` com `source='auto_trader'` + `cl_ord_id` ecoado. Pré-req: seed em `robot_strategies` com config_json (tickers + capital_per_strategy + momentum_lookback_days p/ R2). Defer pra 02/mai (sex pregão) ou 05/mai (seg).

#### R2 — Strategy: TSMOM ∩ ML overlay ✅ DONE 01/mai (commit `8d1c350`)
**Custo**: ~3-5d dentro do R1. **Payoff**: alto (filtro de regime grátis sobre ML existente).

Combina sinal ML calibrado h21d com filtro de momentum 12m (Time Series Momentum, Moskowitz/Ooi/Pedersen 2012). Posição = `sinal_ML × sign(ret_252d) × vol_target`. Quando ML e momentum concordam → full size; divergem → skip. Reduz whipsaws do ML em mean-reverting regimes.

**Implementação real (01/mai)**:
- `TsmomMlOverlayStrategy` em `domain/robot/strategies.py` — herda `MLSignalsStrategy` (reusa `_fetch_signal` cache 60s, sizing/ATR levels). Override `evaluate`.
- Fetch on-the-fly de bars 1y (`range_period='1y'`, ~252 bars) p/ momentum + vol + ATR num só roundtrip. **Não persistiu coluna `momentum_252d_sign` em `signal_history`** (decisão pragmática: zero migration/job; refator pra coluna quando R3 também precisar). Trade-off: +1 chamada `/marketdata/candles` por ticker/ciclo, mitigado pelo TTL 60s na MLSignals.
- Lookback configurável em `config_json.momentum_lookback_days` (default 252).
- Registry: `'tsmom_ml_overlay': TsmomMlOverlayStrategy()` em `auto_trader_worker.STRATEGY_REGISTRY`.
- 10 unit tests (`tests/unit/domain/test_robot_strategies.py`): concordance BUY/SELL, disagree skip, neutral momentum skip, HOLD passthrough sem fetch, bars insuficientes, custom 60d lookback. Helpers usam ruído senoidal determinista p/ produzir vol > 0 nos bars sintéticos.
- ✅ **Polish 01/mai noite (commit `a565667`)**: `MLSignalsStrategy._fetch_bars` agora delega pro `HttpCandleFetcher.fetch_bars` (extraído em `e53d676`). `httpx.Client(...)` inline removido — DRY com worker pairs flow. Construtor aceita `candle_fetcher` kwarg p/ injeção em testes. 5 unit tests novos no fetcher (last-N bars, per-call range_period, fallback `candles` key, HTTP/empty errors). 1636/1636 unit suite verde.

**Edge documentado**: TSMOM tem Sharpe 0.7-1.2 cross-asset desde anos 80, replicado em B3 (Hosp Brasil). ML solo + overfitting risk; sobreposição reduz drawdown.

#### R3 — Strategy: pares cointegrados B3 ⭐⭐ market-neutral
**Custo**: ~5-7d. **Payoff**: médio-alto (Sharpe 1-1.5 histórico, beta-neutral reduz risco macro).

Bancos (ITUB4/BBDC4/SANB11/BBAS3) e Petro (PETR3/PETR4) cointegrados há 10+ anos. Engle-Granger test rolling 252d, Z-score do spread → entrada `|Z|>2`, saída `Z<0.5`, stop `|Z|>4`. Capacidade limitada (R$1-5M por par) mas suficiente para conta pessoal/proprietária pequena.

Implementação:
- Job diário `cointegration_screen.py`: testa todos pares em watchlist, persiste em `cointegrated_pairs` (rho, half_life, last_test)
- Strategy.evaluate roda no tick monitor (não no signals): quando |Z| cruza threshold, dispara 2 ordens OCO paralelas
- Risk: stop |Z|>4 force-close; cointegração quebra (p-value > 0.05) → marca par como "inativo"

**Pitfalls**: cointegração quebra em regime change (2008/2020 quebrou vários). Re-test rolling obrigatório.

**Status R3 (01/mai/2026)**:
- ✅ R3.1 Engle-Granger screening (offline) — commit `419cb73`. Schema `cointegrated_pairs`, domain puro (`compute_hedge_ratio`/`compute_residuals`/`adf_test`/`compute_half_life`/`engle_granger`), CLI `scripts/cointegration_screen.py`. 18 unit tests verde com data sintética (par OU mean-rev cointegrado detectado, random walks independentes não).
- ✅ Validação live 01/mai (8 tickers x 28 pares contra `fintz_cotacoes_ts` até 2025-11-03):
  - **252d → 0 cointegrados**. PETR3/PETR4 (rho=0.99) p=0.28 — janela curta + regime instável 2024-25 (Selic 13.75→11.25, mudanças governança Petrobras).
  - **504d → 2 cointegrados**: `CMIN3-VALE3` (p=0.045, β=0.094, half-life=27d, fundamento mineração) e `SANB11-VALE3` (p=0.039, half-life=22d, **provável spurious** — 28 testes a p=0.05 → ~1.4 falsos positivos esperados por sorte).
- 🔲 **R3.2 deve aplicar Bonferroni** (`p_threshold = 0.05 / N_pairs ≈ 0.0018`) ou exigir filtros adicionais antes de tradar: half-life razoável (5-30d) + fundamento econômico (mesmo setor/subsetor). CMIN3-VALE3 passa esses filtros; SANB11-VALE3 não.
- ✅ R3.2.A pure decision logic (commit `024b4b9`): `compute_zscore`, `PairThresholds` (validates 0<exit<entry<stop), `decide_pair_action` state machine (NONE→OPEN, OPEN→CLOSE/STOP, |Z|>stop sempre força STOP), `apply_bonferroni` + `validate_pair_filters` (Bonferroni p<alpha/N + half-life ∈ [5d,30d]). 33 unit tests verde, incluindo caso real CMIN3-VALE3 corretamente rejeitado por Bonferroni (p=0.045 vs alpha_eff=0.0018).
- ✅ R3.2.B.1 service layer (commit `151abad`): `ActivePair` + `PairEvaluation` entities, `PsycopgPairsRepository.get_active_pairs` (filtro `cointegrated=TRUE AND last_test_date >= today-7d`), `evaluate_active_pairs` orchestra repo+filtros+candles+zscore+decisão. Protocol-based DI (`PairsRepository`/`CandleFetcher`/`PositionState`). 15 unit tests com stubs.
- ✅ R3.2.B.2 worker integration (commit pendente): `dispatch_pair_order` em `auto_trader_dispatcher` faz 2 chamadas sequenciais a `/agent/order/send` com `cl_ord_id` `pairs:{pair_key}:{a|b}:{action}:{minuto_iso}` determinístico. Naked-leg detection: se leg B falha após leg A executou → `naked_leg='a'` no return + log error (caller faz alert). Worker tem `_evaluate_pairs(iteration)` env-gated (`PAIRS_TRADING_ENABLED=false` default), `_pair_positions: dict[pair_key, PairPosition]` in-memory, `_HttpCandleFetcher` adapter. Sizing dual-leg: metade do `PAIRS_CAPITAL_PER_PAIR` em cada side (dollar-neutral approx). Para CLOSE/STOP, sides revertem `current_position`. 9 dispatcher tests verde + 123 regression total.
- ✅ **R3.2.B.3 COMPLETO** (01/mai): persistência infra + worker integration + Pushover naked_leg alert. Alembic 0024 + `PsycopgPairPositionsRepository` (9 unit tests). Worker `_evaluate_pairs` usa `positions_repo` direto. OPEN dispara `repo.upsert(pair_key, position, last_cl_ord_id=cl_a)`; CLOSE/STOP dispara `repo.delete(pair_key)`. Sobrevive restart. Naked leg → `notify_system(critical=True)` siren + priority=1 com pair_key + erro no body. 18 unit tests da handler integrada cobrindo todas as branches (happy paths + naked_leg + 4 cenários de notify_fn).
- ✅ **R3.3 UI `/pairs`** completa (commit `5e2afc0+` schema/routes; H 01/mai late: Z-scores real-time): `pairs.html` lista (1) posições abertas, (2) z-scores atual de cada par com classificação semafórica (verde |z|<exit, neutro, gold |z|≥entry, vermelho |z|≥stop), (3) tabela completa de pares cointegrados ativos. Routes:
  - `/api/v1/pairs/active` (LEFT JOIN com robot_pair_positions)
  - `/api/v1/pairs/positions`
  - `/api/v1/pairs/zscores?lookback_days=60` — calcula on-the-fly via `compute_residuals` + `compute_zscore` (funções puras), busca closes via `request.app.state.market_client` (Decisão 20 fallback chain). Retorna `bars_age_days` p/ audit data freshness.
  Auto-refresh 30s, sidebar entry com ícone.
- 🔲 **R3 smoke live** (próximo pregão): `PAIRS_TRADING_ENABLED=true` + `AUTO_TRADER_DRY_RUN=false`. Pré-req: `cointegration_screen.py --persist --lookback 504` populou `cointegrated_pairs`; `PAIRS_CAPITAL_PER_PAIR=10000` razoável p/ smoke. Validar: `pairs.evaluation` logs cada ciclo, OPEN dispara 2 ordens em `profit_orders` com `cl_ord_id` `pairs:*`, position state atualiza, CLOSE reverte legs corretamente.
- ✅ **Operacional** (01/mai): scheduler job `cointegration_screen_job` agendado 06:30 BRT (= 09:30 UTC) via `coint_screen_loop` em `scheduler_worker.py`. Skip weekend (dentro do job via `_is_weekday`). Env vars: `SCHEDULER_COINT_SCREEN_{ENABLED,HOUR,MIN,LOOKBACK}` (defaults: true / 6 / 30 / 504). Subprocess timeout 5min. Validado live: scheduler container restartado, logs `scheduler.coint_screen.start_loop hour=6 min=30` + `next_utc=2026-05-02T09:30:00+00:00`. Próxima execução real: seg 04/mai 06:30 BRT.

#### R4 — Strategy: Opening Range Breakout WINFUT ⭐⭐ futuros
**Custo**: ~7-10d. **Payoff**: alto se filtros funcionam (Sharpe ~1.5 documentado em ES, replicável em WIN).

Range dos primeiros 5-15min após 09:00 BRT. Rompimento + volume confirmação → entrada com OCO. Stop = outro lado do range, alvo 1R/2R + trailing chandelier (3*ATR). Funciona porque institucional gringo replica overnight gap em pregão BR.

Filtro adicional usando DI1 realtime (já implementado): só opera quando slope DI1 estável (sem repricing macro abrupto). Setup completo em <30s de pregão (7 candles 1m + filtro 1 cruzamento DI1).

**Edge**: Zarattini & Aziz 2023 (SSRN) documentou edge persistente em S&P futures. Replicação em WINFUT viável dado liquidez similar.

#### R5 — Backtest harness (vectorbt-pro / próprio) ⭐⭐ multiplica produtividade
**Status (30/abr/2026)**: harness base já existe há tempo (`domain/backtesting/{engine,optimizer,multi_ticker,strategies}.py` + 4 services: backtest, walkforward, optimizer, multi_ticker) cobrindo walk-forward (rolling+anchored), grid search, equity curve, drawdown, profit factor, Calmar, multi-ticker. Aprimoramentos pós-pregão 30/abr:
- ✅ **Slippage por classe de ativo** (`domain/backtesting/slippage.py`): futuros (WDO/WIN/IND/DOL/DI/CCM/BGI/OZM) cobram 2 ticks/lado em valor absoluto; ações cobram 0.05% por lado. Aplicado em `run_backtest` via `apply_slippage_model=True` default. Trade.entry_price/exit_price registram preço efetivo.
- ✅ **Deflated Sharpe Ratio** (`domain/backtesting/metrics.py`): correção López de Prado 2014 para multiple testing bias do grid search. `OptimizationResult.deflated_sharpe` traz `{deflated_sharpe, prob_real, e_max_sharpe}` sobre o melhor candidato. Implementação canônica: SR_0 = sigma(SR_hat) × f(N), com f(N) = (1-γ)Φ⁻¹(1-1/N) + γΦ⁻¹(1-1/(Ne)). Probit Beasley-Springer-Moro sem dependência de scipy.
- 31 unit tests novos (slippage 13 + DSR 18) + 199 tests de regressão verdes.

**Follow-up fechado 30/abr noite** (`3c60baa` + `978482e`):
- ✅ ~~DSR aplicado também no `WalkForwardService`~~ — DONE. Por fold OOS (`WalkForwardFold.oos_dsr` com `num_is_trials` como N e `len(oos_bars_slice)-1` como T) + agregado (`WalkForwardResult.deflated_sharpe` traz `avg_z`, `avg_prob`, `folds_real`, `total_trials`). Persist em `walkforward_service._persist` com strategy `wf:<name>` pra não colidir com runs grid_search. 12 unit tests `test_walkforward_dsr.py`.
- ✅ ~~Tabela `backtest_results` com `config_hash` para histórico comparativo~~ — DONE 30/abr pós-pregão (Alembic 0021 + `infrastructure/database/repositories/backtest_repo.py` + `compute_config_hash` SHA256 + UPSERT idempotente; demo script ganha flag `--persist`; 17 unit tests SQLite). Validado live: PETR4 RSI re-run UPDATE (created != updated), VALE3 MACD insert único.
- ✅ ~~Slippage calibrável por liquidez (small caps merecem mais que 0.05%)~~ — DONE 30/abr noite (`978482e`). Sqrt-impact ADV-aware: `slippage_pct = base_pct × sqrt(trade_size_pct_adv)` capado em 5x base. Configurável via `apply_slippage_model={"liquidity_aware": True, "adv": ADV_USD}`. 13 unit tests `test_slippage.py` cobrindo small/mid/large caps e cap.
- ✅ ~~Endpoint `/api/v1/backtest/history` para listar runs persistidos via UI~~ — DONE 30/abr noite (`3c60baa`). GET `/history` (list + filter por ticker/strategy + paginação), GET `/history/{config_hash}` (drilldown completo com `full_result_json`), DELETE `/history/{config_hash}`. UI `backtest.html:2456-2535` consome endpoint com filtros. 20 unit tests `test_backtest_history_routes.py` + 6 `test_backtest_history_persist.py`.

**Único item R5 ainda aberto**:
- Survivorship bias check (precisa lista de tickers delistados B3 no DB — coleta CVM/B3 ainda não feita).

### Pitfalls comuns que matam robôs amadores (referência)

Checklist obrigatório para qualquer strategy nova:
1. **Look-ahead bias**: usar fechamento do dia D pra decidir trade no D. Sempre `t-1` em features ou abertura D+1.
2. **Slippage subestimado**: WDOFUT/WINFUT em horário ruim custa 1-2 ticks ida+volta. Sem slippage realista, backtest é fantasia.
3. **Overfitting**: grid search infinito de parâmetros. Walk-forward + deflated Sharpe obrigatório.
4. **Survivorship bias**: ações deslistadas somem do dataset.
5. **Regime change**: estratégia 2010-2020 morre 2022. Revalide em janelas rolling.
6. **Leverage sem hedge**: futuro alavanca 10x natural; gap noturno zera conta sem stop.

---

## 📧 Pipeline de research / notas (E1-E3)

> Stack disponível: pdfplumber já no projeto, Anthropic SDK (Claude Haiku 4.5), Pushover. Fonte de dados a ser definida (abordagem Gmail descartada).

#### E1 — Research bulletins → tags por ticker → enrich signals ⭐⭐⭐ alpha real
**Status**: classifier pronto (`ResearchClassifier`, Haiku 4.5 + prompt caching) + worker scaffold (`email_research_worker`) com `ResearchFetcher` Protocol. Aguardando fonte de dados p/ implementar fetcher concreto.

**Custo restante**: ~2-3d (fetcher + parsing + smoke). **Payoff**: alto (research institucional dirige preço em ações líquidas; event study 1-3d pós-publicação).

LLM (Haiku 4.5) classifica corpo da mensagem:
```json
{ticker_mentions: [...], sentiment: BULLISH|NEUTRAL|BEARISH,
 action_if_any: BUY|HOLD|SELL, target_price: 52.0,
 time_horizon: "1-3 meses"}
```

**Storage**: `email_research (msg_id, ticker, sentiment, target, source, received_at, raw_text_excerpt)`.

**Enrich**:
- `/api/v1/ml/signals` ganha campo `research_overlay` (sentiment majority período 5d)
- `/dashboard` card mostra badge "📰 BTG: BUY @52" abaixo do badge ML
- Painel novo "Research Recente" lista últimos 50 com filtro por ticker

**Custo LLM**: Haiku 4.5 $0.80/M input. ~50 mensagens/dia × 2k tokens × 30d = **~$2.40/mês por user**. Cache aggressive (msg_id hash → resultado).

#### E2 — Notas de corretagem → reconciliation automática ⭐⭐ compliance
**Custo**: ~3d MVP por corretora. **Payoff**: médio (IR + confiança em fills + auditoria).

Parse PDF (pdfplumber) das notas das corretoras:
- Extrai `(ticker, side, qty, preco_medio, taxa_corretagem, taxa_emolumentos, irrf, data_pregao)`
- Match com `profit_orders` por `(ticker, side, qty, ±5min, ±0.5%)`
- Se não conciliado: alert "❌ ordem em PDF da corretora sem match no DB" → revisão manual

**Storage**: `brokerage_notes (note_id, broker, pdf_url, parsed_at, total_taxes, total_irrf, reconciled)` + `brokerage_note_items (note_id, ticker, side, qty, price, fees)`.

**UI**: aba "Notas" em `/movimentacoes` lista + filtro por status. **Valor secundário**: cálculo automático de IR + DARF mensal.

#### E3 — Pipeline genérico de mensagens (fundação multi-uso) ⭐ infra
**Custo**: ~7d. **Payoff**: alto SE tiver 3+ casos de uso futuros.

Schema base (`messages` + `message_attachments`), worker de sync configurável, parsers plugáveis (PDF/HTML/LLM), API `/api/v1/messages` paginada + busca full-text, UI dedicada. Habilita E1+E2+casos futuros.

**Quando atacar**: depois que E1 estiver maduro e aparecer 3º caso de uso (ex: alertas de margin call ou tag de eventos corporativos).

### Riscos a documentar antes de começar (E1/E2/E3)
1. **Privacidade/LGPD**: mensagens podem ter dados sensíveis (saldos, CPF, posições). Storage criptografado em rest. Acesso restrito ao próprio `user_id`. Retenção config (deletar > 6 meses).
2. **Rate limits da fonte**: depende da API/protocolo. Worker precisa rate limit configurável.
3. **Auth refresh**: tokens normalmente expiram; planejar fluxo de re-auth com notificação (Pushover).
4. **LLM cost runaway**: cache aggressive (msg_id hash → resultado), só re-classify quando body muda. Skip mensagens já parseadas.
5. **False positives no parser**: validar com sample manual antes de gravar `email_research` automatic. Threshold de confiança (LLM retorna `confidence: 0-1`).

### Recomendação de ordem

| # | Item | Custo | Quando |
|---|---|---|---|
| 1 | E1 fetcher (1ª fonte) | 2-3d | Quando fonte definida — alpha visível |
| 2 | E1 expansão (fontes adicionais) | +2d cada | Depois da 1ª fonte validada |
| 3 | E2 (notas corretagem) | 3d | Quando IR/compliance virar prioridade |
| 4 | E3 fundação | 5-7d | Só se aparecer 3º caso de uso |

---

## 🐛 Bugs descobertos

#### P8 — Broker simulator rejeita ordens em futuros (WDOFUT/WINFUT) ✅ FECHADO 30/abr (transient broker degradação 29/abr)

**Status**: re-validado 30/abr 10:50 BRT — broker aceita normalmente. Cenários:
- Limit BUY 1 WDOFUT @ 4985 → status=0 NEW ✓
- Market BUY 1 WDOFUT → fillou @ 4987 ✓ (B.19 retry hoje)
- Attach OCO em parent pending → group active + cancel ✓

**Causa raiz**: degradação transient do broker simulator Nelogica em 29/abr (mesma janela em que P9/P11 também afetaram com errors P1+P9 stack). Não é bug do código — fix foi recovery natural do simulator.

---

#### P8 (histórico) — original report 29/abr
**Custo**: investigação ~1h (depende de doc Nelogica). **Payoff**: desbloqueia testes Bloco B com futuros antes de 10h (equity opening).

**Sintoma**: 100% das ordens em WDOFUT (limit @ 4900, limit @ 4960, market) rejeitadas pelo broker simulator com `trading_msg code=5 status=8 msg=Ordem inválida`. P1 auto-retry funciona corretamente (validado live em 014021→014022→014023, todas com mesmo erro). Market data (ticks/quotes) funciona normal — `WDOFUT @ 4990.5` fluindo via subscribed_tickers.

**Diferenças observadas**:
- PETR4 (ações) última ordem com sucesso (status=2 FILLED) em 28/abr — broker aceita ações
- WDOFUT (futuros) — todas rejeitadas em 29/abr, mesmo com routing_connected=true e login_ok=true
- Símbolo `WDOFUT` é alias profit_agent que resolve corretamente para market data; pode não resolver para o roteamento
- `sub_account_id=NULL` em todas as ordens (incluindo PETR4 que funcionou) — não parece ser causa

**Hipóteses**:
1. Conta de simulação Nelogica **não tem permissão de futuros habilitada** — checar com Nelogica
2. Broker exige código contrato mensal específico (`WDOK26` p/ mai/26) em vez do alias `WDOFUT`
3. Sub-account distinta necessária para futuros (BMF vs Bovespa)
4. Problema temporário do simulator (mesmo padrão de degradação visto 28/abr 16h)

**Próximos passos**:
1. Verificar com Nelogica se conta sim tem permissão BMF/futuros
2. Tentar ordem com símbolo de contrato específico (`WDOM26` ou ativo do mês)
3. Documentar `account_type` e `exchange` esperados no `trading_accounts` para futuros

**Workaround**: usar PETR4 ou outras ações pra todos os testes Bloco B até resolver.

#### P2-futuros — DB não reflete `status=8` do broker (P2 fix não pegou pra rejeições "Ordem inválida") ✅ DONE 01/mai
**Status (01/mai/2026)**: ✅ FECHADO. **Causa raiz**: `_db_worker.trading_result` handler usava `WHERE local_order_id = X OR cl_ord_id = Y`. Quando broker rejeita futuro com `code=5 status=8` ("Ordem inválida") E `r.OrderID.LocalOrderID = 0` (struct callback corrompida em alguns codes) E `_msg_id_to_local` sem mapping (in-memory, perdido em restart NSSM), ambos identifiers chegavam vazios → handler skip + status stuck em 10. **Fix**: extraído `compute_trading_result_match(local_id, cl_ord, message_id)` puro em `profit_agent_validators.py` que adiciona `OR message_id = X` no WHERE. `profit_orders.message_id` já era persistido em `insert_order` desde o início — só faltava o handler usar. Skip apenas se TODOS os 3 vazios. 9 unit tests novos cobrem caso P2-futuros + boundaries (negative IDs, whitespace cl_ord, todas combinações de identifiers). 29 tests verde (regression OK).

---

#### P10 — OCO legacy `/order/oco` perdia pares pós-restart ✅ DONE 29/abr 16:30
**Status**: hipótese inicial errada. `_oco_pairs` JÁ era populado em `send_oco_order` (linhas 4093-4108). **Causa raiz real**: dict in-memory não persistia através de restart NSSM, deixando SL órfão se TP fillasse após restart.

**Fix aplicado (commit deste teste)**:
1. `send_oco_order` agora gera SL com `strategy_id=f"oco_legacy_pair_{tp_id}_sl"` — codifica pareamento TP→SL no campo do DB.
2. Novo `_load_oco_legacy_pairs_from_db` scan `profit_orders` por padrão `LIKE 'oco_legacy_pair_%_sl'` AND status pendente; reconstrói `_oco_pairs[tp_id]` + `_oco_pairs[sl_id]` no boot.
3. Boot chama load antes do `_oco_monitor_loop` processar primeiro tick.

**Validação live 29/abr 16:30**:
- POST `/order/oco` PETR4 → TP+SL no book, oco_status=ativo
- Restart agent → log: `oco_legacy.loaded pairs=1` + `profit_agent.oco_legacy_pairs_loaded n=1`
- `/oco/status/{tp_id}` retorna `ativo` pós-restart ✅
- Change TP → fillou → log: `oco.filled local_id=... type=tp → canceling pair ...` + `oco_monitor.removed ids=[tp,sl] remaining=0`
- SL auto-canceled em <1s (B.3 fechado também)

#### P9 — DB stuck em status=10 mesmo após cancel/fill confirmado pelo broker ✅ MITIGADO 29/abr + EXTENSÃO 30/abr boot-load
**Status**: callback raiz não foi corrigido (impossível com a DLL atual — ver comentário expandido em `order_cb`), mas mitigação operacional cobre 100% dos cenários práticos.

**Mitigação fase 1 (commit `b153037` 29/abr)**: `_watch_pending_orders_loop` thread.
- `_send_order_legacy` registra `local_id` em `self._pending_orders`.
- Loop varre @5s: chama `EnumerateAllOrders` (reusa reconcile UPDATE).
- Se DLL enumera com status final, watch remove do registry.
- Se DLL não enumera + DB stuck pendente após 60s → marca `status=8` `error='watch_orphan_no_dll_record'`.
- Após 5min, remove do registry mesmo se ainda pending (last-resort).

**Mitigação fase 2 (commit `98a5e20` 30/abr)**: `_load_pending_orders_from_db` no boot.
- Pre-popula `_pending_orders` com ordens em status (0,1,10) das últimas N horas (env `PROFIT_WATCH_LOAD_HOURS=24` default).
- Sobrevive restart NSSM — antes, registry in-memory zerava e órfãs ficavam fora do watch até cleanup_stale 23h BRT.

**Validação live 30/abr 11:36 pós-restart**:
- `watch_pending_orders.loaded n=12 hours=24` — 12 ordens carregadas do DB
- 10 órfãs detectadas e marcadas `status=8` em **<1 segundo**:
  - `watch.order_orphaned local_id=126042914321588 age=75843.1s ticker=PETR4`
  - 9× `watch.order_orphaned ... ticker=WDOK26 age=76366-83700s` (idades 21-23h)
- DB pending residual = 4 (ordens >24h fora da janela de boot-load → caem no cleanup_stale 23h BRT)

**Fix definitivo (descartado 30/abr)**: tentamos avaliar callback-based fix mas a DLL Profit não fornece status final via callback (`order_cb` só dá identifier 24B; `trading_msg_cb` só dá estágios de roteamento — Accepted/Rejected, nunca FILLED/CANCELED). O teto técnico é polling. Comentário expandido em `order_cb` pra evitar futuras tentativas infrutíferas.

#### P11 — Aba Pos. dashboard mostra futuros como "Zerada" ✅ DONE 29/abr 14:08
**Fix aplicado** (commits pendentes):

1. `profit_agent.py:get_position_v2` — detecta `ticker in FUTURES_ALIASES` ou prefix `(WDO|WIN|IND|DOL|BIT)`, força `exchange="F"` e chama `_resolve_active_contract()`. Loga `position_v2.alias_resolved alias=X contract=Y exchange=F`.
2. `dashboard.html:loadDLLPosition` — regex client-side `/^(WDO|WIN|IND|DOL|BIT)/` injeta `exchange=F`; defensive contra response sem campos numéricos (502/error JSON); mostra `WDOK26 (alias WDOFUT)` quando alias foi resolvido.

**Validado live 29/abr 14:08**:
- WDOFUT via UI → `WDOK26 (alias WDOFUT) · Compras 6×R$5000.75 · Vendas 6×R$5001.00` (sessão B.2/B.6/B.8 hoje, +R$15 brutos confere)
- WDOK26 direto via UI → mesma resposta
- PETR4 (regressão) → `— Zerada · 0` mantido OK
- Curl backend `WDOFUT` exchange=B (input antigo) → resposta retorna `WDOK26 exchange=F` ← auto-corrige em qualquer caller

**Sintoma original (29/abr 13:35 B.4)**: UI envia exchange=B + alias WDOFUT → DLL devolve struct zerada (DLL silently aceita combinação inválida). Crash JS `r.open_avg_price.toFixed undefined` quando 502 retornava body sem campo `error`.

**P11.2 (extensão 14:21)** — `/order/flatten_ticker` tinha o mesmo gap: buscava pending por ticker original (WDOFUT) mas DB grava resolved (WDOK26 — `_send_order_legacy` rewrites). Resultado: `pending_found=1` (apenas stuck antigas) em vez do real. Fix:
- Novo endpoint `GET /resolve_ticker/{ticker}?exchange=F` no profit_agent expõe `_resolve_active_contract` (retorna `{original, resolved, exchange, is_future}`)
- `agent_flatten_ticker` no proxy: detecta prefix `(WDO|WIN|IND|DOL|BIT)`, chama `/resolve_ticker`, usa `resolved` em busca de pending + zero_position. Retorna `original_ticker` na resposta
- `flattenTicker()` na UI passa `exchange='F'` para futuros (defesa em profundidade)
- Validado live 14:21: `pending_found=12` (vs 1 antes), 4/12 cancels aceitos pela DLL — broker rejection nos demais por P1 blip, não código

## 🛠 Infra

#### I4 — `/agent/restart` não restartava o agente ✅ FECHADO 30/abr (causa real: NSSM AppExit=Exit)

**Causa raiz REAL** (descoberta 30/abr 12:11 via diagnóstico):
`nssm get FinAnalyticsAgent AppExit Default` retornava **`Exit`** em vez do default **`Restart`**. Por isso quando o processo Python morria via `_hard_exit` → `TerminateProcess`, o NSSM detectava o exit mas NÃO restartava — service ficava `Stopped` exigindo `Start-Service` manual.

**`TerminateProcess` SEMPRE funcionou** (hipótese original estava errada): o diagnóstico (commit `cdc9349`) capturou `hard_exit.attempt` em 2 tentativas seguidas, ambas SEM `hard_exit.terminate_failed`, confirmando sucesso da chamada nativa.

**Fix aplicado** (PowerShell elevado):
```powershell
& nssm set FinAnalyticsAgent AppExit Default Restart
```

**Validação live 30/abr 12:17**: ciclo completo `/agent/restart` em **9 segundos** (15:17:02 dispatch → 15:17:11 agent UP) — sem intervenção manual:
- PID 78012 → `hard_exit.attempt pid=78012 code=0`
- TerminateProcess succeeded (sem terminate_failed)
- NSSM detected exit, AppExit=Restart triggered
- PID novo 55484 spawned em ~2s
- watch_pending_orders.loaded n=0 hours=24 — boot OK

**Por que estava `Exit`**: provavelmente config inicial do NSSM (talvez setup manual antigo). Não é default — fresh install do NSSM usa `Restart` por padrão.

**Lição**: o diagnóstico expandido em `_hard_exit` (commit `cdc9349`) provou-se útil mesmo com hipótese original errada — eliminou TerminateProcess como suspeito e direcionou pra config NSSM. Manter os logs para diagnose futura.

---

#### I4 (histórico) — sintomas e diagnóstico parte 1

**Sintoma**: chamar `POST /api/v1/agent/restart` (com sudo válido) retorna `{ok:true,message:"restarting"}` mas o processo Python não morre. PID continua o mesmo (validado: PID 116820 com `creation=29/abr 18:41` mantido após /restart de 30/abr 14:22).

**Causa raiz hipotética**: `_hard_exit` chama `TerminateProcess(GetCurrentProcess(), 0)` via `kernel32`. Em serviço NSSM rodando como Local System com a conta atual sem permissão de "Process Termination" sobre o próprio handle (Windows ACL stricta), `TerminateProcess` falha silenciosamente. O `try/except` cai em `os._exit(0)` que CLAUDE.md já documentou: "não termina processo limpo — DLL ConnectorThread C++ bloqueia."

**Validação adicional**:
- `Stop-Process -Id <pid> -Force` por user não-admin → `Acesso negado`
- nssm restart pelo CLI por user não-admin → `OpenService(): Acesso negado`
- Único caminho hoje: PowerShell elevado (Run as Administrator) → `Restart-Service FinAnalyticsAgent -Force`

**Diagnóstico aplicado** (commit 30/abr — parte 1):
- `_hard_exit` agora tem `log.warning("hard_exit.attempt pid=...")` ANTES e `log.error("hard_exit.terminate_failed last_error=...")` quando `TerminateProcess` retorna 0.
- Restypes corretos: `GetCurrentProcess: HANDLE`, `TerminateProcess: HANDLE+UINT→BOOL`, `GetLastError: DWORD`. Antes era ctypes default (int) e qualquer crash silenciava.
- Próximo `/restart` que silenciosamente falhar deixará pista clara no log.

**Resta** (parte 2 — opcional):
- Mecanismo alternativo se diagnóstico confirmar `last_error=5` (ERROR_ACCESS_DENIED): gravar stop_marker + watchdog script externo elevado.

**Workaround atual**: `/agent/restart` continua útil para rebuild de in-memory state (loops vão re-popular), mas ciclo de processo Python não rotaciona. Para deploy de novo código no profit_agent: PowerShell elevado manual.

---

#### I3 — Rebuild containers stale (após pregão 29/abr) ✅ DONE 30/abr pós-pregão
Rebuild de `api worker event_worker_v2 scheduler ohlc_ingestor` em ~6min. Bug bonus encontrado: `ohlc_ingestor` estava em loop `Restarting (255)` há tempo indeterminado com erro `Can't locate revision identified by '0020_diario_is_complete'` — image antiga (pré-27/abr) não tinha as migrations 0019-0020. Rebuild resolveu. Validado: `profit_agent.py` mtime 30/abr 19:41 (commit C5) + `di1_realtime_worker.py` mtime 30/abr 13:03 nos containers, todos healthy.

**Achado 29/abr 09:34**: containers tem código defasado (file mtime dentro do container):
- `finanalytics_worker` — di1 worker datado **20/abr** (perde 8d de fixes, incluindo P3 cursor)
- `finanalytics_worker_v2` — event_worker_v2 datado **3/abr** (~mês de defasagem)
- `finanalytics_api` — workers datados **21/abr** (perde fixes 28/abr noite: P1-P7+O1, snapshot_signals job, ml_metrics_refresh path fix)
- `finanalytics_scheduler` ✅ — workers datados 28/abr (rebootado 29/abr 06:47)
- `finanalytics_di1_realtime` ✅ — hot deploy P3 fix realizado 29/abr 09:18

**Causa**: hot deploys via `docker cp` aplicados em alguns containers mas image não foi rebuilt. Próximo `compose up` (sem build) usa image antiga.

**Comando**:
```bash
docker compose build api worker worker_v2
docker compose up -d api worker worker_v2
```

**Quando**: pós-fechamento pregão (17h+). Não fazer minutos antes/durante pregão (api restart causa ~5s downtime no dashboard).

**Não bloqueante hoje**: containers estão saudáveis pra observação/leitura. Funcionalidades dependentes dos fixes recentes (snapshot_signals job, ml_pickle_count fix) já foram hot-deployed onde necessário.

#### I2 — Finalizar rotação log profit_agent (após pregão 29/abr) ✅ DONE 30/abr pós-pregão
`RotatingFileHandler(maxBytes=10MB, backupCount=10)` já estava ativo em `profit_agent.py:_setup_logging` (linha 162). Housekeeping pós-pregão: 1848 arquivos `profit_agent-2026XXXXX.log` legacy (65.7MB total, gerados antes do switch para RotatingFileHandler) zipados em `_archive_logs/profit_agent_legacy_pre_rotate_20260430.zip` (6.44MB, ratio 10x) e removidos do `logs/`. Pasta `logs/` agora limpa.

#### I1 — Migrar Docker Desktop → Docker Engine direto via WSL2 🔄 EM ANDAMENTO 01/mai
**Status (01/mai/2026)**:
- ✅ **Fase A** concluída: Docker Engine 29.4.2 + Compose Plugin instalados em Ubuntu-22.04 WSL2 com systemd. NVIDIA Container Toolkit 1.19.0 configurado. Validações passaram: hello-world OK, GPU passthrough OK (2x RTX 4090 listadas, mapeamento PCIe 01:00.0/08:00.0 idêntico ao validado em Decisão 15). Comportamento `nvidia-smi` listar 2 GPUs mesmo com `CUDA_VISIBLE_DEVICES=0` é peculiaridade conhecida (CLAUDE.md), isolamento real continua via libs CUDA.
- ✅ **Fase B.1 COMPLETA** (01/mai 16h): cutover end-to-end funcional. 17 containers up no Engine WSL2, DBs preservadas (28 cointegrated_pairs, 884 fintz tickers, alembic ts_0004 + 0023). Container → `host.docker.internal:8002` → `{"ok":true}`. Windows `localhost:8000` → `{"ok":true}` (WSL2 port forward auto). Smoke segunda 11h vai rodar contra esse stack. **Comandos Docker agora via** `docker context wsl-engine`: dockerd config `tcp://127.0.0.1:2375` em `daemon.json` + `/etc/systemd/system/docker.service.d/override.conf` resolvendo conflito `-H` flag. Context criado: `docker context create wsl-engine --docker host=tcp://127.0.0.1:2375`. Trocar com `docker context use wsl-engine` (atual) ou `docker context use default` (Docker Desktop, vazio). `docker ps`/`docker compose` no PowerShell agora apontam direto pro Engine WSL2 — sem prefix `wsl --`.
- ✅ **Bloqueador host networking** resolvido em 3 mudanças complementares:
  1. `profit_agent.py` bind `0.0.0.0:8002` por default (env `PROFIT_AGENT_BIND` p/ override). Funciona em Docker Desktop **e** Engine WSL2. NSSM service restartado.
  2. Regra firewall Windows inbound permitindo TCP 8002 de `172.17.80.0/20` (subnet WSL):
     ```powershell
     New-NetFirewallRule -DisplayName "Profit Agent WSL Inbound" -Direction Inbound -LocalPort 8002 -Protocol TCP -Action Allow -RemoteAddress 172.17.80.0/20 -Profile Any
     ```
  3. `docker-compose.wsl.yml` extra_hosts usa **IP direto** `host.docker.internal:172.17.80.1` (não `:host-gateway`). Engine WSL2 puro resolve `host-gateway` pra docker bridge interna (`172.18.0.1`), não pro Windows host. WSL gateway IP é estável dentro de uma sessão WSL mas pode mudar após `wsl --shutdown` ou reboot Windows — verificar com `wsl -d Ubuntu-22.04 -- ip route show default`.
- ✅ **Pendência paralela** (resolvida 01/mai): imagens `finanalytics-ai:latest` e `finanalytics-worker:latest` foram rebuildadas pra incluir migrations 0021/0022/0023/ts_0004. **Diagnóstico**: build COM cache funciona (~5min), build `--no-cache` falhou transient em pip install torch+prophet (re-download 2GB) — não é bug do Dockerfile, só network glitch ocasional. Para futuras falhas similares: re-tentar build SEM `--no-cache` antes de investigar fundo. Containers api/worker/scheduler/ohlc_ingestor recreated com nova imagem; `/api/v1/agent/health = {"ok":true}` validado.
- ✅ **Fase B.2 COMPLETA** (01/mai 21h): volumes Postgres + Timescale migrados pra ext4 nativo WSL (`~/finanalytics/data/`). Cópia: Postgres 36GB → 7m24s; Timescale 183GB → 43m05s. Total downtime ~50min. Sizes batem (du -sh confirmou). chown 999:999 aplicado. `docker-compose.wsl.yml` atualizado com paths novos. Validação live: 28 cointegrated_pairs preservados, 884 fintz tickers, alembic 0024+ts_0004 OK, robot tables OK. `/signals` 2.0-2.3s warm (igual pré-migração). Heavy query `COUNT(*) FROM fintz_cotacoes_ts` (1.32M rows) em 427ms. **Backups originais em `/mnt/e/finanalytics_data/docker/{postgres,timescale}` mantidos por 1 semana** como rollback (delete após 08/mai se nada quebrar).


**Custo**: ~1-2d (investigação + migração de volumes). **Payoff**: médio (operação 24/7 mais robusta + sem dependência de user logado).

**Motivação**: Docker Desktop hoje morre quando o user faz logoff do Windows. Pra setup que precisa rodar 24/7 (api/scheduler/timescale/grafana/alerts/snapshots/jobs), isso é frágil. Docker Engine instalado direto numa distro WSL2 roda como systemd service — independente de sessão de user.

**Outros ganhos colaterais**:
- Sem GUI overhead (Docker Desktop come ~500MB RAM mesmo minimizado)
- Sem licença Docker Desktop (não obrigatória pra uso pessoal/<R$10M, mas por princípio)
- Mais "server-like" (libera futuro hop pra Linux dedicado/colocation sem mudar workflow)

**Plano**:
1. Instalar Ubuntu/Debian em WSL2 (`wsl --install -d Ubuntu`)
2. Instalar `docker-ce` + `docker-compose-plugin` + `nvidia-container-toolkit` na distro
3. Habilitar systemd no `/etc/wsl.conf` + `systemctl enable docker`
4. **Decisão de volumes** (crítica — NTFS bind via `/mnt/d/` é 10-50x mais lento que ext4 nativo):
   - **Opção A**: mover todos volumes (TimescaleDB, Postgres, Grafana, Prometheus, Redis) pra dentro do filesystem WSL (`~/finanalytics/data/`). Performance ótima, mas backup/inspeção fora do WSL fica menos prática.
   - **Opção B**: deixar volumes em `/mnt/d/` (mesmo path atual). Performance ruim — inviável pra TimescaleDB ingestão de ticks live.
   - **Recomendado**: A (migrar volumes pra ext4 WSL).
5. Stop Docker Desktop, validar Engine WSL2 sobe os mesmos containers via `docker compose up -d`
6. Verificar `nvidia-smi` dentro container (Decisão 15 ainda vale — NVIDIA Container Runtime funciona idêntico)
7. Depois de 1 semana estável: uninstall Docker Desktop

**Riscos / pegadinhas**:
- **Volume migration downtime**: parar TimescaleDB, copiar `pgdata`, restartar. ~30min por volume grande. Fazer fim-de-semana.
- **profit_agent permanece no Windows host** (NSSM service). `host.docker.internal` continua funcionando dentro do Engine WSL2 via configuração equivalente (precisa testar — em WSL2 puro o nome resolve diferente).
- **Sem UI Docker Desktop** pra inspecionar containers — usar `lazydocker` ou `ctop` no terminal compensa.
- **`docker context` switch** durante transição: dá pra coexistir Docker Desktop + Engine WSL2 com contexts separados, validar antes de migrar de vez.
- **Backup pré-migração obrigatório**: snapshot completo do volume Postgres+Timescale antes de mexer.

**Quando atacar**: quando aparecer 1ª vez que o Docker Desktop "morreu" em situação ruim (user logoff acidental, update Windows reboot mal-timed). Hoje funciona — não fazer migração preventiva sem dor real, mas deixar documentado.

**Alternativa mais radical** (não atacar agora): migrar containers pra Linux server dedicado (NUC/mini-PC barato, ou colocation) — desliga Windows do caminho crítico de produção. Faz sentido quando a operação virar realmente production-grade ou multi-user.

---

## Notas

- **Próxima sprint sugerida** (02/mai+): smoke live R1.5+R2+R3.2 no pregão de 04/mai (segunda) — routine `trig_013JvZLcbANEuRf8rSYiFhK5` agendada 11h BRT roda re-screening cointegração + tail dos logs do auto_trader. Depois: E1 fetcher (quando fonte de dados definida) ou R4 (ORB WINFUT, ~7-10d).

### Sessão 02/mai sábado pré-smoke — 12 commits, 6 bugs latentes corrigidos
Detalhamento em `memory/project_session_02mai_full.md` + `docs/runbook_alembic_audit.md`. Pontos vinculantes:
- **Padrão UNION cross-source** (Decisão 24): apareceu 4x na sessão. Pipelines lendo `fintz_cotacoes_ts` direto ficaram stale 6mo após Fintz freeze (2025-11-03). Fix em série: `/candles_daily` endpoint + `cointegration_screen.load_closes` + `features_daily_builder.load_bars` + `yield_curves_refresh_job` (novo). Ver Decisão 24 em CLAUDE.md.
- **PETR3-PETR4 emergiu como par real** após cointegração rodar com dados atuais (p=0.0002 << α/28). Antes do fix, robô estava cego ao par real — testava SANB11/VALE3 + CMIN3/VALE3 (falso-positivos).
- **Decisão 23 — alembic ts_*** registry-only**: 4 tabelas robot_* zumbi DROPped em Postgres. ts_* migrations rodam DDL contra Postgres por design quebrado; tabelas Timescale reais vêm de `init_timescale/*.sql`.
- **Bug raiz `rates_features_builder`**: filtrava series histórica pelo --start/--end → range curto = TSMOM/value NULL silently. Fix em commit `208650d`.
- **Pipeline ANBIMA automatizado**: `yield_curves_refresh_job` 21h BRT diário (yield_ingestion + rates_features_builder com lookback 400d). Antes era manual; última execução manual 17/abr → ML signals retornavam feature_nulls 15 dias depois.
- **ML signals counts pós-fix**: 14 BUY / 1 SELL / 26 HOLD / 116 errors. PETR4 SELL agora real (predicted=-0.60% < th_sell=0.0, ref 2026-04-30) — antes era falso (features de nov/2025).
- **Dependência crítica**: Z5 (treinar pickles h3/h5) bloqueado em dados Nelogica.
- **Operação atual**: estável em Docker Engine WSL2 (Decisão 22). Volumes Postgres/Timescale em ext4 nativo (Fase B.2 done 01/mai). Backups originais `/mnt/e/finanalytics_data/docker/{postgres,timescale}` ficam até ~08/mai antes de delete.

---

_Criado: 26/abr/2026_
_Última edição: 29/abr/2026 (cleanup agressivo pré-pregão)_
