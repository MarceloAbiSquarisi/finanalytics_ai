# Backlog de Melhorias — FinAnalytics AI

> Lista priorizada de melhorias. Itens entregues sumarizados no topo; novos itens descobertos no rodapé.
>
> **Última revisão**: 27/abr/2026 noite — sessão M1-M5 + features /diario + /dashboard

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

## 🔄 NOVO BACKLOG (descoberto na sessão de 27/abr)

Itens identificados durante implementação/validação. Ordenados por ROI dentro de cada bloco.

### Data quality

#### N1 — Limpeza `profit_daily_bars` escala mista ⭐⭐ alto impacto
**Custo**: ~2-3h investigação + script. **Payoff**: alto (desbloqueia S/R swings/williams + qualquer agregado daily da DLL).

PETR4 tem 64 rows mas **62 com escala fracionária 0.36** e só **2 com escala correta 48**. Provavelmente quirk da DLL Profit (close/close_ajustado misturados ou split factor dinâmico — nota em `features_daily_builder.py:207-213`).

Hoje o endpoint `/indicators/{ticker}/levels` filtra outliers e retorna `data_quality_warning` com `swing/williams=null` em 95% dos casos. Mitigação efetiva mas não resolve.

**Ação**: investigar origem (DLL retorna assim ou bug do `populate_daily_bars.py`?) e regenerar tabela. Pode ser que `--source 1m` produza dados limpos.

#### N2 — CVM informe diário sync agendado ⭐ trivial
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

#### N5 — Fundamentals FII (DY corrente, P/VP) ⭐⭐ habilita alpha real M1
**Custo**: ~1 dia (Status Invest scraper + tabela + integração ml_features). **Payoff**: alto.

M1 hoje usa só features técnicas. Adicionar **DY TTM** (dividends últimos 12m / preço) e **P/VP** (preço / valor patrimonial cota) é o que diferencia FII bom de FII ruim. Requer scraper porque Fintz não cobre.

#### N6 — Crypto persistence + horizons h1/h6 ⭐ médio
**Custo**: ~2 dias. **Payoff**: médio (timing de aporte BTC, mas só 1 holding hoje).

Hoje `/api/v1/crypto/signal/{symbol}` é on-demand sem persistência. Para ter sinais multi-horizonte intraday precisa:
- Worker que persiste OHLC CoinGecko (5min) em tabela `crypto_ohlc_5m`
- Computar indicadores em h1/h6/h24 separadamente
- Endpoint `/crypto/signal/{symbol}/{horizon}`

### UI & Bugs menores

#### N7 — Sino topbar em /diario ⭐ trivial bug fix
**Custo**: ~10min. **Payoff**: baixo (só afeta exibição visual do contador de pendentes em /diario).

`notifications.js` injeta `fa-notif-btn` apenas em páginas com `.fa-topbar`. /diario tem topbar custom (não a canônica). Correção: garantir `.fa-topbar` no diario.html ou adaptar notifications.js.

#### N8 — Fix renderADX null pré-existente ⭐ baixa prioridade
**Custo**: ~30min debug. **Payoff**: baixo (erro console, sem impacto funcional).

Erro pré-existente: `TypeError: Cannot read properties of null (reading 'year')` em `lightweight-charts setData` (renderADX). Provavelmente passa ts inválido em algum candle. Reproduce confiável: ativar/desativar S/R toggle no popup.

#### N9 — Validar S/R em ticker com dados limpos ⭐ trivial
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
