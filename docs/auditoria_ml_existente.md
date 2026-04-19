# Auditoria ML/Backtest existente — Sprint 10

> Executada em 19/abr/2026, antes de iniciar o scaffold do MVP R10.

## O que já existe

### `src/finanalytics_ai/application/ml/` (834 linhas)

| Arquivo | Conteúdo | Cobertura vs §14 |
|---|---|---|
| `feature_pipeline.py` (142) | `compute_returns`, `compute_volatility_21d`, `compute_rsi_14`, `compute_beta_60d`, `compute_volume_ratio`, `build_features_from_ohlc` — puras, sem I/O | **Cobre r_1d/5d/21d, vol_21d, rsi_14, vol_rel_20** do schema §14 passo 2. Falta apenas `atr_14`, `sma_50`, `sma_200`. |
| `ml_service.py` (219) | Classe `MLService`: `compute_features(tickers, date)` — busca OHLC → computa features | Orquestração in-memory; ainda não materializa em `features_daily` |
| `return_forecaster.py` (211) | `QuantileForecaster` **LightGBM Quantile (P10/P50/P90)** + `TrainingDataRow` + serialização (pickle bytes) | §14 passo 5 pede "LightGBM" — **cobre**. Preserva modelo via `serialize()/deserialize()` |
| `ml_strategy.py` (112) | `MLStrategy.evaluate(forecast, risk) -> StrategySignal`; score = `prob_positive × p50/var_consensus`; thresholds calibrados para IBOV | Cobre a camada de sinal acionável |
| `risk_estimator.py` (150) | `RiskEstimator`: histórico, t-Student, GARCH(1,1), Monte Carlo 100k sims | Cobre VaR/CVaR probabilístico — complemento ao forecaster |

### `src/finanalytics_ai/domain/backtesting/` (1297 linhas)

| Arquivo | Conteúdo |
|---|---|
| `engine.py` (386) | `Signal` enum, `Trade`/`BacktestMetrics`/`BacktestResult` (dataclasses imutáveis), **`Strategy` como `Protocol` (duck typing)** com `.generate_signals(bars) -> list[Signal]` — já no formato pedido pelo §14 passo 4. `run_backtest()` + `_calc_metrics()` prontos. |
| `multi_ticker.py` (253) | Backtest multi-ativo (carteira) |
| `optimizer.py` (658) | Grid search / walk-forward de parâmetros |
| `strategies/technical.py` | **19 estratégias** já implementando o Protocol: RSI, MACD Cross, Combined, Bollinger Bands, EMA Cross, Momentum, Pin Bar, Inside Bar, Engulfing, Fakey, Setup 9.1, Larry Williams, Turtle Soup, Hilo Activator, Breakout, Pullback in Trend, First Pullback, Gap and Go, Bollinger Squeeze |

### `src/finanalytics_ai/interfaces/api/routes/` (1080 linhas ML)

| Arquivo | Conteúdo |
|---|---|
| `backtest.py` (542) | Endpoints de backtest (provavelmente `/backtest/run` + listagens) |
| `forecast.py` (193) | Endpoints de forecast (possível `/forecast/predict` — precisa confirmar escopo) |
| `ml_forecasting.py` (345) | Endpoints de ML forecasting (possível `/ml/...`) |

## Gap vs §14

| Passo §14 | Estado | Ação scaffold |
|---|---|---|
| 1. Auditar | ✅ feito (este documento) | — |
| 2. Schema `features_daily` (hypertable) | ❌ não existe | **Criar** em `Melhorias/sprint10_features_daily.sql` |
| 3. `scripts/features_daily_builder.py` | ❌ não existe | **Criar**, usando `feature_pipeline.build_features_from_ohlc` + leitura de `profit_daily_cov`/`fintz_cotacoes_ts` |
| 4. Padronizar interface Strategy | ✅ já padronizada como Protocol (`engine.py:Strategy`) | Só documentar |
| 5. MVP PETR4 (LightGBM r_1d) | Peças prontas (`QuantileForecaster`, features), pipeline end-to-end **não integrado** com `features_daily` | Pipeline de demonstração usando infra existente |
| 6. Endpoint `/predict` | Possivelmente existe em `forecast.py` ou `ml_forecasting.py` | Conferir + expor `/ml/predict/{ticker}` se faltar |
| 7. Registrar modelo | `QuantileForecaster.serialize() -> bytes` existe | Padronizar pickle em `models/` + metadata JSON |
| 8. Runbook | ❌ não existe | **Criar** `Melhorias/runbook_R10_modelos.md` |

## Features do §14 passo 2 vs `feature_pipeline.py`

| Feature §14 | Em `feature_pipeline.py`? |
|---|---|
| `r_1d`, `r_5d`, `r_21d` | ✅ `compute_returns(closes, window)` |
| `vol_21d` | ✅ `compute_volatility_21d` |
| `vol_rel_20` | ✅ `compute_volume_ratio(volumes, window=21)` (~equivalente) |
| `rsi_14` | ✅ `compute_rsi_14` (Wilder) |
| `close`, `sma_50`, `sma_200` | ⚠️ `close` é input; `sma_50/200` **não estão** — fáceis de adicionar |
| `atr_14` | ⚠️ **não existe** — fácil (requer high/low/close) |

## Estratégia MVP

1. Criar `features_daily` (hypertable) + `features_daily_builder.py` lendo de **`fintz_cotacoes_ts`** (2010→2025-12-30) + **`profit_daily_cov`** agregando para daily (2026-01-02→hoje). Computa features via `build_features_from_ohlc` + `sma_50/200` + `atr_14` adicionados se necessário.
2. Usar `QuantileForecaster` existente — split train 2020–2023 / val 2024 / test 2025 (validação estrutural apenas; IC real só pós-S1).
3. Reusar `MLStrategy` para sinal, `run_backtest()` para walk-forward.
4. Endpoint `/predict` confirmado/criado.
5. Runbook documentando: `python scripts/features_daily_builder.py --backfill --start 2020-01-02`, treino, serving.

## Risco

- **Dados incompletos** (S1 ainda em backfill) — pipeline vai rodar com histórico parcial; IC/Sharpe não serão KPI válidos até S1 completar.
- **Fintz estagnado** em 2025-12-30 — necessário juntar Fintz (≤2025) + ProfitDLL aggregado (>=2026-01) para ter série contínua.
