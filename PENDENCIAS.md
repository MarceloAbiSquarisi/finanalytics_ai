# Pendencias — finanalytics_ai
> Atualizado: 2026-04-19 (sessão Sprints 2-10 + fixes)
> Fonte: PENDENCIAS.md original + analise Claude Code do projeto completo

> **IMPORTANTE:** o estado operacional vivo dos sprints R1-R10 está em
> `ESTADO_CONSOLIDADO.md §1.6` e `ESTADO_TECNICO.md`. Este arquivo preserva
> pendências mais antigas (abril/2026). Novas pendências técnicas vão para o
> runbook específico de cada sprint (ex: `Melhorias/runbook_R10_modelos.md`).

---

## 00. Status 19/abr/2026 (2) — pós ordem RF completa

**Sequência E4 → Tier 2 HMM → F9 → E5 → BERTimbau → F8:**

| Item | Entrega | Status |
|---|---|---|
| **E4** Treasuries RF (FRED) | `scripts/fred_ingestion.py` (requests+session+retry), `us_macro_daily` com 12 634 rows, `treasury_rf_mvp.py` RandomForestClassifier direcional 21d. Test acc 54.5%, AUC 0.47 (training set pequeno por HY_spread começar em 2023). | ✅ scaffold |
| **Tier 2 HMM** monetário | `hmm_monetary_cycle.py` (hmmlearn GaussianHMM 3-estados), `hmm_monetary_daily` com 1 338 dias. Rótulos: easing/neutral/tightening (balanceado ~450 cada). Últimos dias alternando easing/neutral (ciclo BR 2024-25). | ✅ |
| **F9** Quality cross-asset | `quality_ntnb_vs_div_yield`, `quality_bank_equity_credit` em `rates_features.py`. Pure compute (features só). | ✅ |
| **E5** DI1 realtime | `di1_realtime_worker.py` — **SCAFFOLD only** (não executar). TODO: subscribe DI1 via ProfitDLL, Kafka `market.rates.di1`. | ⏸️ scaffold |
| **BERTimbau COPOM** | `copom_sentiment.py` — **SCAFFOLD only**. Interface `COPOMSentimentModel` estável. TODO: fine-tune BERTimbau (450MB download, VRAM 1.5GB). | ⏸️ scaffold |
| **F8 DRL** | `drl_env.py` — **SCAFFOLD only**. Observation space (30 RF features), reward function. TODO: Gymnasium Env + PPO stable-baselines3. | ⏸️ scaffold |

Novas tabelas: `us_macro_daily`, `br_macro_daily`, `hmm_monetary_daily`.
Novos ingestores: `fred_ingestion.py`, `sgs_ingestion.py` (BCB 4 814 rows).
Dependências novas: `hmmlearn`.

---

## 0. Status 19/abr/2026 — pós-sessão

**Sprints fechados hoje:** S2 (R4 watchlist), S3 (R2 ohlc_1m), S4 (R3 stride),
S5 (R7 gap_map_1m), S6 (R6 Fintz — bloqueio externo), S8 (R9 dashboards),
S10-scaffold (R10 features_daily + MVP PETR4 IC=0.11).

**Em execução:** Sprint 1 (R1 backfill 2020-hoje) como nssm service
`FinAnalyticsBackfill` — em `ITUB4 2020-06` (última checagem 19/abr 19h BRT;
ETA 3-5 dias wall-clock, competindo com I/O concorrente).

**Pendentes (ordem de prioridade):**

1. **Conclusão Sprint 1** — backfill continuar até 100% (bloqueia R10 completo).
2. **Restart do profit_agent** para ativar `/metrics` Prometheus (endpoint
   implementado em 19/abr mas agent ainda rodando sem restart).
3. **Fintz pós-dez/2025** — contato com Fintz/Varos (dataset `cotacoes_ohlc`
   congelado em 2025-12-30, hash idêntico desde então).
4. **R8 — decisão hospedagem** (input em `Melhorias/proposta_decisao_15_dualgpu.md`).
5. **R5 — decisão VERMELHO_sem_profit** (82 tickers; custo/benefício plano Nelogica).
6. **`profit_daily_bars` quirk de escala — CAUSA IDENTIFICADA (B1, 19/abr/2026)**.
   Não é bug de `populate_daily_bars.py` — os ticks em `market_history_trades`
   coletados ANTES do commit `efba27c` ("fix: remove erroneous /100 division
   in V2 history callback") têm price dividido por 100. `populate_daily_bars`
   só agrega esses ticks → close ~0.49 em vez de 49. Escopo: 64–69 de 72 dias
   para os 8 tickers DLL principais (~95% do histórico 2026-01 → 2026-04-08).
   Diagnóstico via `scripts/audit_profit_price_scale.py`:
   - `FULL_BUG`: 100% dos ticks ÷100 (ex: PETR4 09-10/abr).
   - `MIXED`: Sprint 3 re-coletou e ON CONFLICT DO NOTHING deixou antigos
     ÷100 junto com novos corretos (ex: PETR4 15-16/abr min=0.47 max=48).
   - `ok`: todos corretos (ex: PETR4 13-14/abr re-coletados pós-patch sem
     registros antigos; 17/abr novo).
   **Fix pendente (destrutivo)**: DELETE dos ticks com `price < 5` (stocks
   com mediana_vol > 1M) + re-coleta via `/collect_history` + re-rodar
   `populate_daily_bars.py`. Requer Profit.exe. Estimativa: 30-60 min de
   operação. Script de auditoria read-only já está versionado.
7. **Expansão `features_daily`** para watchlist inteira via
   `python scripts/features_daily_builder.py --backfill --start 2020-01-02`
   (~1h de run).
8. **Endpoint `/predict`** — revisar `routes/forecast.py` e
   `routes/ml_forecasting.py` (já existentes, 1 080 linhas) e garantir que
   expõem o pickle mais recente em `models/`.
9. **Prometheus server** — subir container dedicado para scrape do profit_agent
   `/metrics` (integração Grafana).
10. **`MLStrategy` + `RiskEstimator`** em produção — tudo já existe em
    `application/ml/*`, falta integrar ao pipeline de backtest/serving.
11. **75 datasets Fintz** com hash_unchanged nunca chegaram ao Timescale —
    `TRUNCATE fintz_sync_log` + full sync (~4-6h) se quiser backfill completo
    dos fundamentos/indicadores no Timescale.

**Bugs descobertos que já foram corrigidos:**

- `timescale_writer` rejeitava df bruto em indicadores/itens_contabeis → fix
  via `_ensure_data_publicacao` + `_ensure_tipo_periodo`. 5 datasets reparados
  (13.96 M rows em `fintz_indicadores_ts`).
- `PROFIT_TIMESCALE_DSN=localhost:5433` inválido dentro do container
  `fintz_sync_worker` → override em `docker-compose.yml`.
- `gap_map_1m` cast `dia::date` bloqueava Index Only Scan → usar timestamp
  range direto em queries de grande janela.
- Contagens de `profit_agent_total_contaminations` agora disponíveis via
  `/metrics` (antes só em log).

**Arquivos de orquestração** (vivos):
- `ESTADO_CONSOLIDADO.md §1.6` — changelog R1-R10 detalhado.
- `ESTADO_TECNICO.md` — schemas, queries, patches.
- `PLANO_CLAUDE_CODE.md` — plano 0-10 (roadmap).
- `Melhorias/runbook_R10_modelos.md` — operação ML.
- `docs/historico/` — briefings de sessões 16-17/abr (não-ativos).

---

## 1. Imediatas [DONE]

- [x] `PROFIT_SIM_ROUTING_PASSWORD` — senha real no `.env`, nao eh placeholder
- [x] `POST /order/send` — testado em simulacao, limit buy PETR4 OK (local_order_id=26041317263503)
- [x] Restart automatico — Task Scheduler ja instalado (`FinAnalytics-ProfitAgent-Watchdog`, Running). Porta corrigida de 8001→8002
- [x] SetOrderCallback — codigo alterado para `POINTER(TConnectorOrder)`. Validacao ao vivo pendente de restart do profit_agent

> **ACAO MANUAL**: Reinstalar watchdog com porta correta:
> ```powershell
> # PowerShell como Admin
> powershell -ExecutionPolicy Bypass -File D:\Projetos\finanalytics_ai_fresh\scripts\install_profit_watchdog.ps1
> ```
> **ACAO MANUAL**: Reiniciar profit_agent para ativar SetOrderCallback novo + auto-populate user_account_id

---

## 2. Bloqueadas — aguardando Nelogica (DLL 4.0)

- [ ] Confirmar nova interface de market data streaming na DLL 4.0
- [ ] Implementar `price_depth_cb` — book de precos (`profit_agent.py:2736` — `return` antes do codigo)
- [ ] Testar `daily_cb` — candles diarios (nao testado)
- [ ] Investigar `total_assets=0` — catalogo de ativos nao chegando
- [ ] `_pos_impl` stub vazio (`profit_agent.py:3929`) — callback de posicao registrado mas `pass`

---

## 3. Codigo — bugs e bypasses ativos [DONE]

- [x] `if True:` em `routes/events.py:52` — corrigido: `if not settings.kafka_bootstrap_servers:`
- [x] `if True:` em `routes/events.py:209` — corrigido: `if not settings.kafka_bootstrap_servers:`
- [x] `/docs` (Swagger) — ja funcionando, `from __future__ import annotations` nao esta mais nas routes
- [x] `fintz_sync_service_updated.py` — deletado (identico ao original, ja havia sido copiado)
- [x] `container.py` e `container_v2.py` — removido container.py + .v1.bak (so v2 eh usado)

---

## 4. Sprint U7 — Event Processor [DONE]

- [x] Domain layer: entities, models, exceptions, ports, rules, value_objects
- [x] Application layer: EventProcessorService, factory, config, tracing, rules
- [x] Infrastructure: ORM, repository, mapper, consumer, idempotency, observability
- [x] Hub router: POST/GET /events, GET /stats, POST /events/{id}/reprocess
- [x] Worker: event_worker_v2.py com poll loop async
- [x] Testes: 16 hub tests + 72 event processor tests passando
- [x] mypy limpo, ruff limpo nos arquivos novos
- [x] pyproject.toml: ruff, mypy strict, pytest-asyncio ja configurados

---

## 5b. Follow-ups Sprint UX C (21/abr/2026) — gaps menores

Levantamento UX cobriu 25+ endpoints de mutação user-facing em
DayTrade/Screener/Fundamentalista/ML/Outros. **Cobertura geral 85%**
(22/25 com UI). Gaps restantes (ordem de impacto):

- [ ] **ETF Rebalancer** — `POST /etf/rebalance` (`routes/etf.py:122`)
      calcula COMPRAR/VENDER/MANTER. Sem botao em `etf.html`.
      Impacto: feature invisivel.
- [ ] **Fundos sync** — `POST /fundos/sync/cadastro` + `POST /fundos/sync/informe`
      (`routes/fundos.py:116,122`). Sem pagina propria. `laminas.html` so le.
      Impacto: sync exige curl manual.
- [ ] **ETF Correlation** — `POST /etf/correlation` (`routes/etf.py:100`)
      duplica `correlation.html` generico mas sem UX especifica em `etf.html`.
      Impacto: confusao de navegacao.

## 5. Sprint U8 — Hub frontend + observabilidade [DONE 21/abr/2026]

- [x] Cards dead-letter/failed na pagina `/hub` com botao "Reprocessar" — ja existia (`hub.html` + `POST /hub/events/{id}/reprocess`)
- [x] Metrica Prometheus `finanalytics_dead_letter_total` no Grafana — paineis 15/16 em `data_quality.json` (commit `f8ccd89`)
- [x] Cleanup job: DELETE `event_records` WHERE status terminal AND age > N — `cleanup_event_records_job` no scheduler 23:00 BRT (commit `aab3895`)
- [x] `correlation_id` propagado no tracing cross-service — Kafka producer injeta header + consumer extrai + worker_v2 usa payload.cid (commit `0af1972`)

---

## 6. Multi-conta (sprint dedicada)

- [x] Schema `investment_accounts` (migration 0009)
- [x] `user_account_id` auto-populado no `_send_order_legacy`
- [ ] CRUD API para contas (endpoints REST)
- [ ] Seletor de conta no dashboard
- [ ] Integracao renda fixa com `investment_account_id` na UI `/carteira`
- [ ] Visualizacao de carteiras de outros usuarios na pagina `/admin` (role MASTER)

---

## 7. Backfill historico

- [x] ITUB4 (63 dias)
- [x] PETR4 (63 dias)
- [x] VALE3 (63 dias)
- [x] ABEV3 (68 dias)
- [x] BBDC4 (68 dias)
- [x] WDOFUT (67 dias)
- [ ] WEGE3 (55/70 dias — faltam ~15)
- [ ] WINFUT (13/70 dias — faltam ~57)
- [x] Scripts run_backfill.ps1 e monitor_backfill.ps1 criados

---

## 8. Organizacao do repo [DONE]

- [x] 8 scripts soltos na raiz — todos deletados (nenhum era importado por codigo ativo)
- [x] `container.py.v1.bak` na raiz do src — deletado
- [x] `.bak` e `.spd.bak` em routes/, static/, workers/ — todos deletados
- [x] `static_sidebar_bak/` — diretorio inteiro removido
- [x] Merge-head migration 0013 — ja nao existe

---

## Resumo

| Secao | Pendentes | Status |
|-------|-----------|--------|
| 1. Imediatas | 0 | DONE (2 acoes manuais) |
| 2. Nelogica DLL | 5 | Aguardando resposta |
| 3. Bugs/bypasses | 0 | DONE |
| 4. Sprint U7 | 0 | DONE |
| 5. Sprint U8 | 0 | DONE |
| 6. Multi-conta | 4 | Sprint dedicada |
| 7. Backfill | 2 | Scripts prontos |
| 8. Organizacao | 0 | DONE |
