# Pendências do projeto — leia primeiro

> **Para Claude/agente:** este é o primeiro arquivo a consultar em qualquer sessão. Contém pendências priorizadas + carryover de sessões anteriores. Atualizar ao fim de cada sessão (mover concluídas pra `## Done recente` e depois pra `docs/historico/`).

Última atualização: **2026-05-04 17:00 BRT** (após smoke success)

## Top priority — pegar antes do próximo smoke real

### P0 — fixes que evitam regressão imediata

- [ ] **Validação local `qty % lot_size == 0` no agent antes de `SendOrder`** — defesa em profundidade. Hoje (04/mai) strategies retornavam qty=20 (lote PETR4=100), broker rejeitou silenciosamente, agent tentava 5 retries inúteis em loop. Strategy fix já aplicado mas validação no agent previne classes inteiras de regressão futura. Local: `_send_order_legacy` linha ~2469 (após valida ticker/qty>0).
- [ ] **Persistir `local_order_id` no `robot_orders_intent` após dispatch** — bug menor: intents 11/12 do smoke 16:47 ficaram com `local_order_id=NULL` apesar das ordens DLL fillarem com sucesso (status=2). Dispatcher.dispatch_order não está fazendo o UPDATE final. Quebra reconcile/audit.
- [ ] **Stop loss / trailing automático nas posições** — hoje sem proteção pós-entry. Hoje no smoke fechei manualmente; em produção real, ml_signals SELL → posição short sem SL = exposure ilimitada. Strategy retorna `take_profit`+`stop_loss` no payload mas dispatcher só cria `/order/oco` se ambos vierem; verificar se ATR levels chegam de fato.
- [ ] **Subscribe race no boot do `profit_agent` (task #6)** — `subscribing_from_db count=0` quando DB conectado mas vazio NÃO faz fallback pra env. Mitigado via `POST /subscribe` manual; fix definitivo: alterar lógica em `profit_agent.py` linha ~928 pra OR(env ∪ DB) ou tratar vazio como invalid.

### P1 — qualidade/robustez

- [ ] **Escapar `$$` no `.env PROFIT_SIM_ROUTING_PASSWORD`** — compose interpreta `$utD_$` como var → senha truncada para `wB#.&5hd!8$`. Irrelevante em sim path (não injeta senha) mas precisa correto antes de production. Trocar pra `wB#.&5hd!8$$utD_$$`.
- [ ] **Lookup automático de `lot_size` por ticker** — hoje hardcoded `100` no config_json. Para futuros (WINFUT/WDOFUT) lote é 1; para BDR alguns são 1, outros 10. Adicionar coluna `tickers.standard_lot` ou tabela de referência. Substitui o context.get("lot_size", 100).
- [ ] **Alerta Grafana se `profit_agent.stderr.log` cresce** — bug `/agent/restart` NameError ficou silencioso 3+ dias porque ninguém olhava stderr. Adicionar regra: stderr > N bytes/hour = critical alert.
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

## Convenção

- **P0**: bloqueia próximo smoke real. Resolver antes de retomar trades.
- **P1**: melhora robustez/qualidade. Pode ir junto com P0 mas não bloqueia.
- **Ativas/Roadmap**: longer term, sem prazo curto.
- **Done recente**: últimos ~7 dias; mover pra `docs/historico/` quando ficar > 1 semana.

Atualizar este arquivo no fim de cada sessão.
