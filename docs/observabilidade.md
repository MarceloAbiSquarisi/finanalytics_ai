# Observabilidade

## Grafana :3000 (admin/admin)

Provisionado via `docker/grafana/provisioning/`:
- Datasources: Prometheus :9090
- Dashboards JSON em `docker/grafana/dashboards/` (data_quality, profit_agent_health)
- **15 alert rules** em `provisioning/alerting/rules.yml` (recarregam via `docker restart finanalytics_grafana`)

**Roteamento** (`policies.yml`): `severity=critical` → `pushover-critical` (priority=1+siren); demais → `pushover-default` (priority=0). Credenciais: 4 env vars no `.env` (`PUSHOVER_USER_KEY`, `PUSHOVER_APP_TOKEN`, `GRAFANA_PUSHOVER_USER_KEY`, `GRAFANA_PUSHOVER_APP_TOKEN`).

## Endpoints ML (`/api/v1/ml/*`)

- `/signals` — batch de 118 tickers calibrados (filtrar por `?asset_class=fii|etf`)
- `/predict_mvp/{ticker}` — single horizon (h21 default)
- `/predict_ensemble/{ticker}` — multi-horizon agregado por sharpe
- `/signal_history` + `/changes` — auditoria histórica
- `/metrics` — saúde do pipeline (drift, snapshot age, signals 24h)

## Scheduler jobs (`scheduler_worker.py`)

- 06:00 BRT — `macro_job` (SELIC, IPCA, FX, IBOV, VIX)
- 06:30 BRT — `cointegration_screen_job` (Engle-Granger 8 tickers, 504d lookback)
- 07:00 BRT — `fii_fund` (Status Invest → `fii_fundamentals`, skip weekend)
- 07:00 BRT — `ohlcv_job` + `brapi_sync_job` (delta diário, idempotente)
- 08:00 BRT — `yahoo_bars` (39 FIIs+ETFs → `profit_daily_bars`, skip weekend)
- 09:00 BRT — `crypto_signals` (snapshot → `crypto_signals_history`, sem skip)
- 09:00 BRT no dia 5 do mês — `cvm_informe` (sync `inf_diario_fi_AAAAMM.zip` da CVM)
- 21:00 BRT — `yield_curves_refresh_job` (DI1 curves)
- 23:00 BRT — `cleanup_event_records_job` + `cleanup_stale_pending_orders_job`
- A cada 5min em 10h-18h BRT — `reconcile_loop` (DLL ↔ DB)
- A cada 60s — `gtd_enforcer_loop` (cancela ordens GTD expiradas)

## Métricas Prometheus

**profit_agent (`:8002/metrics`):**
- `profit_agent_order_callbacks_total` — counter (DLL viva)
- `profit_agent_last_order_callback_age_seconds` — gauge
- `profit_agent_oco_groups_active` — gauge
- `profit_agent_oco_trail_adjusts_total` / `profit_agent_oco_trail_fallbacks_total` — counters

**Scheduler (`:9102/metrics`):**
- `scheduler_job_runs_total{job,status}` — counter por job + status
- `scheduler_reconcile_errors_total` — counter
