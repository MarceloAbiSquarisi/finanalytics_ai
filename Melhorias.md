# Backlog de Melhorias — FinAnalytics AI

> Lista priorizada de melhorias. Itens entregues sumarizados no topo; novos itens descobertos no rodapé.
>
> **Última revisão**: 27/abr/2026 madrugada — sessão N1-N9 (data quality + scheduler + scrapers + UI fixes)

---

## ✅ ENTREGUES (sessão 27/abr madrugada — N1+N2+N5+N7+N9+N8)

### N1 — Limpeza profit_daily_bars escala mista ✅ DONE
**Diagnóstico**: ticks em `market_history_trades` chegam com escala /100 em alguns dias específicos (PETR4 09/04→16/04 mostrou padrão misto). `ohlc_1m` (source `tick_agg_v1`) NÃO tem o bug — agregador filtra/corrige.

**Fix**:
1. Backup: `CREATE TABLE profit_daily_bars_backup_27abr AS SELECT * FROM profit_daily_bars`
2. DELETE dos 6 tickers afetados (404 rows: ABEV3/BBDC4/ITUB4/PETR4/VALE3/WEGE3)
3. `populate_daily_bars.py --source 1m --ticker $T` regenera de ohlc_1m

**Validação**: PETR4 antes min=0.2968 max=49.55, depois min=14.66 max=49.61 (1.92→38.55 média) ✅. Endpoint `/levels` retorna `outliers_dropped=0` e `data_quality_warning=null` para os 6 tickers.

### N2 — Job CVM informe mensal no scheduler ✅ DONE
- Adicionado `cvm_informe_sync_job()` em `scheduler_worker.py`. Roda 1x/dia em `CVM_INFORME_HOUR=9` BRT, mas só executa de fato em `CVM_INFORME_DAY=5` (skip silencioso resto dos dias).
- Competência calculada = mês anterior (`hoje.replace(day=1) - timedelta(days=1)`). Validado: 27/04/2026 → `202603`.
- Idempotente (sync_informe_diario já checa fundos_sync_log).

### N5 — Fundamentals FII via Status Invest ✅ DONE
- Tabela `fii_fundamentals (ticker, snapshot_date, dy_ttm, p_vp, div_12m, valor_mercado, source, scraped_at)` PK `(ticker, snapshot_date)`.
- Scraper `scripts/scrape_status_invest_fii.py` (httpx + regex robustos): 27/28 FIIs OK em ~30s (MALL11 delistado).
- Job `fii_fundamentals_refresh_job` no scheduler (7h BRT, skip weekend, subprocess isolado para não bloquear event loop).
- Dockerfile worker stage agora copia `scripts/`.

### N11 — /levels suporta FIIs e ETFs (profit_daily_bars populado via Yahoo) ✅ DONE 28/abr
- Diagnóstico: `fetch_candles` (5 níveis) já era usado por `/levels`, mas nenhum nível cobria FIIs/ETFs Yahoo (`features_daily` só guarda close). 404 confirmado em KNRI11 nesta sessão.
- Solução pragmática: novo `scripts/backfill_yahoo_daily_bars.py` popula `profit_daily_bars` (schema OHLCV existente) com daily bars Yahoo dos 39 FIIs+ETFs do `ticker_ml_config`. ON CONFLICT (time, ticker, exchange) idempotente.
- Run-once: 20.178 rows novas (~518 bars × 39 tickers, 2 anos). Total `profit_daily_bars` agora: 20.779 rows.
- Validação: KNRI11/BOVA11/HFOF11/RECT11 retornam Williams 17-30 fractais cada (antes 404).
- Follow-up: agendar refresh diário (similar a fii_fundamentals) — fica como **N11b** trivial.

### N11b — Refresh diário Yahoo daily bars no scheduler ✅ DONE 28/abr
- `yahoo_daily_bars_refresh_job` em `scheduler_worker.py`. Roda 8h BRT diário, skip weekend.
- Subprocess isolado chama `scripts/backfill_yahoo_daily_bars.py --years 2`.
- Mantém profit_daily_bars de FIIs+ETFs em dia sem intervenção manual.

### N6 — Crypto persistence + multi-horizon signals ✅ DONE 28/abr
- Tabela `crypto_signals_history` (symbol, snapshot_date, vs_currency PK).
- Script `scripts/snapshot_crypto_signals.py` chama API `/crypto/signal/{sym}` e persiste 9 colunas (signal/score/price/RSI/MACD/EMAs/BB).
- Job `crypto_signals_snapshot_job` no scheduler (9h BRT diário, sem skip weekend — crypto 24/7).
- Endpoint novo `/api/v1/crypto/signal_history/{symbol}?days=30` retorna histórico + agregação multi-horizon (`h7d`/`h14d`/`h30d`: signal predominante + score_avg + n).
- Validado: BTC HOLD score=-2 price=77.445, ETH HOLD score=1, SOL HOLD score=1.
- **Limitação**: CoinGecko sem candles intraday verdadeiros (h1/h6 reais ficariam para worker tick separado).

### N4 — Markov empírico para RF Regime ✅ DONE 28/abr
- Função `compute_transitions(history, current_regime)` em `domain/rf_regime/classifier.py`.
- Calcula matriz 4×4 P(regime_t+1 | regime_t) por contagem empírica + duração média de cada regime + most_likely_next.
- `analyze_regime` retorna campo novo `transitions: RegimeTransitions | None`.
- Validado live: NORMAL atual com 94.64% prob de continuar NORMAL amanhã, 3.57% STEEPENING, 1.79% FLATTENING. NORMAL dura ~17 dias em média; STEEPENING ~1.7 dias.
- **Decisão pragmática**: HMM real (hmmlearn) descartado — Markov chain empírica entrega o essencial (probabilidades + duração) sem dep pesada e sem treino.

### Sessão housekeeping (28/abr madrugada A→H) ✅ DONE 28/abr
- **A** SW cache bumped v86 → v87 (invalida cache stale do N6b/N4b).
- **B** `docs/runbook_profit_daily_bars_scale.md` documenta sintomas, diagnóstico e fix do bug N1 + Decisão 21.
- **C** `Roteiro_Testes_Pendentes.md` ganhou seção A.24 (30 checks novos cobrindo todos os itens N1-N12 + housekeeping).
- **D** Pre-flight live de `yahoo_daily_bars_refresh_job` no scheduler container: 39 tickers, 20.178 rows em 90s — subprocess validado.
- **E** Sparkline extraído para `static/sparkline.js` (helper reusável `FASparkline.render(values, opts)`). Carteira refatorada para usar helper. Disponível para screener/performance/watchlist no futuro.
- **F** Métricas Prometheus novas: `finanalytics_fii_fundamentals_age_days` e `finanalytics_crypto_signals_history_age_days` (atualizadas a cada 5min em `ml_metrics_refresh._refresh_once`). Alert rule `fii_fundamentals_stale` migrada para gauge direta + nova `crypto_signals_history_stale`. **15 alert rules** ativas no Grafana.
- **G** `tests/unit/domain/test_rf_regime_transitions.py` — 13 testes do Markov empírico (matriz, duração, argmax, alternância).
- **H** `tests/unit/scripts/test_scrape_status_invest_fii.py` — 16 testes do scraper (`_to_float` pt-BR, regex DY/PVP/div12m/valor_mercado em snapshot HTML real). 29 tests verdes em <1s.

### Migrations alembic + populate default ✅ DONE 28/abr
- `init_timescale/004_fii_fundamentals.sql` e `005_crypto_signals_history.sql` versionam tabelas que existiam só em runtime.
- Idempotentes (`CREATE TABLE IF NOT EXISTS`); aplicar via `psql -f` em DB existente é no-op.
- `populate_daily_bars.py` invertido: `auto` agora tenta `1m` primeiro, `ticks` como fallback. Evita regressão N1 quando alguém rodar sem `--source` explícito. PETR4 validado: `source=1m`.

### Alert rules Grafana para novos jobs ✅ DONE 28/abr
- `scheduler_data_jobs_errors`: alerta quando yahoo_bars/fii_fund/crypto_signals/cvm_informe têm ≥3 falhas em 6h (severity=warning, team=data).
- `fii_fundamentals_stale`: alerta quando `fii_fund` job não tem nenhuma execução OK em 48h (Status Invest scraper parou).
- 14 rules ativas no Grafana (12 antigas + 2 novas), validado via API `/api/v1/provisioning/alert-rules`.

### N6b — UI Crypto sparkline do score histórico ✅ DONE 28/abr
- `enrichCryptoSignals` em `carteira.html` agora também busca `/crypto/signal_history/{sym}?days=14` em paralelo.
- Sparkline SVG inline (64×16, sem libs) à direita do badge BUY/SELL/HOLD.
- Cor da linha derivada do score atual (verde >+1, vermelho <-1, cinza neutro).
- Tooltip mostra horizons agregados (h7d/h14d).
- Linha pontilhada do zero como referência visual.

### N4b — UI RF Regime Markov transitions ✅ DONE 28/abr
- Novo bloco `#rf-regime-transitions` no card RF (separado por borda dashed do bloco de alocação).
- Mostra probabilidades P(amanhã | regime_atual) ordenadas DESC, mais provável em destaque (cor sólida + bold).
- "regime atual dura ~N dias em média" baseado no `avg_duration_days`.
- Esconde silenciosamente quando `transitions=null` (history < 31 obs).

### N10b — anomalies/style validados para FIDC/FIP ✅ DONE 28/abr
- Smoke OK em ambos endpoints com FIDC e FIP top do peer-ranking.
- FIDC anomalies: 5 detectadas (z-scores -10/-4.86/-3.91 — saltos típicos de fundo de crédito).
- FIDC style: r²=0.26, alpha 15% a.a., baixíssimos betas (consistente com não-correlação com mercado).
- FIP anomalies: 3 detectadas (cota com baixa frequência); FIP style: r²=0.32, alpha 27% a.a.
- Backend genérico não precisou modificação. UI já exibe na seção Style/Anomalies expansível do peer-ranking.

### Limpeza Dockerfile — scripts/ no api stage ✅ DONE 28/abr
- `COPY scripts/ ./scripts/` adicionado no api stage (worker já tinha desde N5).
- Permite jobs subprocess executados pela API quando necessário (ex.: futuras chamadas N6/N5).

### N10 — ML analytics para FIDC/FIP ✅ DONE 28/abr
- Backend já era genérico (peer_ranking aceita qualquer `tipo`). Validação live: FIDC top sharpe=144 (inflado por low-vol), FIP top sharpe=4.7.
- UI `/fundos`: dropdown ganhou FIDC/FIDC-NP/FIP/FIP Multi/Referenciado.
- Backend route `/peer-ranking` retorna campo `warning` quando tipo ∈ {FIDC*, FIP*, FMIEE} explicando peculiaridades de cota (low-vol → sharpe inflado / baixa frequência → métricas instáveis).
- Frontend exibe warning amarelo abaixo de "Avaliados: N fundos".

### N12 — Validar fix N1 + drop backup ✅ DONE 28/abr
- Validado: 6 tickers DLL com escala coerente (ABEV3 13-17, ITUB4 39-50, PETR4 15-50, VALE3 51-90 etc).
- `/indicators/PETR4/summary` retorna EMAs/RSI/BB normais (close=48.54, EMA20=46.82, RSI14=61.98, BB_upper=50.36).
- `DROP TABLE profit_daily_bars_backup_27abr` (560 rows da pré-N1, sem mais utilidade).

### N5b — Integração visual dos fundamentals em /dashboard ✅ DONE 28/abr
- Endpoint `/api/v1/ml/signals` enriquecido com `dy_ttm` e `p_vp` (LEFT JOIN snapshot mais recente em `fii_fundamentals` via `DISTINCT ON`, 1 query bulk para todos os FIIs do batch).
- `SignalItem` Pydantic ganha 2 campos novos (null para ações/ETFs).
- UI dashboard tab Signals: badges `DY X.X% · PVP Y.YY` na meta column de FIIs (verde quando P/VP<1, cinza caso contrário).
- Filtro novo "FII P/VP<1" como checkbox próximo ao "só BUY/SELL". Validado: 12 FIIs → 8 com filtro ligado.
- Caso ideal observado: RECT11 BUY com P/VP=0.43 + DY=12.63%.
- **Decisão pragmática**: retreino ML real (`features_daily_builder` ingerindo dy_ttm/p_vp como features) fica adiado — `fii_fundamentals` só tem 1 snapshot, LightGBM precisa de variação histórica. Job diário acumula dados; em ~30-90d virá **N5c** (retreino com snapshots empilhados).

### N7 — Sino topbar em /diario ✅ DONE
- `notifications.js` agora aceita fallback `[data-fa-notif-host]` quando `.fa-topbar` não existe + `[data-fa-notif-anchor]` para posicionamento.
- `diario.html`: `dj-header` marcado com `data-fa-notif-host`; botão "+ Novo Trade" com `data-fa-notif-anchor`.
- Validado via Playwright: sino aparece no header, antes do botão "+ Novo Trade".

### N9 — Validar S/R em ticker com dados limpos ✅ DONE
- Smoke nos 6 tickers DLL pós-N1: todos retornam `outliers_dropped=0` e `data_quality_warning=null`.
- Williams Fractais retorna 8-11 fractais por ticker; classic.pp coerente com `last_close`.
- swing_levels=0 nos 6 (algoritmo de clusters precisa de pivots repetidos; janela 66-71 candles é curta — comportamento normal, não bug).

### N8 — Fix renderADX null em lightweight-charts ✅ DONE
- Bug em `dashboard.html:2234`: `ref25 = timestamps.map(t => ({time:t, value:25}))` não filtrava timestamps null do warm-up do ADX (~14 bars iniciais).
- Fix: aplicar mesmo padrão `.filter(Boolean)` das outras linhas (adxLine/diPlusLine/diMinusLine) + converter strings → unix timestamps.
- Reprodução validada via Playwright: `setData([{time:null,...}])` → `"Cannot read properties of null (reading 'year')"`. Pós-fix: `null` rejeitado, setData OK.

---

## ✅ ENTREGUES (sessão 27/abr)

### M1 — ML para FIIs ✅ DONE
**Tempo real**: 50min (estimativa original: 1 dia)

- Backfill Yahoo daily de 26 FIIs IFIX (`scripts/backfill_yahoo_fii.py`)
- Calibração + treino MVP-h21 (top sharpe: HFOF11 +2.55, KNRI11 +1.33, RECT11 +1.46)
- Coluna `asset_class` em `ticker_ml_config` (migration ad-hoc no TS)
- Endpoint `/api/v1/ml/signals?asset_class=fii` filtra por classe
- Badge amarelo `FII` na lista de signals do /dashboard
- 26/30 FIIs (MALL11 e BCFF11 delistados no Yahoo)

**Limitações conhecidas** (escopo Sprint 2):
- Sem fundamentals FII (DY, P/VP) — Fintz não cobre, Status Invest scraper não implementado
- Histórico só 2 anos (Yahoo) — train=73d / val=85d / test=141d
- Features RF (DI1) não incluídas no MVP — flag `--no-rf` removível em retreino

### M2 — ML para ETFs ✅ DONE
**Tempo real**: 25min (estimativa: 2h)

- Backfill 13 ETFs B3 (`backfill_yahoo_etf.py` reusa pipeline FII)
- Calibração + treino: BOVB11 +2.70, GOVE11 +2.54, BOVV11 +2.51, FIND11 +2.24
- Badge azul `ETF` na lista de signals
- USPD11 e B5P211 ficaram fora (delistado / sem trades válidos)

**Observação estratégica**: BOVA11/BOVV11/BOVB11 trackeiam IBOV — sinal redundante com TSMOM no Grafana. Setoriais (FIND11, GOVE11) e RF (IMAB11) são os mais úteis.

### M3 — Fundos CVM analytics ✅ DONE
**Tempo real**: 50min (estimativa: 3-5 dias)

Backend + UI completos:
- `domain/fundos/analytics.py` (Python+numpy puro): 3 algoritmos
  - **style_analysis** (OLS via lstsq): regressão fundo vs fatores → R²/alpha/betas/peso%
  - **peer_ranking** (sharpe annualized): top-N fundos da classe
  - **nav_anomalies** (z-score rolling 30d, threshold 3σ): saltos suspeitos
- 3 endpoints sob prefix dedicado `/api/v1/fundos-analytics/` (evita conflito com `/{cnpj:path}` greedy)
- UI `/fundos` aba "Analytics — Peer Ranking" com botão Analisar que expande Style + Anomalies inline

**Validação**: PODIUM (FI Crédito Privado) → R² 0.04 (esperado — fundo crédito tem pouca correlação com fatores), alpha 14.66% a.a., **57% IMAB11** ✅

**Limitações**:
- Informes CVM cobrem só jan-abr/2024 (preciso sync mensal automático — `POST /sync/informe?competencia=AAAAMM` está manual)
- Fatores ETFs em `features_daily` começam 2024-03-28; overlap com fundos limitado
- Threshold de overlap reduzido para 20 obs (de 30) acomodar janela curta

### M4 — Crypto signal ✅ DONE
**Tempo real**: 25min (estimativa: 2 dias)

- Endpoint `/api/v1/crypto/signal/{symbol}` — score weighted dos 4 indicadores existentes (RSI/MACD/EMA cross/Bollinger) → BUY/SELL/HOLD
- Coluna `Sinal` na aba Crypto do /carteira com badge colorido + tooltip de breakdown
- Snapping de `days` no CoinGecko (só aceita 1/7/14/30/90/180/365)

**Validação BTC**: price $77.445 · score=-2 · HOLD (RSI 51 -1, MACD bullish +1, EMA9<EMA21 -1, BB sobrecomprado -1)

**Limitações**:
- CoinGecko OHLC retorna apenas ~45 candles em 180d (granularidade reduzida em janelas longas)
- Sem persistência local — sempre fetch on-demand (rate limit 30 req/min)

### M5 — RF Regime classifier ✅ DONE
**Tempo real**: 25min (estimativa: 3 dias)

- Módulo `domain/rf_regime/classifier.py` — 4 regimes determinísticos:
  - **INVERSION** (slope < -0.5bp) → 70% CDI / 20% Pré / 10% IPCA
  - **STEEPENING** (slope > 0 + delta z > +1σ) → 30 / 50 / 20
  - **FLATTENING** (slope ≥ 0 + delta z < -1σ) → 20 / 20 / 60
  - **NORMAL** (default) → 30 / 30 / 40
- Endpoint `/api/v1/rf/regime?history_days=N&lookback_days=M`
- Card visual no /carteira aba RF: borda colorida + headline emoji + slope/z/score + 3 chips de alocação

**Estado atual** (2026-04-17): regime=**NORMAL**, score=85%, slope +33bp, z=-0.15

**Trade-off da decisão**: HMM real seria mais robusto (transição probabilística) mas custa lib pesada + treino. Determinístico entrega 80% do payoff em 0% do tempo.

---

## 🔄 NOVO BACKLOG (descoberto na sessão de 28/abr — pós C.2 Sudo)

#### P1 — Broker subconnection com blips intermitentes "Cliente não logado" ⭐⭐⭐ crítico
**Custo**: ~1d investigação + ~1d fix. **Payoff**: crítico (desbloqueia 80% do Bloco B do Roteiro de Testes).

Sintoma observado na sessão pregão 28/abr (~13:00-13:11 BRT): aproximadamente 30% dos `SendOrder`/`SendChangeOrder` recebem rejeição do broker com `trading_msg code=3 status=8 msg=Cliente não está logado` (mesmo `msg_id` da operação que tinha aparecido antes como `code=2 Enviando ao HadesProxy`). DLL retorna ret=0 (sucesso local), mas a operação NUNCA chega ao broker — fica órfã: DB tem com status PendingNew, DLL EnumerateAllOrders não conhece, `cancel_order` retorna `-2147483645` (NL_INVALID_ARGS).

**Padrão**:
- Send/change/cancel da mãe-fresh: ~30% rejection
- Pares de SetOrder + EnumerateAllOrders por reconcile: ~5% rejection (auto-recupera)
- `EnumerateAllOrders` degrada para `ok=False orders=N` após blips acumulados
- `routing_connected=true` permanece true durante blips → status callback não detecta a queda

**Diagnóstico esperado**: a `cstRoteamento` callback dispara `crBrokerConnected` no boot e o agent assume que o estado se mantém. Em produção, o broker subconnection (entre DLL e HadesProxy/Nelogica server) tem micro-disconnects que NÃO disparam `crDisconnected`, mas que rejeitam ordens enviadas durante a janela. O Delphi reference client mostrou padrão similar (`caInvalid` espontâneo às 12:18:29 em sessão saudável).

**Opções de fix**:
1. **Auto-retry em "Cliente não logado"**: detectar `trading_msg code=3 status=8 msg=Cliente não está logado` no callback, marcar a ordem como `auth_blip` no DB, agendar re-send em 2s. Limit 3 tentativas → fail final.
2. **Health probe de broker subconnection**: a cada 30s, mandar `GetAccountCount` (ou outro lightweight DLL call). Se retorna NL_ERR, marcar `routing_connected=false` e disparar reconnect.
3. **Reconnect explícito**: ao detectar blip, chamar `DLLFinalize` + `DLLInitializeLogin` em background, resetar handlers. Disrutivo mas robusto.
4. **Fallback Delphi-like**: se broker-subscription drops, reabrir a conta com `OpenAccount(broker_id, account_id)` (ou equivalente).

**Recomendado**: opção 1 (low-risk, recovery automático) + opção 2 (detecta/loga problema). Opções 3-4 só se 1+2 não bastarem.

**Impacto no Roteiro**: Bloco B itens B.6.6-B.6.8 (fill mãe + dispatch), B.7-B.12 (active OCO + trailing + cross-cancel + persist), B.18 (fill + diary), B.19 (flatten) ficam frágeis até fix.

#### P2 — Reconcile UPDATE só funciona com cl_ord_id, mas envio inicial grava NULL ⭐⭐ alto
**Custo**: ~1h. **Payoff**: alto (DB stale → user vê status PendingNew enquanto broker já cancelou/preencheu).

Em `profit_agent.py:3566`, o handler `/positions/dll` faz:
```python
if not o["cl_ord_id"]:
    continue
self._db.execute("UPDATE profit_orders SET ... WHERE cl_ord_id=%s", (..., o["cl_ord_id"]))
```

Mas no `/order/send` (handler que grava no DB ao enviar), o `cl_ord_id` é gravado como NULL inicialmente — só vem do broker depois via order_callback. Resultado: o reconcile **nunca atualiza** orders enviados pelo agent, porque DB tem `cl_ord_id=NULL` e DLL tem `cl_ord_id='NLGC.150...'` → `WHERE NULL = 'NLGC...'` → 0 rows.

**Sintoma**: ordem cancelada no DLL (`status=4 CANCELED`), mas DB fica em `status=10 PendingNew` permanentemente. UI fica mostrando "pendente" enquanto na realidade o broker já cancelou.

**Fix**:
```python
# Match by local_order_id (sempre presente) em vez de cl_ord_id
self._db.execute(
    "UPDATE profit_orders SET order_status=%s, traded_qty=COALESCE(%s,traded_qty),"
    " leaves_qty=COALESCE(%s,leaves_qty), avg_price=COALESCE(%s,avg_price),"
    " cl_ord_id=COALESCE(%s,cl_ord_id), updated_at=NOW()"
    " WHERE local_order_id=%s",
    (o["order_status"], o["traded_qty"] or None, o["leaves_qty"] or None, o["avg_price"], o["cl_ord_id"] or None, o["local_id"])
)
```

Bonus: também atualiza o cl_ord_id no DB quando ele aparece (resolve drift permanente).

#### P3 — di1_realtime_worker cursor stuck em trade_number antigo após reset de sessão B3 ⭐ médio
**Custo**: ~1h. **Payoff**: médio (Kafka stream `market.rates.di1` zerado durante o dia; não impacta DB direto, mas pipelines downstream que dependem do tópico ficam stale).

Em `workers/di1_realtime_worker.py` (ou similar), no boot o worker faz:
```python
last_trade_number = SELECT MAX(trade_number) FROM profit_ticks WHERE ticker = 'DI1FXX'
poll loop: SELECT * FROM profit_ticks WHERE trade_number > $last_trade_number
```

Problema: B3 resetau `trade_number` por sessão. Se worker boota durante uma sessão nova com poucos ticks, ele pega como `last` o MAX da sessão **anterior** (que ficou no DB). Os ticks novos vêm com `trade_number < last` → query nunca retorna, `ticks_total=0` indefinidamente.

Confirmado live (28/abr 12:44 BRT):
- DI1F27: worker init `last_trade_number=314920` (antigo), MAX atual = `139600` (sessão atual)
- worker uptime 45min, ticks_total=0, kafka_published=0

**Fix**: usar `(time, trade_number)` composto como cursor. OU filtrar por `time > worker_start_time` (mais simples).

```python
worker_start = datetime.now(tz=UTC)
poll: SELECT * FROM profit_ticks WHERE ticker = ? AND time > $worker_start ORDER BY time, trade_number
```

#### N13 — Gmail briefings → enrichment de signals/dashboard ⭐⭐ alto payoff
**Custo**: ~1d MVP / ~3-5d completo. **Payoff**: alto (research institucional vira sinal acionável).

User recebe daily briefings de corretoras (BTG morning, XP top picks, Itaú research, newsletters) que hoje ficam só no inbox. Pipeline pra extrair sinais e enriquecer dashboard.

**Stack proposto**:
1. **Worker scheduler** novo (`gmail_briefings_job`, ~10min interval) lê Gmail via:
   - **IMAP** (mais simples, app password): `imaplib` stdlib
   - **Gmail API** (mais robusto, OAuth2): `google-api-python-client`
   - Decisão: começar IMAP por simplicidade, migrar pra API se precisar de filtros server-side
2. **Parser híbrido** em `application/services/gmail_briefings_service.py`:
   - **Regex** pra senders mapeados (~20 corretoras com formato estável)
   - **LLM fallback** pra senders novos / conteúdo livre — usar **LLM local na RTX 4090** (Decisão 15: compute na GPU 0). Modelo sugerido: Llama 3.1 8B ou similar via vLLM/llama.cpp
   - **Por que LLM local**: emails têm saldos/posições/CPF — privacidade exige não trafegar pra cloud
3. **Schema**: tabela `market_briefings (id, received_at, source, sender, subject, tickers[], sentiment, action ENUM(buy/sell/hold/neutral), summary TEXT, raw_text TEXT, processed_at)` no TimescaleDB. Hypertable particionada por `received_at`.
4. **Integração**:
   - Badge no `/signals` ("📧 BTG segue overweight PETR4 hoje (3 corretoras)")
   - Card em `/dashboard` consolidando últimas 24h
   - AlertService → Pushover priority=1 quando ≥3 fontes convergem em mesma ação pra mesmo ticker
   - Coluna nova em `signal_history` referenciando `briefing_ids[]`

**Tradeoffs**:
- **Acurácia vs cobertura**: regex puro 95%/baixa cobertura; LLM em tudo 80%/alta. Híbrido melhor balance mas mais complexo
- **Compliance**: user reage ao research, não republica → OK. Mas **não** revender/redistribuir conteúdo extraído
- **Custo**: zero com LLM local; Claude API custaria R$10-30/mês

**MVP (~1d)**:
- 1 fonte conhecida (escolher: BTG morning brief? XP top picks? newsletter X?)
- Regex parser específico
- Tabela + endpoint `/api/v1/briefings/recent`
- Badge no /dashboard signals

**V2 (~2-3d adicional)**:
- LLM local fallback
- 5-10 fontes mapeadas
- Sentiment scoring
- AlertService convergence detection

**V3 (~2d adicional)**:
- Backtesting: briefing recommends X → X retorno em 5/10/30d? Quais fontes alpha real?
- Filtro de fontes ruins (>30% errada vira opt-out automático)

**Pré-requisito**: user define quais 1-2 fontes começar (criar issue ou listar aqui antes de implementar).

#### O1 — Zombie processes do profit_agent em restart NSSM ⭐ médio
**Custo**: ~2-3h. **Payoff**: médio (evita memory leak ao longo de semanas).

NSSM watchdog instalado e funcional (commit do install_nssm_service.ps1 + Service FinAnalyticsAgent rodando como LocalSystem). Auto-recovery confirmado: cada `/agent/restart` muda PID e `/health` volta em segundos.

Mas: cada restart deixa **pares Python zombie** (parent+child) que não morrem com `os._exit(0)`. Causa raiz: DLL ConnectorThread (C++ nativa, gerida pela ProfitDLL) bloqueia o exit do interpretador Python. Os zombies não estão listening em nenhuma porta nem fazendo I/O, mas consomem RAM (~100-200MB cada par, dependendo do que estava em cache).

Cenário: 1 restart/dia × 30 dias × ~150MB = 4.5GB de zombies em 1 mês.

**Opções de fix**:
1. **TerminateProcess via ctypes**: substituir `os._exit(0)` por `kernel32.TerminateProcess(GetCurrentProcess(), 0)` — mata sem chance de cleanup, força DLL a morrer
2. **DLL Finalize antes do exit**: adicionar `dll.Finalize()` ou `dll.DLLFinalize()` no handler antes do `_exit`. Precisa descobrir API correta da Profit
3. **NSSM AppKillProcessTree=1**: configurar NSSM pra matar arvore de processos no shutdown — mata pais e filhos juntos

Probably **opção 1 é mais robusta** (não depende de API Profit não documentada). Validar em ambiente de teste antes de aplicar.

---

## 🔄 NOVO BACKLOG (descoberto na sessão de 27/abr)

Itens identificados durante implementação/validação. Ordenados por ROI dentro de cada bloco.

### Data quality

#### N1 — Limpeza `profit_daily_bars` escala mista ✅ DONE 27/abr madrugada (ver topo)
~~Investigar/regenerar — DONE.~~ Causa raiz: ticks brutos com escala /100 intermitente. Fix: regenerar via `--source 1m`. Backup em `profit_daily_bars_backup_27abr`.

#### N1-old — Limpeza `profit_daily_bars` escala mista ⭐⭐ alto impacto (referência)
**Custo**: ~2-3h investigação + script. **Payoff**: alto (desbloqueia S/R swings/williams + qualquer agregado daily da DLL).

PETR4 tem 64 rows mas **62 com escala fracionária 0.36** e só **2 com escala correta 48**. Provavelmente quirk da DLL Profit (close/close_ajustado misturados ou split factor dinâmico — nota em `features_daily_builder.py:207-213`).

Hoje o endpoint `/indicators/{ticker}/levels` filtra outliers e retorna `data_quality_warning` com `swing/williams=null` em 95% dos casos. Mitigação efetiva mas não resolve.

**Ação**: investigar origem (DLL retorna assim ou bug do `populate_daily_bars.py`?) e regenerar tabela. Pode ser que `--source 1m` produza dados limpos.

#### N2 — CVM informe diário sync agendado ✅ DONE 27/abr madrugada (ver topo)

#### N2-old — CVM informe diário sync agendado ⭐ trivial (referência)
**Custo**: ~30min. **Payoff**: alto (libera analytics M3 com dados frescos).

Hoje `POST /api/v1/fundos/sync/informe?competencia=AAAAMM` é manual. Última sincronização: jan-abr/2024.

**Ação**: adicionar job no `scheduler_worker.py`: roda mensal no dia 5 com `competencia` do mês anterior. Reusa serviço existente.

### ML & Sinais

#### N3 — Multi-horizonte ML (h3/h5/h21) ⭐⭐ aguarda Nelogica
**Custo**: ~1d com pickles + ensemble. **Payoff**: alto (reduz dependência de h21 único).

Já existe `predict_ensemble` no roteiro (Z4) mas só faz fallback uniforme porque pickles h3/h5 não foram treinados (precisa `ohlc_1m` completo). Quando Nelogica chegar (item 20 do CLAUDE.md), treinar.

#### N4 — HMM real para RF Regime (substituir M5) ⭐ baixa prioridade
**Custo**: ~3 dias (lib hmmlearn + treino dos estados + tuning). **Payoff**: marginal sobre o determinístico.

M5 atual usa regras fixas. HMM permitiria descobrir regimes empíricos + transições probabilísticas. Vale só se houver evidência de que regras determinísticas perdem regimes intermediários relevantes.

#### N5 — Fundamentals FII (DY corrente, P/VP) ✅ DONE 27/abr madrugada (parcial — falta integração ML como N5b)

#### N5-old — Fundamentals FII (DY corrente, P/VP) ⭐⭐ habilita alpha real M1 (referência)
**Custo**: ~1 dia (Status Invest scraper + tabela + integração ml_features). **Payoff**: alto.

M1 hoje usa só features técnicas. Adicionar **DY TTM** (dividends últimos 12m / preço) e **P/VP** (preço / valor patrimonial cota) é o que diferencia FII bom de FII ruim. Requer scraper porque Fintz não cobre.

#### N6 — Crypto persistence + horizons h1/h6 ⭐ médio
**Custo**: ~2 dias. **Payoff**: médio (timing de aporte BTC, mas só 1 holding hoje).

Hoje `/api/v1/crypto/signal/{symbol}` é on-demand sem persistência. Para ter sinais multi-horizonte intraday precisa:
- Worker que persiste OHLC CoinGecko (5min) em tabela `crypto_ohlc_5m`
- Computar indicadores em h1/h6/h24 separadamente
- Endpoint `/crypto/signal/{symbol}/{horizon}`

### UI & Bugs menores

#### N7 — Sino topbar em /diario ✅ DONE 27/abr madrugada (ver topo)

#### N7-old — Sino topbar em /diario ⭐ trivial bug fix (referência)
**Custo**: ~10min. **Payoff**: baixo (só afeta exibição visual do contador de pendentes em /diario).

`notifications.js` injeta `fa-notif-btn` apenas em páginas com `.fa-topbar`. /diario tem topbar custom (não a canônica). Correção: garantir `.fa-topbar` no diario.html ou adaptar notifications.js.

#### N8 — Fix renderADX null pré-existente ✅ DONE 27/abr madrugada (ver topo)

#### N8-old — Fix renderADX null pré-existente ⭐ baixa prioridade (referência)
**Custo**: ~30min debug. **Payoff**: baixo (erro console, sem impacto funcional).

Erro pré-existente: `TypeError: Cannot read properties of null (reading 'year')` em `lightweight-charts setData` (renderADX). Provavelmente passa ts inválido em algum candle. Reproduce confiável: ativar/desativar S/R toggle no popup.

#### N9 — Validar S/R em ticker com dados limpos ✅ DONE 27/abr madrugada (ver topo)

#### N9-old — Validar S/R em ticker com dados limpos ⭐ trivial (referência)
**Custo**: ~10min. **Payoff**: confirmação.

A.22.4 do roteiro: testar `/levels` em ticker que NÃO tem o bug do profit_daily_bars (ex: VALE3 ou outro fora da DLL Profit). Esperado: `warning=null`, swing/williams retornam normalmente.

### ML para outras classes (do backlog antigo, não atacados)

#### N10 — ML para outros tipos CVM (FIDC, FIP) ⭐ futuro
**Custo**: ~2 dias. **Payoff**: nichado.

M3 entregou peer ranking + style + anomalies para Multimercado/Ações/RF/FII. Estender para FIDC/FIP requer adaptações (estrutura de cota diferente, distribuições periódicas, vencimento das CCBs).

---

## Notas

- **Ordem ideal próxima sprint**: N1 (data quality bloqueia S/R) → N2 (libera M3 com dados atuais) → N5 (alpha real para M1)
- **Quando atacar**: off-hours ou início de sprint planejada. Nenhum bloqueia operação atual.
- **Backlog M1-M5 zerado**: tempo total real ~3h15 vs estimativa original 8-13 dias — economia 95% pela reutilização do pipeline ML existente + algoritmos determinísticos vs HMM/treino pesado.

---

_Criado: 26/abr/2026 (super-sessão noite)_
_Atualizado: 27/abr/2026 noite (M1-M5 done + novos itens N1-N10)_
_Atualizado: 27/abr/2026 madrugada (N1+N2+N5+N7+N8+N9 done; N5b pendente — integração ML dos fundamentals FII)_
