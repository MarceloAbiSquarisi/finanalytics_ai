# FinAnalyticsAI — Estado Consolidado (17/abr/2026)

> Documento vivo. Consolida o que já foi feito, o que está rodando agora, e as melhorias planejadas com suas dependências. Sucessor da série de briefings pontuais (`CLAUDE_01.md`, `briefing_claude_code_pos_sessao_16abr.md`, `plano_proximos_passos_17-19abr.md`, `sessao_pausa_17abr_0450.md`).

---

## Sumário

- Parte 1 — Overview Executivo
  - 1.1 Visão e objetivo do produto
  - 1.2 Arquitetura em camadas
  - 1.3 Estado das fases (0–4)
  - 1.4 O que está rodando agora
  - 1.5 Destaques da sessão 17/abr/2026
  - 1.6 Roadmap de melhorias (R1–R10) e suas necessidades
- Parte 2 — Apêndices Técnicos
  - A. Modelo de dados TimescaleDB
  - B. profit_agent: bugs, patches, endpoints
  - C. Pipeline Fintz e gap handling
  - D. Watchlist canônica (Fase 0): critérios e tiers
  - E. Scripts de backfill: inventário e uso
  - F. Runbooks operacionais
  - G. Consultas SQL de referência
  - H. Paths, serviços e portas

---

# PARTE 1 — OVERVIEW EXECUTIVO

## 1.1 Visão e objetivo

FinAnalyticsAI é uma plataforma de análise quantitativa do mercado acionário brasileiro voltada a trading sistemático e research. O objetivo é manter uma base de dados histórica completa e de alta granularidade (tick-by-tick para ativos líquidos) que alimente modelos, backtests e dashboards próprios, sem depender de vendors caros em produção.

As fontes primárias são: (i) ProfitDLL (Nelogica) para ticks históricos e tempo real do mercado à vista brasileiro e futuros B3; (ii) Fintz para cotações diárias (EOD) agregadas e metadados fundamentalistas. O foco inicial é construir 6 anos de profundidade (2020–hoje) para ~135 stocks líquidos + contratos cheios de mini-índice e mini-dólar (WINFUT, WDOFUT).

## 1.2 Arquitetura em camadas

Resumida em prosa: na camada de persistência, um TimescaleDB (Postgres + extensão) roda em Docker local (porta 5433) e hospeda tanto o banco transacional de ticks (`market_data`) quanto a tabela de watchlist canônica. Acima dele fica o `profit_agent`, uma FastAPI que embrulha a ProfitDLL como um serviço HTTP local em `localhost:8002`, normalizando callbacks V1/V2 e gravando ticks em `market_history_trades` com `ON CONFLICT DO NOTHING`. Acima disso ficam os scripts de backfill (Python) e diagnóstico (PowerShell + SQL) no repositório `D:\Projetos\finanalytics_ai_fresh`. Uma pasta paralela em `D:\Investimentos\FinAnalytics_AI\Melhorias` serve de workbench: briefings, scripts .ps1 auxiliares, SQLs de auditoria e planos de fase. O fluxo é: ProfitDLL → profit_agent → TimescaleDB (ticks) → continuous aggregate `ohlc_1m` → consumidores (notebooks, backtests, dashboards futuros).

## 1.3 Estado das fases (0–4)

**Fase 0 — Watchlist canônica (concluída):** a tabela `watchlist_tickers` é a fonte de verdade sobre quais ativos o sistema monitora. Critério: mediana diária de `volume_negociado` > R$ 500k no período nov/2024 a nov/2025, ≥ 150 dias de cobertura em Fintz. Exclusões: IBOV (índice), BRFS3 e MRFG3 (fundiram em MBRF3), NTCO3 (delisted). Inclusão manual: MBRF3 (só 30 dias de Fintz por ser fusão recente, mas cobertura Profit completa). Resultado: ~135 tickers líquidos distribuídos em quatro tiers (VERDE / AMARELO_parada_recente / AMARELO_coleta_fraca / VERMELHO_sem_profit), ordenáveis por `mediana_vol_brl DESC`. Detalhes e DDL em Apêndice D.

**Fase 1 — Agregação OHLC 1m (concluída para 2025; re-run parcial pendente):** o continuous aggregate `ohlc_1m` deriva candles de 1 minuto diretamente de `market_history_trades`. A política de refresh é incremental. A re-agregação dos 4 dias 2026-04-13 a 04-16 está **adiada** até o backfill histórico terminar, porque esses dias sofreram contaminação do patch antigo e precisam ser re-coletados antes de re-agregar (o que já foi feito em 17/abr — vide 1.5). SQL em `aggregation_ohlc_1m_from_ticks.sql` e `fase1_backfill_ohlc_1m.sql`.

**Fase 2 — Sync Fintz refinado (pendente):** pipeline P23 para sincronizar Fintz com o estado canônico da watchlist. Hoje o `fill_fintz_gap.ps1` supre gaps manualmente. O refinamento envolve mapear faixas de datas atrasadas por ticker e disparar fetchs em lote respeitando quotas.

**Fase 3 — Mapeamento de gaps por ticker (em andamento):** já há scripts de diagnóstico (`diag_fintz_atrasados_readonly.ps1`, `diag_fintz_gap_readonly.ps1`) que produzem inventário de gaps. Falta consolidar em uma view `gap_map_1m` que junte Profit + Fintz por ticker × data e aponte exatamente onde falta cobertura em cada fonte.

**Fase 4 — Backfill histórico 2020–hoje (em andamento, HOJE):** universo = watchlist (~135 stocks) + futuros (WINFUT, WDOFUT). Período: 02-jan-2020 a hoje, ~1.500 pregões. Script `backfill_historico_watchlist.py` pronto, resiliente (retry 3×, graceful shutdown via signals, idempotente via ON CONFLICT). Falta: instalar nssm, criar o serviço via `setup_backfill_service.ps1`, dar start.

## 1.4 O que está rodando agora (snapshot 19/abr/2026 ~20h BRT)

**TimescaleDB** — container `finanalytics_timescale`, `localhost:5433`, user `finanalytics`, banco `market_data`. Armazena ticks 2020–2026-04-17 para 135 tickers (parcial — backfill em curso), 1.32 M bars Fintz diárias (2010→2025-12-30, estagnada lado Fintz), 270 405 linhas em `gap_map_1m` (Sprint 5), 9 489 linhas em `profit_daily_cov` (Sprint 5+4), 13.96 M rows em `fintz_indicadores_ts` (pós-fix Sprint 6), features_daily com 1 457 rows PETR4 (Sprint 10 scaffold).

**profit_agent** — `localhost:8002`, BaseHTTPRequestHandler embrulhando a ProfitDLL. Endpoints principais: `GET /status|/metrics|/ticks`, `POST /collect_history|/subscribe|/unsubscribe|/order/*`. Desde 17-abr com patch-bundle contra as 5 contaminações. **Endpoint `/metrics` Prometheus adicionado em 19/abr (precisa restart do agent para ativar).** Pré-requisito: Profit.exe logado.

**Backfill histórico (Sprint 1)** — `FinAnalyticsBackfill` nssm service **em execução**. Universo: 135 tickers da watchlist VERDE+AMARELO + WINFUT/WDOFUT, 2020-01-02 → hoje. Última checagem 19/abr ~20:00 BRT: `ITUB4 2020-06` (1.5 % do universo; ETA 3–5 dias wall-clock, competindo com queries concorrentes). Logs em `Melhorias/logs/backfill_historico_{stdout,stderr}.log`. Graceful stop via `setup_backfill_service.ps1 stop`.

**Renda fixa (RF Tier 1-2)** — stack funcional pós-sessão 19/abr:
- `yield_curves`: 40 413 rows ANBIMA (br_pre+br_ipca) + 4 719 rows FRED (us_treasury) = ~45 k rows, 2020→2026-04.
- `rates_features_daily`: 1 567 dias × 36 features (slope/butterfly/TSMOM/carry/value/NS/FRA/V+M).
- `hmm_monetary_daily`: 1 338 dias classificados easing/neutral/tightening.
- `us_macro_daily` (7 915 rows) + `br_macro_daily` (4 814 rows): macro FRED+BCB SGS.
- Schedule: ANBIMA via `yield_ingestion.py` idealmente 20:30 BRT (não automatizado ainda); FRED e SGS manual. Automatização = follow-up do scheduler_worker.

**Fintz sync worker** — schedule diário 22:05 BRT embutido. Full sync 19/abr gravou 5 datasets (75 em skip por hash inalterado). Gap estrutural: `fintz_cotacoes_ts.max(time) = 2025-12-30` congelado do lado da Fintz/Varos (requer contato com vendor — R6).

**Grafana** — `localhost:3000` (admin / finanalytics2026), container `finanalytics_grafana` 10.4.0, rede `finanalytics_net`. Datasource `TimescaleDB` (UID `afjkz1gl2ky68e`) configurado. Dashboard `FinAnalytics AI — Qualidade de Dados` (UID `finanalytics-data-quality`) com 3 painéis: cobertura 30d, latência por ticker, candles/min hoje. JSON em `Melhorias/grafana_dashboards/qualidade_dados.json`.

**Outros containers Docker** (2 dias up): `finanalytics_api`, `finanalytics_worker`, `finanalytics_scheduler`, `finanalytics_ohlc_ingestor`, `finanalytics_worker_v2`, `finanalytics_zookeeper`, `finanalytics_kafka`, `finanalytics_kafka_ui`, `finanalytics_redis`, `finanalytics_redisinsight`, `finanalytics_postgres`, `finanalytics_pgadmin`, `finanalytics_backup`, `finanalytics_evolution`.

**Nada de produção externa ainda** — tudo local. Decisão de hospedagem (R8) em aberto — input em `Melhorias/proposta_decisao_15_dualgpu.md`.

## 1.5b Sessão 19/abr/2026 (2) — RF Tier 1-2 + MLStrategy prod + debug

Segundo bloco da sessão após fechar S2-S10. Foco em renda fixa, ML produção e scaffolds para expansões futuras.

**Entregas:**

| Item | Entrega | Status |
|---|---|---|
| RF F1 | Backfill histórico `yield_curves` via pyield: 1 567 dias úteis × LTN+NTN-B = **40 413 rows** `yield_curves` + **22 504 rows** `breakeven_inflation` (2020-01-02 → 2026-04-17). | ✅ |
| RF F2-F6 | Funções puras em `application/ml/rates_features.py`: `tsmom_signal` (Moskowitz 2012), `carry_ntnb_over_cdi`+`carry_roll_down` (Koijen 2018), `value_zscore`+`value_breakeven_vs_focus` (Asness 2013), `value_momentum_combined`, `butterfly_duration_neutral`+`fra_implied` (Litterman & Scheinkman 1991). Validadas em dados reais DI1 1Y (614 dias hist). | ✅ |
| RF F7 | `rates_features_daily` (tabela materializada, 36 campos, 1 567 rows) + `scripts/rates_features_builder.py`. View `features_daily_full` (JOIN features_daily + RF). MVP v2 (`train_petr4_mvp_v2.py`): cross-asset features elevaram **val Sharpe de -0.28 para +1.57** e val IC 0.059 → 0.080 (test com variância 2025+). | ✅ |
| A3 | `scripts/mlstrategy_backtest.py` — pipeline end-to-end QuantileForecaster (P10/P50/P90) + score MLStrategy + `engine.run_backtest`. PETR4 th=0.10: Sharpe 0.402 / +2.12% / 1 trade. Scaffold validado; calibração por ticker e walk-forward são follow-up. | ✅ scaffold |
| E4 | Treasuries RF via FRED: `scripts/fred_ingestion.py` (requests+session+retry vs urllib bloqueado por Kaspersky). Tabela `us_macro_daily` (DFF/CPI/VIX/T5YIE/T10YIE/BAMLH0A0HYM2) + Treasuries em `yield_curves market='us_treasury'`: **12 634 rows**. `treasury_rf_mvp.py` RandomForest 500 trees. Test acc 54.5% (N=728; HY_spread só desde 2023 limita train). Feature importances consistentes com literatura: slope_3m_10y (0.28) e slope_2y_10y (0.22) dominam. | ✅ scaffold |
| Tier 2 HMM | BCB SGS via `scripts/sgs_ingestion.py` (SELIC over, CDI, IPCA mensal, PTAX): **4 814 rows** em `br_macro_daily`. `scripts/hmm_monetary_cycle.py` (hmmlearn GaussianHMM 3-estados): **1 338 dias classificados** em easing/neutral/tightening (balanceado ~450 cada). Tabela `hmm_monetary_daily`. Últimos dias alternando easing/neutral (ciclo BR 2024-25). | ✅ |
| F9 | `quality_ntnb_vs_div_yield` e `quality_bank_equity_credit` em `rates_features.py`. Pure functions. | ✅ |
| E5 | `workers/di1_realtime_worker.py` — **SCAFFOLD only**. TODO: subscribe DI1 via ProfitDLL + Kafka `market.rates.di1`. | ⏸️ scaffold |
| BERTimbau COPOM | `application/ml/copom_sentiment.py` — **SCAFFOLD only**. Interface `COPOMSentimentModel`. TODO: fine-tune BERTimbau (450 MB, VRAM 1.5 GB). | ⏸️ scaffold |
| F8 DRL | `application/ml/drl_env.py` — **SCAFFOLD only**. `RATE_OBSERVATIONS` (30 features) + `reward_fn`. TODO: Gymnasium + PPO. | ⏸️ scaffold |
| **B1** (descoberto) | Bug escala `profit_daily_bars` (valores 0.4↔49). Causa: ticks pré-`efba27c` em `market_history_trades` têm price ÷100. Script `audit_profit_price_scale.py` classifica dias em `ok`/`FULL_BUG`/`MIXED`. **Fix destrutivo não executado** (requer DELETE + re-coleta via Profit.exe). | ✅ audit / ⏸️ fix |
| `/predict_mvp` | `routes/predict_mvp.py` — endpoint `GET /api/v1/ml/predict_mvp/{ticker}` valida via HTTP (PETR4 retorna log_ret=0.00073). Complementar ao `/forecast` existente. | ✅ |
| `features_daily` | Backfill watchlist inteira via `--backfill --start 2020-01-02`: **171 473 rows × 133 tickers × 1 457 dias** (fonte `fintz_cotacoes_ts`). | ✅ |

**Novas dependências:** `pyield>=0.48.9`, `hmmlearn==0.3.3`, `requests` (implícita via pyield).

**Novas tabelas TimescaleDB:**
- `yield_curves` (pré/IPCA/Treasury)
- `breakeven_inflation`
- `rates_features_daily`
- `us_macro_daily`, `br_macro_daily`, `hmm_monetary_daily`
- View `features_daily_full` (JOIN features_daily + rates_features_daily)

**Novos scripts:** `yield_ingestion`, `fred_ingestion`, `sgs_ingestion`, `rates_features_builder`, `train_petr4_mvp_v2`, `mlstrategy_backtest`, `treasury_rf_mvp`, `hmm_monetary_cycle`, `audit_profit_price_scale`.

## 1.5a Sessão 19/abr/2026 — Sprints 2–10 fechados

Dia intenso com 7 sprints fechados (sequenciais e paralelos ao Sprint 1 em execução). 7 commits em `origin/master` (`fcc9a06`, `d32eef6`, `8ecdc53`, `fab7e46`, `2173f18`, `f756f84`, `c71ab13`). Todos validados com DoDs do plano (exceções documentadas).

**Entregas:**

| Sprint | R | Entrega | Status DoD |
|---|---|---|---|
| S2 | R4 | BBDC3 → VERDE; BPAN4/GUAR3 → VERMELHO_sem_profit (delisting confirmado via `/collect_history` em 17/abr = 0 ticks). Distribuição final: 124 VERDE, 9 AMARELO_coleta_fraca, 0 AMARELO_parada_recente, 82 VERMELHO_sem_profit. | ✅ |
| S3 | R2 | Re-agregação `ohlc_1m` 2026-04-13..16 via fork `fase1_backfill_ohlc_1m_range.sql`. 46k candles/dia × 133 tickers. Sanity PETR4 09:30 bateu exato vs ticks (open/high/low/close/volume/trades idênticos). Descoberta: `ohlc_1m` é hypertable comum (não CAgg) — docs corrigidos. | ✅ (passo 6) / ⚠️ passo 5 (30 tickers VERDE baixa liquidez com <300 candles — esperado) |
| S4 | R3 | `ticker_stride` (215 stocks + WIN/WDO, todos stride=10 empiricamente). View `cobertura_diaria_v2`. Resultado 13-16/abr: stocks 100%, futuros 0.6-14.6 % — fórmula `(tn_max-tn_min)/stride` super-estima futuros (gaps largos no trade_number). Migrado para métrica temporal em S5. | ✅ stocks / ⚠️ futuros (alternativa implementada em S5) |
| S5 | R7 | **Rota B**: `calendario_b3` (2 922 dias), `profit_daily_cov` (materializada via procedure `pop_profit_daily_cov` com COMMIT por ticker), view `gap_map_1m` com métrica `minutos_com_tick` (funciona para stocks e futuros). 270 405 linhas; query top-20 gaps em 8.99 s. | ✅ |
| S6 | R6 | Full sync Fintz RUN_ONCE: ok=5, skip=75, error=0, 13.96 M rows adicionadas a `fintz_indicadores_ts` (antes 0). **Fix de dois bugs**: (a) timescale_writer não aceitava df bruto (data→data_publicacao); (b) DSN apontava localhost:5433 dentro do container. **Bloqueio externo confirmado**: `cotacoes_ohlc` da Fintz congelado em 2025-12-30. `fill_fintz_gap.ps1` arquivado. | ⚠️ código OK; DoD de cobertura bloqueado lado Fintz |
| S8 | R9 | Grafana reaproveitado (já rodava 2 dias). Datasource `TimescaleDB` + dashboard `finanalytics-data-quality` com 3 painéis (cobertura 30d heatmap-like, latência por ticker, candles/minuto hoje). Senha admin resetada (`finanalytics2026`). JSON versionado. | ✅ |
| S10-scaffold | R10 | Auditoria ML existente (3.2 k linhas já em produção). `features_daily` hypertable. `scripts/features_daily_builder.py` (CLI) + `scripts/train_petr4_mvp.py` (LightGBM). Validação PETR4: **val IC=0.0593, test IC=0.1106** (DoD >0.05 bate). Sharpe LS naive negativo (esperado; usar `MLStrategy` + `RiskEstimator` existentes). `Strategy` Protocol documentado via README. | ✅ scaffold / ⚠️ Sharpe (tratamento via infra existente) |

**Fixes transversais:**

- **profit_agent /metrics** (Prometheus text exposition): counters/gauges para ticks/probes/contaminações/buffer/db. Código pronto, requer restart do agent (retry do backfill protege).
- **profit_daily_cov** expandido de 30d para **108d** (2026-01-02 → 2026-04-17), 9 489 linhas. Painel 2 do Grafana ganha backup de 3.5 meses.
- **fintz_indicadores_ts** reparado (5 datasets, 13.96 M rows).

**Ressalvas e follow-ups** (detalhes em changelog §1.6 e `runbook_R10_modelos.md §6`):

1. **Profit_daily_bars quirk de escala**: valores oscilam 0.4 ↔ 49 entre dias para PETR4 (possível confusão close/close_ajustado ou split factor dinâmico). Desativado como fonte no `features_daily_builder`. Investigar em Sprint 10.1.
2. **Fintz pós-dez/2025**: requer contato com Fintz/Varos. Alternativas: migrar fonte EOD, usar ProfitDLL pós-S1, aceitar gap.
3. **`/predict` endpoint**: `routes/forecast.py` e `routes/ml_forecasting.py` (1 080 linhas) cobrem mas não foram revisados.
4. **Restart profit_agent** pendente para ativar `/metrics`.
5. **Prometheus container** não existe (datasource mal configurado) — subir container real para consumir `/metrics`.
6. **R5 e R8** são decisões de negócio pendentes.
7. **75 datasets Fintz** em `hash_unchanged` nunca chegaram ao Timescale; limpar `fintz_sync_log` + full sync para backfill completo (~4–6h, pode esperar).

## 1.5 Destaques da sessão 17/abr/2026

Três descobertas importantes nesta sessão, na ordem em que ocorreram:

**(a) 5 bugs em `/collect_history` causaram contaminação severa em 13–16 de abril.** Auditoria em `market_history_trades` revelou cobertura catastrófica nos quatro dias de 13/abr a 16/abr (0,04% / 1,84% / 14,16% / 9,84%). As causas: filtro de ticker ausente no callback histórico, contaminação cruzada entre probes consecutivos, race condition no `done.set()` (sinalizava término antes do último batch drenar), janela temporal mal-validada e re-aproveitamento de buffer entre tickers. Patch-bundle (7 alterações) aplicado em 17-abr. Detalhes em Apêndice B.

**(b) Validação do patch em escala: 540 probes, 0 contaminações.** O script `backfill_recentes_4dias.py` re-coletou a watchlist VERDE+AMARELO nos 4 dias problemáticos (135 tickers × 4 dias = 540 probes), registrando 5,3M ticks e **zero** violações de `first.ticker == last.ticker == requested`. Confirma empiricamente que o patch segura. Log completo em `backfill_4dias_log.txt`.

**(c) A fórmula `(tn_max − tn_min)/10 + 1` não serve para futuros.** Na auditoria pós-backfill, a cobertura ponderada ficou entre 25% e 82% em alguns dias, mesmo com a cobertura média em 99%+. Diagnóstico mostrou que todas as ações ficaram em pct=100% (stride de `trade_number` é 10 na B3 para à vista); os ~82% vieram inteiramente de WINFUT e WDOFUT, cujo stride é variável (~160 e ~70 respectivamente, não 10). A fórmula de “esperado” é stocks-specific e precisa ser adaptada para futuros quando for feita a auditoria ponderada daquela classe de ativos. Isso é uma tarefa de Fase 3.

Consequência prática: a **profundidade histórica** do ProfitDLL está validada (PETR4 2020-01-07 devolveu 45k ticks em probe isolada; janela de 7 dias em 2020 gerou 356k ticks sem erro), e é seguro deixar o backfill de 6 anos rodando. O script foi projetado para retomar sem perda de dados se o serviço reiniciar.

## 1.6 Roadmap de melhorias (R1–R10) e suas necessidades

**R1. Backfill histórico 2020–hoje (em execução iminente).** Necessidades: nssm instalado (`choco install nssm -y` ou download em nssm.cc), profit_agent up em 8002, Profit.exe logado. Comando: `.\setup_backfill_service.ps1 install` seguido de `start` e `tail`. Saída esperada em `logs\backfill_historico_stdout.log`.

**R2. Re-agregação ohlc_1m para 13–16/abr (pendente).** Necessidades: R1 concluído (ou ao menos para esses 4 dias), script `fase1_backfill_ohlc_1m.sql` adaptado com cláusula `WHERE trade_date::date IN (…)`. Após isso, auditar o agregado para garantir que o número de candles por ticker × dia bate com o esperado de pregão (horário oficial de 10:00 a 17:00 dá ~420 candles).

**R3. Auditoria ponderada adaptada a futuros (Fase 3).** Necessidades: descobrir o stride típico de `trade_number` em WINFUT e WDOFUT por contrato e por intensidade (pode variar com vencimento). Criar uma CTE que, por `exchange`, aplique o divisor correto (10 para stocks, stride empírico para futuros). Alternativa: abandonar a fórmula baseada em `trade_number` e medir cobertura em termos de janela temporal (minutos com ≥ 1 tick / minutos totais do pregão).

- **19/abr/2026**: R3 parcialmente fechado (Sprint 4, §8 PLANO_CLAUDE_CODE.md).
  - Criado `ticker_stride` (PK ticker, classe stock/future, stride_padrao, fonte). Populado com 215 stocks da watchlist (fonte `b3_stride_10`) + WINFUT + WDOFUT (fonte `empirical_2026-04-13..16`). **Medição empírica mostrou stride_mediana=10 e stride_p95=10 para ambos os futuros** — contrariando a doc antiga (WINFUT~160, WDOFUT~70). DLL ou patch-bundle normalizou o stride.
  - Criada view `cobertura_diaria_v2` (stocks + future por dia, com `pct_cob_media` e `pct_cob_pond`). INNER JOIN com `ticker_stride` exclui tickers fora da watchlist (opções etc) — 96–104 ficam de fora, trabalho de Sprint 5.
  - **Resultado 13–16/abr/2026:** stocks 100% ponderada nos 4 dias ✅. **Futuros 0.59–14.63% — fórmula `(tn_max−tn_min)/stride+1` super-estima o esperado em WINFUT/WDOFUT** porque `trade_number` tem gaps grandes entre lotes de ticks (não é monotônico consecutivo como em stocks). A alternativa prevista pelo próprio plano (métrica temporal: minutos com ≥1 tick) fica para Sprint 5 (`gap_map_1m`).
  - Limitação performática: filtro `WHERE dia BETWEEN ...` na view bloqueia Index Only Scan (cast `::date`); para janelas grandes, usar CTE direto com `trade_date >= ts AND trade_date < ts_excl`. Documentado em `ESTADO_TECNICO.md §9.1`.
  - Artefatos: `Melhorias/sprint4_ticker_stride.sql`, `ticker_stride` (tabela), `cobertura_diaria_v2` (view).

**R4. Investigar 3 tickers AMARELO_parada_recente.** BBDC3, BPAN4, GUAR3 caíram para AMARELO por parada recente de coleta. Necessidades: consulta `SELECT max(trade_date) FROM market_history_trades WHERE ticker IN (…)`, cruzar com Fintz e calendário B3 para distinguir delisting real, fusão, ou falha de coleta. Se for falha de coleta, forçar probe manual e reverter status para VERDE.

- **19/abr/2026**: R4 fechado.
  - **BBDC3** → `VERDE`: snapshot desatualizada. `max(trade_date)` real = 2026-04-16 (backfill já puxou; 7 675–15 353 ticks/dia entre 13–16/abr). Cobertura normal; não era parada real de coleta.
  - **BPAN4** → `VERMELHO_sem_profit`: parou abrupta em 2026-01-23. Probe manual `/collect_history` em 2026-04-17 (janela 09:00–18:30) retornou 0 ticks (`v1=0, v2=0, first=null, last=null`). Fintz (`fintz_cotacoes_ts`) sem registros além de 2025-11-03 — inconclusivo por atraso da sync Fintz, mas a ausência na DLL em pregão recente confirma delisting/fusão.
  - **GUAR3** → `VERMELHO_sem_profit`: parou abrupta em 2026-02-04. Probe manual em 2026-04-17 também retornou 0 ticks. Mesma conclusão que BPAN4.
  - Distribuição pós-UPDATE: VERDE=124, AMARELO_coleta_fraca=9, AMARELO_parada_recente=0, VERMELHO_sem_profit=82.

- **19/abr/2026**: R2 fechado (Sprint 3, §7 PLANO_CLAUDE_CODE.md).
  - **Premissa do plano corrigida**: `ohlc_1m` é hypertable comum, não continuous aggregate. `refresh_continuous_aggregate` não se aplica. Produtor primário é brapi (ingestor contínuo); ticks só entram em `ohlc_1m` via SQL manual com `source='tick_agg_v1'`. Docs `ESTADO_TECNICO.md §2.2` e §A corrigidas.
  - **Re-agregação efetiva**: fork `Melhorias/fase1_backfill_ohlc_1m_range.sql` com filtro `trade_date BETWEEN 2026-04-13 .. 2026-04-16`. Processou 133 tickers (watchlist VERDE + AMARELO_*) em ~2 min. Resultado: 46 336 / 46 893 / 46 090 / 46 495 barras por dia, 133 tickers/dia, 100 % `source='tick_agg_v1'` (6 tickers brapi antigos em 15–16/abr sobrescritos).
  - **Sanity PETR4 2026-04-15 09:30**: OHLC do `ohlc_1m` bateu exatamente com agregação direta dos ticks (open=47.54, high=47.58, low=47.54, close=47.56, volume=23 000, trades=110). DoD passo 6 ✓.
  - **Validação passo 5** (HAVING count(*) < 300 para VERDE): 30 linhas restantes — ENJU3 (33), IFCM3 (33–66), MATD3 (52–67), AZTE3/ITSA3/AZEV4/FRAS3/HBRE3/MLAS3/OPCT3. Todos tickers VERDE de liquidez marginal (<1M BRL/dia mediano em vários casos). Não é falha de coleta; threshold de 300 é inadequado para tickers esparsos. Tratamento refinado fica para Sprint 5 (gap_map_1m com threshold dinâmico).
  - **Backup preservado**: `ohlc_1m_backup_20260419` (90 294 rows, 132 tickers). Rollback documentado no cabeçalho do fork SQL. Dropar em ~7 dias.

**R5. Assinar ProfitDLL para cobrir os ~80 VERMELHO_sem_profit.** Necessidades: revisar lista de VERMELHO, calcular custo/benefício da assinatura extra da Nelogica para desbloquear tickers fora do plano atual. Alternativa: aceitar cobertura parcial via Fintz-EOD apenas para esses.

**R6. Pipeline P23 Fintz refinado (Fase 2).** Necessidades: refatorar `fill_fintz_gap.ps1` em um job resiliente (idealmente também serviço), com rate-limiting (Fintz tem quota), paginação robusta e escrita idempotente em `fintz_cotacoes_ts`. Incluir monitoramento de latência de atualização por ticker.

- **19/abr/2026**: R6 parcialmente fechado (Sprint 6, §10 PLANO_CLAUDE_CODE.md) — **entregas técnicas OK, DoD de cobertura bloqueado por Fintz**.
  - **Descoberta**: a lógica Fintz já existia em produção em stack DDD completa (`workers/fintz_sync_worker.py` + `application/services/fintz_sync_service.py` + `infrastructure/adapters/fintz_client.py` + `domain/fintz/*`). Atende TODOS os requisitos de §10: `max_retries=3`, timeouts configuráveis, idempotência SHA-256 por dataset (`fintz_sync_log`), SIGTERM/SIGINT graceful, logging estruturado (structlog JSON), RUN_ONCE mode e filtro via `SYNC_DATASETS`. Schedule diário embutido (22:05 BRT via `run_scheduled`) — não precisa nssm dedicado. **Reescrever seria reinvenção**.
  - **Execução full sync (RUN_ONCE)**: 7m 16s, 80 datasets, `ok=5, skip=75, error=0, total_rows=13 961 262`. Log em `Melhorias/logs/fintz_sync_s6_*.log`.
  - **Bloqueio externo confirmado**: `fintz_cotacoes_ts` permanece em `max(time)=2025-12-30` pós-sync. A API Fintz serve o dataset `cotacoes_ohlc` com SHA idêntico desde então (hash_unchanged skip automático). Não é falha do pipeline — é a Fintz/Varos que parou de publicar cotações. **Requer contato com vendor para destravar**.
  - **DoD §10 (não-bate por causa externa)**: `count(*) FROM gap_map_1m WHERE NOT tem_fintz AND dia < CURRENT_DATE` = 17 500 (plano pede ≤100); cobertura Fintz VERDE 2024-hoje << 99.5%. Mas: isso é insolúvel sem resolver o acesso Fintz pós-dez/2025.
  - **Bug follow-up identificado**: durante o sync, 4 erros `timescale_writer.write_failed: expected a datetime.date or datetime.datetime instance, got 'str'` em `fintz_indicadores_ts`. Não afeta cotações. Writer absorveu os erros (service contabilizou `error=0`) mas rows foram perdidas. Merece fix dedicado em `infrastructure/database/repositories/timescale_writer.py` ou adapter upstream.
  - **Artefatos**:
    - `Melhorias/legado/fill_fintz_gap.ps1` (arquivado — wrapper substituído pela execução direta do worker).
    - Documentação completa da stack Fintz em `ESTADO_TECNICO.md §5`.
  - **Próxima ação operacional (fora do escopo Claude Code)**: contato com Fintz/Varos para investigar por que `cotacoes_ohlc` parou de ser atualizado em 2025-12-30. Alternativas: migrar para outra fonte EOD, usar ProfitDLL EOD (quando S1 completar), ou aceitar gap como conhecido.

**R7. View `gap_map_1m` consolidada (Fase 3).** Necessidades: join de `market_history_trades` (ticker × dia × count(*)) com `fintz_cotacoes_ts` e `calendario_b3`. Output: uma linha por ticker × dia com flags `tem_profit`, `tem_fintz`, `pct_cobertura_intraday`. Base para qualquer dashboard de qualidade de dados.

- **19/abr/2026**: R7 fechado (Sprint 5, §9 PLANO_CLAUDE_CODE.md, **Rota B**).
  - **Desvio do plano original**: doc previa `profit_cov` lendo de `ohlc_1m` (CAgg hipotético). Como `ohlc_1m` é hypertable mista brapi+tick_agg_v1 com cobertura histórica esparsa (S3), migramos para `market_history_trades` direto, materializado em `profit_daily_cov` via `CALL pop_profit_daily_cov(d_start)` — PROCEDURE com COMMIT por ticker (progresso visível). CTE inline fazia seq scan de 545M rows → query top-20 pendurava 10+ min. Com tabela materializada: **8.99 s**.
  - **Métrica intraday**: `minutos_com_tick` (`count DISTINCT time_bucket('1 minute')`) em vez de "candles ≥ 300" — funciona igual para stocks e futuros (resolve dívida de S4 sobre futuros).
  - **Threshold tiered por `mediana_vol_brl`** (300/200/100/50/20 min para stocks, 200 min para WINFUT/WDOFUT). Baseline adaptativo por ticker ficou como follow-up (tentativa 60-90d teve custo I/O proibitivo em disputa com S1).
  - **Artefatos criados**: `calendario_b3` (2 922 dias, 101 feriados), `profit_daily_cov` (PK ticker×dia, 2 557 linhas iniciais cobrindo 2026-03-20→2026-04-17, 135 tickers), procedure `pop_profit_daily_cov`, view `gap_map_1m` (270 405 linhas = 135 tickers × 1 247 pregões de 2020–2027).
  - **DoD**: `count(*) FROM gap_map_1m` = **270 405** (plano: ≈200k) ✅; query top-20 em **8.99 s** (plano: <10s) ✅; calendário 248–250 pregões/ano (plano: 247–252) ✅.
  - **Follow-ups documentados**: expandir janela de `profit_daily_cov` pós-S1; implementar `ticker_profit_baseline`; resolver `tem_fintz=false` em ~160 dias recentes (S6); opcionalmente cobrir tickers não-watchlist.
  - **Artefatos em disco**: `Melhorias/sprint5_calendario_b3.sql`, `sprint5_profit_daily_cov.sql`, `sprint5_pop_cov_procedure.sql`, `sprint5_populate_cov_window.sql`, `sprint5_gap_map_1m.sql`. Documentação em `ESTADO_TECNICO.md §9.2`.

**R8. Decisão de hospedagem e deploy.** Necessidades: finalizar comparativo de TCO em `FinAnalyticsAI_Comparativo_Hospedagem.xlsx`, decidir entre workstation local persistente × VM dedicada × híbrido (dual-GPU). Documento em `proposta_decisao_15_dualgpu.md` cobre opções. Impacta plano de uptime do ProfitDLL, uma vez que a DLL é Windows-only e exige Profit.exe rodando.

**R9. Dashboards de qualidade de dados.** Necessidades: stack de visualização (Grafana + Postgres? Streamlit? Next.js?). Painéis mínimos: (i) cobertura por ticker × dia nos últimos 30 dias; (ii) latência de ticks (diferença entre `now()` e `max(trade_date)` por ticker); (iii) gaps em `ohlc_1m` por minuto no dia corrente. Dependência: R7.

- **19/abr/2026**: R9 fechado (Sprint 8, §12 PLANO_CLAUDE_CODE.md).
  - **Contexto descoberto**: container `finanalytics_grafana` 10.4.0 já estava rodando há 2 dias na rede `finanalytics_net` (volume persistente `finanalytics_ai_fresh_grafana_data`), com 3 dashboards pré-existentes (API Overview, Business Metrics, System Resources) e datasource Prometheus. Admin password foi resetado via `grafana cli admin reset-admin-password finanalytics2026`.
  - **Artefatos novos**: datasource `TimescaleDB` (UID `afjkz1gl2ky68e`, via POST `/api/datasources`) e dashboard `FinAnalytics AI — Qualidade de Dados` (UID `finanalytics-data-quality`, via POST `/api/dashboards/db`). JSON versionado em `Melhorias/grafana_dashboards/qualidade_dados.json`.
  - **3 painéis**:
    - **P1 — Cobertura intraday 30d**: table sobre `gap_map_1m` com cores gradiente (vermelho <40%, laranja 40-70%, verde ≥70%) + flags `tem_profit_ok`/`tem_fintz` com ícone ✓/✗ colorido.
    - **P2 — Latência por ticker**: horas desde `max(dia)+17h` em `profit_daily_cov`. Cores: verde <24h, laranja 24-72h, vermelho ≥72h. Hoje (19/abr, domingo) painel mostra ~73h para toda a watchlist (último dia coberto = 2026-04-16) — esperado.
    - **P3 — Candles/minuto hoje**: bar chart com contagem de tickers em `ohlc_1m` por minuto (10:00-17:00). Vazio em dia sem pregão (como hoje, domingo). Vai preencher a partir de segunda.
  - **Refresh**: 5min. Time range default: `now-30d`.
  - **DoD §12**: Grafana em `:3000` ✅; datasource TimescaleDB OK (teste `SELECT 1` via API retornou 200) ✅; 3 painéis populados ✅; JSON versionado ✅.
  - **Limitações**: P2 usa `profit_daily_cov` em vez de `market_history_trades` direto — latência real de ticks bruta precisaria de query mais pesada; compromisso aceito (profit_daily_cov é 30d rolling window, será mais preciso pós-Sprint 1). P3 só funciona em dia de pregão.
  - **Artefatos em disco**: `Melhorias/grafana_dashboards/qualidade_dados.json`. Documentação em `ESTADO_TECNICO.md §11` (seção "Grafana", "Datasources", "Dashboards").

**R10. Sistema de modelos e backtests.** Necessidades: R1 terminar (6 anos de histórico), arquitetura definida para features incrementais (materialized views com joins Fintz ↔ Profit), framework de backtest (VectorBT, Zipline fork, ou custom). Decisão de GPU (dual-GPU) impacta custo/tempo de treino de modelos maiores.

- **19/abr/2026**: R10 scaffold concluído (Sprint 10, §14 PLANO_CLAUDE_CODE.md).
  - **Auditoria** revelou ~3 200 linhas de ML/backtest já implementadas: `application/ml/*` (QuantileForecaster LightGBM P10/P50/P90; feature_pipeline com RSI/vol/returns; MLService; MLStrategy; RiskEstimator histórico/t-Student/GARCH/MonteCarlo), `domain/backtesting/*` (Strategy Protocol, engine.run_backtest, 19 estratégias em technical.py, optimizer walk-forward, multi_ticker), routes ML (1 080 linhas). Reescrever seria reinvenção. Inventário em `Melhorias/auditoria_ml_existente.md`.
  - **Artefatos novos**:
    - `features_daily` (hypertable TimescaleDB, PK `ticker,dia`, 12 colunas incluindo `close, r_1d/5d/21d, atr_14, vol_21d, vol_rel_20, sma_50, sma_200, rsi_14, source`) — `Melhorias/sprint10_features_daily.sql`.
    - `scripts/features_daily_builder.py` — CLI `--backfill` / `--incremental` / `--only T1,T2` / `--dry-run`. Fonte MVP: `fintz_cotacoes_ts` 2010→2025-12-30. `profit_daily_bars` desativado por quirk de escala (0.4 ↔ 49 para PETR4; investigar Sprint 10.1).
    - `scripts/train_petr4_mvp.py` — pipeline end-to-end LightGBM Regressor, target r_1d_futuro (log-return 1d ahead), split 2020-23/2024/2025+, métricas IC (Spearman), hit rate, Sharpe LS. Serializa `models/*.pkl|json`.
    - `src/finanalytics_ai/domain/backtesting/strategies/README.md` — documenta padrão Strategy Protocol (§14 passo 4).
  - **Validação end-to-end (PETR4, 1 257 rows Fintz)**:
    - train=795, val=251, test=211
    - **val IC = 0.0593** (DoD >0.05 ✅), **test IC = 0.1106** (DoD >0.05 ✅)
    - val hit rate 0.47, test hit rate 0.48
    - val Sharpe LS = −0.28, test Sharpe LS = −0.54 (DoD >0 ❌ — estratégia sign() naive perde; produção deve usar `MLStrategy` com sizing + filtro via `RiskEstimator`).
    - Modelo serializado em `models/petr4_mvp_PETR4_*.pkl|.json`.
  - **DoD §14**:
    - `features_daily` populado para watchlist VERDE 2020-hoje: parcial (PETR4 validado; expandir com `--backfill`).
    - 1 modelo com IC>0.05: ✅ bate.
    - Sharpe>0: ❌ não bate com estratégia naive — follow-up com `MLStrategy` + `RiskEstimator` (infraestrutura pronta).
    - `/predict` respondendo 5 tickers: ❌ não implementado — `routes/forecast.py`/`ml_forecasting.py` já cobrem mas não foram revisados; follow-up.
    - Runbook: ✅ `Melhorias/runbook_R10_modelos.md`.
  - **Follow-ups**: (a) expandir features_daily para watchlist inteira (`--backfill` roda em ~1h); (b) cobertura 2026+ (dep. S1 completar + regenerar `profit_daily_bars` ou agregar `ohlc_1m` daily); (c) endpoint `/predict` usando último pickle; (d) `MLStrategy` backtest produção com QuantileForecaster + RiskEstimator; (e) MLflow registry (out-of-scope).

Priorização prática imediata: R1 (rodando) → R4 (rápido, descarta delisting) → R2 (depende de R1) → R7 (destranca R9) → R10.

---

# PARTE 2 — APÊNDICES TÉCNICOS

## A. Modelo de dados TimescaleDB

**`market_history_trades`** (hypertable, partition por `trade_date`): uma linha por tick. Chave composta `(ticker, trade_date, trade_number)` usada no `ON CONFLICT DO NOTHING`. Colunas principais: `ticker text`, `trade_date timestamptz`, `trade_number bigint`, `price numeric`, `quantity bigint`, `agressor char(1)`, `exchange char(1)`, `trade_id bigint`. `trade_number` é monotônico dentro de um pregão na B3 com stride 10 para ações (à vista); em futuros o stride varia (ver 1.5).

**`ohlc_1m`** (**hypertable**, não continuous aggregate — correção 19/abr/2026): candles de 1 minuto, PK `(time, ticker)`, coluna `source` diferencia origem. Duas fontes populam a mesma tabela: `source='brapi'` (ingestor contínuo `workers/ohlc_1m_ingestor.py` + `OHLC1mService`) e `source='tick_agg_v1'` (agregação manual de `market_history_trades` via `fase1_backfill_ohlc_1m.sql`). Colunas: `time`, `ticker`, `open`, `high`, `low`, `close`, `volume`, `trades`, `vwap`, `source`. Não há continuous aggregate ou job de refresh — apenas Columnstore Policy (job 1003, compressão após 7d). Ver `ESTADO_TECNICO.md §2.2` para detalhes.

**`watchlist_tickers`** (tabela comum, ~135 linhas): fonte canônica da watchlist. DDL em `create_watchlist_tickers.sql`. Campos: `ticker (PK)`, `mediana_vol_brl`, `media_vol_brl`, `mediana_trades_dia`, `dias_cobertura`, `ticks_2026`, `ultimo_tick`, `status` (enum textual: `VERDE`, `AMARELO_parada_recente`, `AMARELO_coleta_fraca`, `VERMELHO_sem_profit`). Refresh é manual após re-running dos diagnósticos de Fase 0.

**`fintz_cotacoes_ts`** (hypertable): EOD bars vindos da Fintz. Cobertura histórica mais ampla (começa antes de 2020) porém diária, sem granularidade intraday. Usada para cross-check e para fechar gaps quando ProfitDLL está indisponível.

## B. profit_agent: bugs, patches, endpoints

**Localização:** `D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\profit_agent.py` (FastAPI) embrulhando `ProfitDLL` da Nelogica via `ctypes`.

**Bugs identificados e corrigidos em 17-abr (patch-bundle v1):**

1. **Filtro de ticker ausente no callback histórico.** O callback V1 registrado via `SetHistoryTradeCallbackV2` (nome confuso da DLL) recebia ticks de **qualquer** assinatura ativa, não só do ticker solicitado. Patch: filtrar `if asset.ticker != requested_ticker: return` no callback.

2. **Contaminação entre probes consecutivos.** Buffer `self._history_buffer` não era limpo entre chamadas a `/collect_history`. Segunda chamada herdava ticks da primeira. Patch: `self._history_buffer.clear()` no início de cada probe.

3. **Race condition em `done.set()`.** O evento era sinalizado quando o callback de término disparava, mas batches ainda podiam estar a caminho na thread do callback. Patch: introduzido `time.sleep(0.5)` antes do `done.set()` e flush explícito via `pending_batches == 0`.

4. **Janela temporal mal-validada.** `dt_start` e `dt_end` eram aceitos sem checagem de consistência (fim < início passava). Patch: validação explícita + `raise HTTPException(400)` em requests inválidos.

5. **Re-aproveitamento de buffer V2.** Callback V2 (detalhado) reusava uma struct compartilhada entre probes, corrompendo dados do probe seguinte. Patch: deepcopy da struct antes de enfileirar.

**Endpoints (em uso ativo):** `GET /status` (retorna `market_connected`, `db_connected`, `total_assets`); `POST /collect_history` com body `{ticker, exchange, dt_start, dt_end, timeout}` retorna `{ticks, inserted, v1_count, v2_count, first, last, status}`; `POST /subscribe` e `POST /unsubscribe` para realtime; `GET /ticks` para leitura em streaming.

## C. Pipeline Fintz e gap handling

**Finalidade:** cotações diárias (EOD) para o universo B3 completo + metadados (setor, indicadores). Serve de backup quando ProfitDLL está indisponível, de fonte de verdade para o ranking de liquidez (base do critério Fase 0), e de dado de referência para validação cruzada.

**Tabela destino:** `fintz_cotacoes_ts` (hypertable).

**Scripts relevantes:**
- `fill_fintz_gap.ps1`: fecha gaps pontuais em datas específicas (uso manual).
- `diag_fintz_atrasados_readonly.ps1`: diagnóstico read-only de tickers com dados atrasados.
- `diag_fintz_gap_readonly.ps1`: mapeamento de gaps por ticker × período.
- `diag_fintz_script_validacao.ps1`: valida consistência Fintz vs ProfitDLL.

**Limitações conhecidas:** Fintz tem quota diária; rate-limiting é manual hoje. MBRF3 tem cobertura Fintz só desde a data da fusão (mar/2026) — é por isso que está marcada como AMARELO_coleta_fraca no campo Fintz mas VERDE no Profit.

## D. Watchlist canônica (Fase 0): critérios e tiers

**Arquivo de criação:** `D:\Investimentos\FinAnalytics_AI\Melhorias\create_watchlist_tickers.sql`.

**Critério de entrada:** mediana(`volume_negociado`) > R$ 500.000/dia no período 2024-11-01 a 2025-11-30, com ≥ 150 dias de cobertura em `fintz_cotacoes_ts`. Excluídos: IBOV (é índice, não ativo), BRFS3 e MRFG3 (fundiram em MBRF3 em mar/2026), NTCO3 (delisted).

**Inclusão manual:** MBRF3, pois só tem 30 dias de Fintz (pós-fusão) mas cobertura Profit completa.

**Tiers:**
- **VERDE:** ativo hoje, cobertura estável, tick flow OK.
- **AMARELO_parada_recente:** tinha fluxo até recentemente, parou. Candidatos a delisting/fusão ou falha de coleta. Hoje: BBDC3, BPAN4, GUAR3 (investigar — R4).
- **AMARELO_coleta_fraca:** tick flow abaixo do esperado; coleta funciona mas produz pouco tick/dia. Pode ser liquidez real baixa (BDRs pouco negociados) ou problema de subscrição.
- **VERMELHO_sem_profit:** presentes na Fintz mas ausentes em `market_history_trades`. Provavelmente fora da assinatura Nelogica atual (~80 tickers — R5).

**DDL CHECK:**

```sql
status text NOT NULL CHECK (status IN (
    'VERDE',
    'AMARELO_parada_recente',
    'AMARELO_coleta_fraca',
    'VERMELHO_sem_profit'
))
```

## E. Scripts de backfill: inventário e uso

**`scripts/backfill_historico_watchlist.py`** (18,4 KB) — **o principal**. Cobertura completa 2020–hoje. Carrega watchlist dinamicamente (VERDE + AMARELO_*), ordenado por `mediana_vol_brl DESC`. Inclui WINFUT e WDOFUT por default (ajustável via `--futures` / `--no-futures`). Timeout diferenciado: 300s para stocks, 2400s para futuros. Retry 3× com backoff 10/20/30s. Graceful shutdown via SIGINT/SIGTERM/SIGBREAK. Ideal para rodar sob nssm.
Uso típico: `python backfill_historico_watchlist.py` (defaults: 2020-01-02 até hoje, delay 2s).
Flags: `--start`, `--end`, `--delay`, `--from-ticker` (retoma a partir de um ticker), `--only "PETR4,VALE3"` (substitui watchlist), `--futures "WINFUT,WDOFUT"`, `--no-futures`, `--dry-run`.

**`scripts/backfill_recentes_4dias.py`** (11 KB) — re-coleta dos 4 dias 2026-04-13 a 04-16 pós-patch. Já rodou com sucesso em 17-abr (540 probes, 0 contaminação). Deixar como scaffold reutilizável para futuras re-coletas pontuais.

**`scripts/backfill_2025_top50.py`** (9 KB) — cenário B de ingestão inicial (top 50 por liquidez em 2025, ranking fixo no script). Foi o teste de carga original antes da watchlist canônica. Dados que ele gerou estão no banco — não precisa re-rodar.

**`scripts/backfill_history.py`** (13,5 KB) — script genérico mais antigo. Mantido por compatibilidade, mas os três acima cobrem todos os casos práticos.

**`scripts/backfill_resume.py`** (10 KB) — reexecução de probes específicos por (ticker, data) a partir de um CSV. Útil para retry manual de erros.

**`setup_backfill_service.ps1`** (`D:\Investimentos\FinAnalytics_AI\Melhorias`) — wrapper nssm para gerenciar o serviço `FinAnalyticsBackfill`. Ações: `install`, `start`, `stop`, `restart`, `status`, `tail`, `uninstall`. Logs em `D:\Investimentos\FinAnalytics_AI\Melhorias\logs\backfill_historico_{stdout,stderr}.log` com rotação em 100 MB.

## F. Runbooks operacionais

**Subir o backfill histórico (primeira vez):**

1. Abrir Profit.exe e fazer login.
2. Subir profit_agent: entrar na conda env `(finanalytics-ai)`, rodar `uvicorn finanalytics_ai.profit_agent:app --port 8002`. Verificar `curl http://localhost:8002/status` → `market_connected: true`.
3. Em PowerShell Admin, com a env ativa: `cd D:\Investimentos\FinAnalytics_AI\Melhorias`.
4. `.\setup_backfill_service.ps1 install` (cria serviço `FinAnalyticsBackfill`).
5. `.\setup_backfill_service.ps1 start`.
6. `.\setup_backfill_service.ps1 tail` (acompanha log; Ctrl+C sai sem afetar o serviço).

**Parar o backfill de forma limpa:** `.\setup_backfill_service.ps1 stop`. nssm envia Ctrl+Break, Python intercepta (SIGBREAK), finaliza o ticker corrente, sai. Espera até 120 s antes de força-bruta.

**Retomar após restart do Profit/workstation:** o serviço tem `Start SERVICE_DEMAND_START` (não sobe no boot). Isso é intencional — Profit.exe precisa estar logado primeiro. Passos: 1) abrir e logar Profit, 2) subir profit_agent, 3) `.\setup_backfill_service.ps1 start`. O script Python retoma automaticamente porque `get_collected_dates` pula qualquer dia já presente no banco e `ON CONFLICT DO NOTHING` torna qualquer duplicata benigna.

**Diagnosticar uma queda do backfill:** `.\setup_backfill_service.ps1 status` mostra últimas 40 linhas de stdout + 20 de stderr. Se `market_connected=false`, o problema está no Profit.exe ou na conexão DLL. Se for erro transitório repetido, ajustar `RETRY_MAX`/`RETRY_DELAY_BASE` no script e reiniciar.

**Re-agregar ohlc_1m após re-coleta de ticks:** `psql` no container, rodar `fase1_backfill_ohlc_1m.sql` com `WHERE trade_date::date IN (…)` nos dias afetados. A chamada de `refresh_continuous_aggregate('ohlc_1m', start, end)` é idempotente.

## G. Consultas SQL de referência

**Cobertura por dia (stocks apenas, ponderada por trade_number stride 10):**

```sql
WITH por_ticker AS (
    SELECT ticker,
           trade_date::date AS dia,
           count(*) AS trades,
           CASE WHEN max(trade_number) - min(trade_number) > 0
                THEN (max(trade_number) - min(trade_number)) / 10 + 1
                ELSE 1
           END AS esperado
      FROM market_history_trades
     WHERE trade_date::date IN ('2026-04-13','2026-04-14','2026-04-15','2026-04-16')
       AND exchange = 'B'   -- exclui futuros
     GROUP BY ticker, trade_date::date
)
SELECT dia,
       count(*) AS tickers,
       sum(trades) AS trades_total,
       sum(esperado) AS esperado_total,
       round(avg(100.0 * trades / NULLIF(esperado, 0)), 2) AS pct_cob_media,
       round(100.0 * sum(trades) / NULLIF(sum(esperado), 0), 2) AS pct_cob_pond
  FROM por_ticker
 GROUP BY dia
 ORDER BY dia;
```

**Últimos tick por ticker (detecta coleta parada):**

```sql
SELECT ticker, max(trade_date) AS ultimo_tick
  FROM market_history_trades
 GROUP BY ticker
 ORDER BY ultimo_tick ASC
 LIMIT 30;
```

**Ranking de liquidez 2025 (base da watchlist):**

```sql
SELECT ticker, percentile_cont(0.5) WITHIN GROUP (ORDER BY volume_negociado) AS mediana_vol_brl
  FROM fintz_cotacoes_ts
 WHERE data BETWEEN '2024-11-01' AND '2025-11-30'
 GROUP BY ticker
HAVING count(*) >= 150
   AND percentile_cont(0.5) WITHIN GROUP (ORDER BY volume_negociado) > 500000
 ORDER BY mediana_vol_brl DESC;
```

**Detectar contaminação cruzada (validação pós-patch):**

```sql
SELECT ticker, trade_date::date AS dia, count(DISTINCT agressor) AS agressores
  FROM market_history_trades
 WHERE trade_date::date = '2026-04-17'
 GROUP BY ticker, trade_date::date
HAVING count(DISTINCT agressor) > 3;
-- esperado: agressor é 'C'/'V'/'D'/' ', até 4. Mais que isso é ruído.
```

## H. Paths, serviços e portas

**Repositório principal:** `D:\Projetos\finanalytics_ai_fresh`.
- `src/finanalytics_ai/profit_agent.py` — FastAPI wrapper.
- `scripts/backfill_*.py` — scripts de ingestão.
- `scripts/diag_*.py` — diagnósticos Python.

**Workbench de melhorias:** `D:\Investimentos\FinAnalytics_AI\Melhorias`.
- `*.ps1` — diagnósticos e scripts operacionais PowerShell.
- `*.sql` — SQLs de auditoria, agregação e criação.
- `*.md` — briefings, planos, este documento.
- `logs/` — logs do serviço nssm.

**Serviços locais:**
- TimescaleDB (Docker) em `localhost:5433`, container `finanalytics_timescale`, banco `market_data`, user `finanalytics`.
- profit_agent em `localhost:8002` (FastAPI/uvicorn).
- Serviço planejado: `FinAnalyticsBackfill` (nssm), status `SERVICE_DEMAND_START`.

**DSN padrão (env `PROFIT_TIMESCALE_DSN`):** `postgresql://finanalytics:timescale_secret@localhost:5433/market_data`.

**Conda env:** `finanalytics-ai` — deve estar ativa para rodar scripts Python e para `setup_backfill_service.ps1 install` (fixa o python.exe da env no serviço).

---

## Changelog do documento

- **17/abr/2026 — v1** (este arquivo): consolidação inicial pós-sessão de debug dos 5 bugs. Estado Fase 0 concluída, Fase 1 parcial, Fase 4 em imminent start.
