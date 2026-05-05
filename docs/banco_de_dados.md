# Banco de Dados — schema + fallback chain

## TimescaleDB (`market_data`)

Tabelas principais:
- `market_history_trades` — ticks históricos (hypertable, partição por trade_date)
- `ohlc_1m` — bars 1m (hypertable; `source` ∈ {brapi, external_1m, nelogica_1m, tick_agg_v1})
- `ohlc_resampled` — N-min bars (hypertable, PK `(time, ticker, interval_minutes)`)
- `profit_daily_bars` — barras diárias OHLCV
- `fintz_cotacoes_ts` — OHLCV diário Fintz (1.32M rows, 200+ tickers, 2010→2025-12-30; **read-only**, freezada)
- `profit_orders` — ordens enviadas via DLL (inclui `validity_type`/`validity_date`)
- `profit_history_tickers` — tickers configurados para backfill (active=True/False)
- `trading_accounts` — contas DayTrade (broker_id+account_id+routing_password)
- `ticker_ml_config` — calibração ML por ticker (118 rows: th_buy/th_sell/best_sharpe/horizon_days/asset_class)
- `signal_history` — snapshots diários de signals (hypertable)
- `fii_fundamentals` — DY TTM/PVP/div_12m/valor_mercado (27 FIIs, refresh 7h BRT)
- `crypto_signals_history` — snapshots BTC/ETH/SOL/etc (refresh 9h BRT)
- `copom_documents` / `copom_sentiment` — pipeline BERTimbau COPOM (vazio até BCB recuperar)

**Robô** (Alembic `ts_0004`, sessão 01/mai):
- `robot_strategies` — registry de strategies (config JSONB + account_id + enabled)
- `robot_signals_log` — auditoria de toda decisão do worker (envio ou skip)
- `robot_orders_intent` — espelho compacto de ordens originadas pelo robô (separa de `profit_orders` manual; liga via `local_order_id`)
- `robot_risk_state` — estado diário de risco + kill switch (`paused`)

## PostgreSQL (`finanalytics`) — multi-tenant

Hierarquia `User → InvestmentAccount → Portfolio → Investment`:
- `users` — RBAC `role ∈ {USER, MASTER, ADMIN}`; MASTER vê contas de outros
- `investment_accounts` — campos obrigatórios: `titular`, `cpf`, `apelido`, `institution_code/name`, `agency`, `account_number`. UNIQUE `(user_id, cpf) WHERE cpf NOT NULL`
- `portfolios` — FK `user_id` + `investment_account_id`; `is_default` flag; **cardinalidade 1:1 com conta** (refactor 25/abr)
- `trades` / `positions` / `crypto_holdings` / `rf_holdings` / `other_assets` — `portfolio_id NOT NULL`, `ON DELETE RESTRICT`
- `trade_journal` — Diário de Trade. Inclui `trade_objective` ∈ {daytrade,swing,buy_hold} (Alembic 0019), `is_complete` BOOL + `external_order_id` UNIQUE (Alembic 0020). Hook `_maybe_dispatch_diary` no profit_agent cria entry pré-preenchida em FILLED.
- `backtest_results` — histórico de runs grid_search/walk_forward (Alembic 0021). UNIQUE `config_hash` (SHA256) para UPSERT idempotente.
- `email_research` — research bulletins parseados pelo classifier E1.1 (Alembic 0022). `(msg_id UNIQUE, ticker, sentiment, target_price, source, received_at, raw_excerpt)`. Anthropic SDK + Haiku 4.5 com prompt caching.
- `cointegrated_pairs` — pairs Engle-Granger screening (Alembic 0023). `(ticker_a, ticker_b, beta, rho, p_value_adf, half_life, lookback_days, last_test_date, cointegrated)`. Job `cointegration_screen_job` 06:30 BRT diário popula.
- `robot_pair_positions` — posições abertas do dispatcher de pairs (Alembic 0024). Liga `(pair_key, leg_a_local_id, leg_b_local_id, entry_zscore, target_zscore, status)` p/ rastrear naked-leg recovery.
- `b3_delisted_tickers` — survivorship bias step 0 (Alembic 0025). `(ticker, cnpj, razao_social, delisting_date, delisting_reason, last_known_price, last_known_date, source, notes)`.

## Candle fallback chain (`candle_repository.py`)

1. `profit_daily_bars` — pré-agregado, 8 tickers DLL + 39 FIIs/ETFs Yahoo
2. `ohlc_1m` — bars 1m, agrega on-the-fly p/ daily
3. `market_history_trades` — agrega ticks on-the-fly
4. `profit_ticks` — ticks real-time
5. `fintz_cotacoes_ts` — stocks only (exclui futuros)

Ver `decisoes_arquiteturais.md` Decisão 24 para UNION cross-source obrigatório em pipelines com lookback recente.
