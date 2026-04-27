# Backlog de Melhorias — FinAnalytics AI

> Lista priorizada de melhorias a implementar quando houver tempo (off-hours, fora da super-sessão).
> Ordem dentro de cada bloco = recomendação de ROI/custo.

---

## ML para outras classes de ativo

### M1 — ML para FIIs (via Yahoo daily) ⭐ prioridade
**Custo**: ~1 dia. **Payoff**: alto (FIIs têm alpha real por mispricing recorrente de P/VP).

- Pipeline MVP-h21 funciona em daily — não precisa esperar Nelogica (Yahoo `KNRI11.SA` etc cobre IFIX).
- Adicionar tickers IFIX (~30-50) em `ticker_ml_config`.
- Features extras específicas de FII:
  - **DY corrente** (último dividendo / preço × 12)
  - **Prêmio/desconto sobre P/VP** (preço / VP_cota último relatório)
  - Regime de juros (já temos via DI1 worker — feature derivada da curva)
- Reusar `calibrate_ml_thresholds.py` por ticker.
- Endpoint: `/api/v1/ml/signals?asset_class=fii` (filtra por classe).
- Snapshot diário no `signal_history` com `asset_class='fii'`.

### M2 — ML para ETFs ⭐ trivial
**Custo**: ~2h. **Payoff**: moderado (ETFs trackeiam índice — alpha limitado).

- Adicionar tickers (BOVA11, IVVB11, SMAL11, DIVO11, FIND11, etc) em `ticker_ml_config`.
- Pipeline MVP-stocks copia direto (DLL/Yahoo já cobrem).
- Sinal mais útil: **regime/momentum no índice subjacente** (já temos TSMOM no Grafana) → rotacionar entre ETFs em vez de prever cada um.
- Cuidado: BOVA11 ≈ IBOV. Sinal "BUY BOVA11" é redundante com "IBOV uptrend".

### M3 — ML para Fundos de Investimento (CVM) ⭐⭐ pipeline novo
**Custo**: ~3-5 dias. **Payoff**: alto pra quem tem alocação relevante em fundos.

- Paradigma diferente: NAV diário pós-fechamento, sem OHLC, sem intraday.
- **Predição supervisionada falha** (estratégia do gestor é opaca) — focar em:
  - **Style analysis**: regressão retorno do fundo vs fatores (CDI / IBOV / IPCA+ / USD / SMLL) → revela "multimercado vendendo RF disfarçada"
  - **Peer ranking** por sharpe/sortino dentro da categoria CVM (multimercado, RF, ações)
  - **Alpha persistence**: rolling 6m sharpe vs benchmark — fundos com persistência ≠ sorte
  - **NAV anomaly detection**: salto >3σ no daily return → suspeita de marcação errada
- **Ingestor novo**: CVM publica `inf_diario_fi_AAAAMM.csv` mensalmente (gratuito, ~2GB/ano).
  - Source: https://dados.cvm.gov.br/dataset/fi-doc-inf_diario
  - Tabela nova: `cvm_fundos_nav` (hypertable por data/cnpj_fundo).
- Endpoints novos:
  - `/api/v1/funds/style/{cnpj}` — coeficientes de exposição a fatores
  - `/api/v1/funds/peer_ranking?categoria=multimercado&window=6m`
  - `/api/v1/funds/anomalies` — fundos com NAV jump suspeito

### M4 — ML para Crypto ⭐ médio
**Custo**: ~2 dias. **Payoff**: médio (1 holding BTC hoje, mas relevante pra timing).

- Horizon diferente do MVP-stocks: h1/h6/h24 (mercado 24/7, vol 5x).
- Pipeline próprio com histórico CoinGecko (free tier).
- Endpoint: `/api/v1/ml/crypto_signals/{symbol}` separado.
- Útil pra timing de aporte/resgate (tabela `crypto_holdings`).

### M5 — Renda Fixa: regime de curva ⭐⭐ paradigma diferente
**Custo**: ~3 dias. **Payoff**: alto pra quem rebalanceia entre indexadores.

- Não é predição de preço — usar DI1 worker que já está vivo.
- Modelo: **HMM** ou **regression on yield slope** (DI1F27/28/29) → detecta:
  - **Steepening** (slope abrindo) → favorece prefixado curto
  - **Flattening** → favorece IPCA+ longo
  - **Inversion** → defensivo (CDI)
- Sinal: "rotacionar prefixado ↔ IPCA+ ↔ CDI".
- Endpoint: `/api/v1/rf/regime` — estado atual + recomendação de indexador.
- Integra com `rf_holdings` (mostrar no /carteira: "seu fundo IPCA+ está com regime favorável").

---

## Notas

- **Decisão importante**: cada classe de ativo precisa pipeline próprio (features, horizon, threshold) e drift monitoring separado. Não é "1 modelo pra tudo". Custo cresce ~linear no número de classes, mas a infra de retreino/calibração/snapshots já existe (basta parametrizar por `asset_class`).
- **Ordem ideal**: M1 (FII) → M2 (ETF) → M5 (RF curve) → M3 (Fundos CVM) → M4 (Crypto).
- **Quando atacar**: off-hours, fora da super-sessão. Nenhum bloqueia operação atual.

---

_Criado: 26/abr/2026 (super-sessão noite)_
