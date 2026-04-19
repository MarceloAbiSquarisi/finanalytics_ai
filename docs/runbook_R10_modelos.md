# Runbook R10 — Modelos e Backtests (scaffold Sprint 10)

> Status: scaffold funcional validado em 19/abr/2026. DoD parcial (IC bate, Sharpe naive não — ver §Interpretação). KPIs definitivos só pós-Sprint 1 completo.

## Pré-requisitos
- TimescaleDB `finanalytics_timescale` up (porta 5433).
- Conda env `finanalytics-ai` ativa (Python 3.11+, lightgbm ≥ 4.3, scikit-learn ≥ 1.4).
- `features_daily` (hypertable) criada — `Melhorias/sprint10_features_daily.sql` já foi executado.

## 1) Popular `features_daily`

### Incremental (rolling 30d, diário)
```powershell
.venv\Scripts\python.exe scripts\features_daily_builder.py --incremental
```

### Backfill full (watchlist VERDE+AMARELO, 2020-hoje)
```powershell
.venv\Scripts\python.exe scripts\features_daily_builder.py --backfill --start 2020-01-02
```

### Ticker específico (debug / adição pontual)
```powershell
.venv\Scripts\python.exe scripts\features_daily_builder.py --only PETR4 --start 2020-01-02
.venv\Scripts\python.exe scripts\features_daily_builder.py --only "PETR4,VALE3,ITUB4,ABEV3,WEGE3" --start 2020-01-02
```

### Dry-run
```powershell
.venv\Scripts\python.exe scripts\features_daily_builder.py --only PETR4 --start 2024-01-01 --dry-run
```

**Fonte atual (MVP):** apenas `fintz_cotacoes_ts` (2010-01-04 → 2025-12-30; 884 tickers). Profit_daily_bars tem quirk de escala (valores oscilam 0.4 ↔ 49 em dias consecutivos para PETR4) — desativado no builder. Para 2026+ será preciso (a) regenerar `profit_daily_bars` pós-Sprint 1, ou (b) agregar `ohlc_1m tick_agg_v1` diariamente.

## 2) Treinar MVP PETR4 (LightGBM)

```powershell
.venv\Scripts\python.exe scripts\train_petr4_mvp.py --ticker PETR4
```

Splits fixos:
- train = 2020–2023
- val   = 2024
- test  = 2025+ (até última data com feature disponível)

Saída:
- `models/petr4_mvp_<TICKER>_<ts>.pkl` — LGBMRegressor serializado
- `models/petr4_mvp_<TICKER>_<ts>.json` — metadata (features, métricas, target)

### Métricas emitidas
| Métrica | Significado | DoD §14 |
|---|---|---|
| `val_ic_spearman` / `test_ic_spearman` | IC de Spearman entre predição e realizado (r_1d_futuro) | > 0.05 |
| `val_hit_rate` / `test_hit_rate` | Pct de vezes que `sign(pred) == sign(realizado)` | > 0.50 (desejável) |
| `val_sharpe_ls` / `test_sharpe_ls` | Sharpe de estratégia long-short naive (long se pred>0 else short) | > 0 |

### Resultado de referência (19/abr/2026, PETR4, dados Fintz 2020→2025-11-03, 1 257 rows)
- train_size = 795, val_size = 251, test_size = 211
- **val IC = 0.0593**  (bate >0.05)
- **test IC = 0.1106** (bate >0.05)
- val hit_rate = 0.47, test hit_rate = 0.48
- val Sharpe = −0.28, test Sharpe = −0.54

## 3) Interpretação

- **IC positivo e acima do DoD** → modelo captura sinal preditivo de magnitude relativa. Pipeline valido estruturalmente.
- **Sharpe negativo da versão naive** → estratégia `sign(pred) × signal` perde em mercado brasileiro com dados atuais. Soluções produtivas:
  1. Usar `MLStrategy` (`application/ml/ml_strategy.py`) que combina forecast quantílico (P10/P50/P90) + `RiskEstimator` (VaR/CVaR GARCH+MC) via score `prob_positive × p50 / var_consensus`.
  2. Filtrar sinais com magnitude pequena (dead zone) — só operar quando `|pred| > threshold`.
  3. Position sizing proporcional a `pred / vol_21d`.
- **Hit rate <50%** é normal em preditores de magnitude — IC mede quanto você ordena acerto, não quanto acerta direção pontual.

## 4) Serving (endpoint `/predict`) — follow-up

`interfaces/api/routes/forecast.py` (193 linhas) e `routes/ml_forecasting.py` (345) já expõem rotas ML. Ação: identificar o endpoint canônico, garantir que carrega pickle de `models/` mais recente e aceita `{ticker, dia}` → `{predicted_return, confidence}`. Fora do escopo scaffold inicial.

## 5) Backtesting (infraestrutura já existente)

Código:
- `domain/backtesting/engine.py` — `run_backtest(strategy, bars, ticker, capital, fee_bps)` + `BacktestResult`
- `domain/backtesting/strategies/technical.py` — 19 estratégias técnicas
- `domain/backtesting/strategies/README.md` — padrão `Strategy` Protocol (Sprint 10)
- `domain/backtesting/optimizer.py` — grid/walk-forward de parâmetros
- `application/ml/ml_strategy.py` — `MLStrategy` acionável a partir de forecast + risk
- `interfaces/api/routes/backtest.py` — rotas HTTP

Exemplo mínimo: ver docstring em `strategies/README.md`.

## 6) O que ainda precisa acontecer para fechar R10 completo

| Item | Dependência | Esforço |
|---|---|---|
| Expandir `features_daily` para toda watchlist VERDE | builder já aceita `--backfill` | 1 run (~1h, 215 tickers × 15 anos Fintz) |
| Cobertura 2026+ em `features_daily` | (a) regenerar `profit_daily_bars` pós-S1, ou (b) adicionar fonte `ohlc_1m agg` no builder | dep. S1 completar |
| Validar IC/Sharpe com 6 anos completos | S1 completar + re-rodar `train_petr4_mvp.py` | 1 run |
| Testar com mais tickers (VALE3/ITUB4/BBDC4/ABEV3/WEGE3) | features_daily populado | trivial |
| Endpoint `/predict` usando pickle do dia | revisar `forecast.py` / `ml_forecasting.py` | ~1h |
| `MLStrategy` backtest produção | integrar QuantileForecaster + RiskEstimator com data loader de features_daily | ~2h |
| MLflow / registry | decisão infra | out-of-scope scaffold |

## 7) Troubleshooting

### "features_daily vazio após builder"
Checar fonte: `SELECT count(*) FROM fintz_cotacoes_ts WHERE ticker='PETR4'`. Se 0, ticker está em `VERMELHO_sem_profit` ou fora do plano Fintz.

### "ERRO: train set < 50 rows"
`features_daily` ainda não tem histórico suficiente — rode o builder com `--start 2020-01-02` antes do treino.

### Aviso LightGBM "X does not have valid feature names"
Benigno. Causa: passamos `np.ndarray` em vez de `pd.DataFrame`. Não afeta métricas.

### Métricas nulas no val/test
Insuficiência de rows após filtro de `None`. Veja `rows_utilizaveis` no stdout do script.
