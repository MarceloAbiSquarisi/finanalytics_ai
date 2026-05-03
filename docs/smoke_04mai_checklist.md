# Smoke checklist — Segunda 04/mai/2026 11h BRT

Objetivo: ativar robô R1.5+R2+R3 em SIM e validar pipeline end-to-end durante 1h
do pregão (11h-12h BRT). Kill switch ready.

## Pré-requisitos validados em 03/mai

- [x] `robot_strategies` seed: `ml_signals` (4 tickers) + `tsmom_ml_overlay` (2 tickers), ambos `enabled=true`
- [x] `robot_risk_state` limpo: `paused=false`, `total_pnl=0`, `positions_count=0`
- [x] Health chain: API :8000, agent :8002, Postgres :5432, Timescale :5433 — todos OK
- [x] Container `auto_trader` Up + container restartando OK
- [x] Jobs noturnos rodando: `yield_curves_refresh_job` 21h, `cointegration_screen_job` 06:30, `features_daily_builder` (incremental)
- [x] Scheduler `cointegration_screen` usa lookback=504d (PETR3-PETR4 cointegrado p=0.0002, half-life=5d)
- [x] WINFUT 19→30/abr ticks: 5/5 dias completos (~23M ticks)
- [x] ohlc_1m 19→30/abr: rebuilt 373K bars (filter pregão 13-20 UTC)
- [x] features_daily: rebuilt com lookback 250d+ pra cobrir sma_200

## Bloqueadores resolvidos no smoke

- [ ] Flip `AUTO_TRADER_ENABLED=false` → `true` em `.env` ou `docker-compose.override.yml`
- [ ] Flip `AUTO_TRADER_DRY_RUN=true` → `false`
- [ ] Restart `auto_trader` container: `wsl -d Ubuntu-22.04 -- docker compose up -d auto_trader`

## Sequência operacional Segunda 04/mai

### 10:00-10:30 BRT — preparação

```powershell
# 1. Validar containers up
wsl -d Ubuntu-22.04 -- docker ps --format '{{.Names}}: {{.Status}}' | findstr auto_trader

# 2. Validar último ciclo do scheduler (jobs 06:30 BRT)
wsl -d Ubuntu-22.04 -- docker exec finanalytics_postgres psql -U finanalytics -d finanalytics `
  -c "SELECT MAX(last_test_date) FROM cointegrated_pairs;"
# Esperado: 2026-05-04

# 3. Health chain
curl http://localhost:8000/api/v1/agent/health
# Esperado: {"ok":true}
```

### 10:45 BRT — flip env vars

```powershell
# Editar docker-compose.override.yml ou .env:
#   AUTO_TRADER_ENABLED=true
#   AUTO_TRADER_DRY_RUN=false
#
# (manter PROFIT_SIM_BROKER_ID=15011 e PROFIT_SIM_ACCOUNT_ID=216541264267275)

# Restart só o auto_trader (não toca em outros)
wsl -d Ubuntu-22.04 -- bash -c "cd /mnt/d/Projetos/finanalytics_ai_fresh && docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.wsl.yml up -d auto_trader"

# Confirmar env vars no container vivo
wsl -d Ubuntu-22.04 -- docker exec finanalytics_auto_trader env | findstr AUTO_TRADER
# Esperado:
#   AUTO_TRADER_ENABLED=true
#   AUTO_TRADER_DRY_RUN=false
```

### 11:00 BRT — abertura pregão

```powershell
# 1. Watch robot_signals_log em tempo real
wsl -d Ubuntu-22.04 -- bash -c "watch -n 5 'docker exec finanalytics_timescale psql -U finanalytics -d market_data -c \"SELECT computed_at, strategy_name, ticker, action, sent_to_dll, COALESCE(reason_skipped, payload->>quantity::text) FROM robot_signals_log ORDER BY computed_at DESC LIMIT 10;\"'"

# 2. (opcional) Watch logs do dispatcher
wsl -d Ubuntu-22.04 -- docker logs -f finanalytics_auto_trader
```

Esperado nos primeiros 5min: pelo menos 1 evento por (strategy, ticker) — total ≥6 entries (4 ml_signals + 2 tsmom_ml_overlay).

### Durante pregão (11h-12h BRT)

Watch contínuo de:
- `robot_signals_log` — eventos novos
- `robot_orders_intent` — ordens enviadas (BUY/SELL com sent_to_dll=true)
- `robot_pair_positions` — posições abertas
- `robot_risk_state.paused` — kill switch (deve permanecer `false`)

## Kill switch (a qualquer hora)

```powershell
# 1. Pause via API
$login = Invoke-RestMethod -Method POST "http://localhost:8000/api/v1/auth/login" `
  -ContentType "application/json" `
  -Body '{"email":"marceloabisquarisi@gmail.com","password":"admin123"}'
$h = @{Authorization="Bearer $($login.access_token)"}
Invoke-RestMethod -Method POST "http://localhost:8000/api/v1/robot/pause" -Headers $h

# 2. (Drástico) flatten todas as posições antes de pausar
Invoke-RestMethod -Method POST "http://localhost:8002/order/cancel_all"
# Pra cada posição aberta:
Invoke-RestMethod -Method POST "http://localhost:8002/order/flatten_ticker" `
  -Body '{"ticker":"PETR4","exchange":"B"}' -ContentType "application/json"

# 3. (Nuclear) restart o agent — descarta DLL state
Invoke-RestMethod -Method POST "http://localhost:8000/api/v1/agent/restart" `
  -Body '{"sudo_token":"admin123"}' -ContentType "application/json"
```

## Pós-smoke (12h BRT)

```powershell
# 1. Pause
Invoke-RestMethod -Method POST "http://localhost:8000/api/v1/robot/pause" -Headers $h

# 2. Snapshot resultado
wsl -d Ubuntu-22.04 -- docker exec finanalytics_timescale psql -U finanalytics -d market_data -c "
SELECT
  strategy_name,
  COUNT(*) FILTER (WHERE action IN ('BUY', 'SELL')) AS trades,
  COUNT(*) FILTER (WHERE sent_to_dll) AS sent,
  COUNT(*) FILTER (WHERE reason_skipped IS NOT NULL) AS skipped,
  COUNT(DISTINCT reason_skipped) AS skip_reasons
FROM robot_signals_log
WHERE computed_at >= CURRENT_DATE
GROUP BY strategy_name;
"

# 3. P&L
$pnl = Invoke-RestMethod "http://localhost:8000/api/v1/robot/status" -Headers $h
Write-Host "Realized: $($pnl.pnl_today.realized) | Total: $($pnl.pnl_today.total)"
```

## Achados antecipados (dry-run 03/mai pós-fix)

`scripts/smoke_dryrun_strategies.py` (chama strategies sem DB):

| Strategy | Ticker | Decisão | Detalhe |
|---|---|---|---|
| ml_signals | PETR4 | SELL | qty=20, tp=45.07, sl=51.54 (RF features negativos) |
| ml_signals | VALE3 | HOLD | sinal neutro |
| ml_signals | ITUB4 | HOLD | sinal neutro |
| ml_signals | BBDC4 | HOLD | sinal neutro |
| tsmom_ml_overlay | PETR4 | SKIP | tsmom_disagree: ml=SELL mas momentum=+35.15% |
| tsmom_ml_overlay | VALE3 | HOLD | |

**Esperado Segunda 11h**: 1 SELL real em PETR4 (ml_signals), 4 HOLDs, 1 SKIP intencional (TSMOM overlay vetando ml).

Pre-fix encontrado e corrigido em 03/mai noite:
- `populate_daily_bars` agregou ticks de Sábado 02/mai por bug (ticks heartbeat fora de pregão).
- `features_daily` herdou row Saturday → JOIN com `rates_features_daily` (sem weekend) → 23 NULLs em RF features.
- Fix: `DELETE FROM profit_daily_bars/features_daily WHERE DOW IN (0,6) AND time >= 2026-04-19` (351 + 133 rows wipadas).
- Bug raiz: filter EXTRACT(hour) BETWEEN 13 AND 20 do continuous aggregate ohlc_1m_from_ticks NÃO filtra weekend, só hora. Adicionar `EXTRACT(dow) BETWEEN 1 AND 5` no rebuild script + populate_daily_bars seria mais robusto. **Defer pós-smoke**.

## Riscos identificados

1. **Smoke pode SKIP-ar se features ainda têm NULLs** — features_daily rebuilt em 03/mai-noite com lookback 250d+ deve resolver. Validar Segunda manhã.
2. **Conta SIM credencial** — fallback `PROFIT_SIM_BROKER_ID=15011` + `PROFIT_SIM_ACCOUNT_ID=216541264267275` confirmar autenticação OK no boot do agent.
3. **WSL gateway IP** — `172.17.80.1` validado em 01/mai; pode mudar após reboot Windows. Confirmar `Get-NetFirewallRule | findstr "Profit Agent"`.
4. **DLL connector blip** — auto-retry resilience patterns em place (P1, P9, P10), mas se broker ficar 30s+ off, signals vão skip por `client_not_logged`.

## Rollback

Se algo dá errado:
1. `Invoke-RestMethod -Method POST .../robot/pause` (kill switch)
2. Edit `.env`: `AUTO_TRADER_ENABLED=false`
3. `docker compose up -d auto_trader` (restart com flag desabilitada)
4. Investigar `robot_signals_log` + `auto_trader` logs

## Memo p/ pós-smoke

Pontos pra avaliar Segunda 12h:
- Quantos trades foram executados (sent_to_dll=true)
- Latência média do dispatch (computed_at → ordem fillada)
- P&L realizado vs unrealized
- Skip reasons mais frequentes (insuficiência de dado vs decisão consciente)
- DLL callbacks se mantiveram vivas (`profit_agent_last_order_callback_age_seconds` < 30s)

Reportar em `docs/smoke_resultados_04mai.md`.
