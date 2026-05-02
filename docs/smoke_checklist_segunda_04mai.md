# Smoke Checklist — Segunda 04/mai/2026 pré-pregão

> **Contexto**: primeiro pregão útil pós-sessão 01/mai (58 commits — robô R1.1→R3.3 completo, I1 B.2, perf signals 2.5s, drag-modify TP/SL, NSSM AppExit=Restart). Routine `trig_013JvZLcbANEuRf8rSYiFhK5` agendada 11h BRT pra rodar `auto_trader_worker` em simulação. **Este doc é playbook manual paralelo** — comandos copy-paste pra você executar caso a routine não rode ou queira validar à mão.
>
> **Janela**: 09:30-10:00 BRT (pré-pregão) + 10:00-11:30 BRT (validação live).
>
> **Pregão B3**: 10:00-18:00 BRT.

## 📋 Pré-validações já executadas (02/mai sábado)

✅ **Saúde da stack**: 12 containers up + healthy, API `/health` OK, profit_agent `:8002` + proxy OK, WSL gateway `172.17.80.1` estável.

✅ **Strategies seedadas em `robot_strategies`**:
- `ml_signals` (id=2, enabled): tickers `[PETR4, VALE3, ITUB4, BBDC4]`
- `tsmom_ml_overlay` (id=3, enabled): tickers `[PETR4, VALE3]`, momentum_lookback_days=252
- `dummy_heartbeat` (id=1, disabled)
- `n_strategies_enabled=2` confirmado via `/robot/status`

✅ **Pause/resume cycle**: PUT /pause + /resume com `X-Sudo-Token` funciona end-to-end.

✅ **Auto-trader dry-run end-to-end** (subido com `AUTO_TRADER_ENABLED=true DRY_RUN=true INTERVAL=30`):
- 2 ciclos completos sem crash
- log_signal grava em `robot_signals_log`
- DRY_RUN respeitado (PETR4 SELL bloqueado com `reason_skipped=dry_run_mode`)
- Risk Engine aplicado (`tsmom_ml_overlay` PETR4 SKIP `insufficient_bars_for_momentum (250 < 253)` — Fintz freezou 30/dez/2025; Segunda terá bars suficientes)
- Container revertido pra `ENABLED=false` ao final (estado idle pré-smoke)

⚠️ **Pendências bloqueadas por mercado fechado** (Segunda 04/mai 10h+):
- Cancel order pendente (seção 1)
- Drag-to-modify TP/SL (seção 2)
- Despacho real de ordens via robô (seção 3.4-3.5)
- R3 pair dispatch live (seção 4)

---

## 0. Saúde da stack (09:30 BRT)

```powershell
# Context Docker correto?
docker context show
# Esperado: wsl-engine

# Containers up?
docker ps --format "{{.Names}}: {{.Status}}" | Select-String -Pattern "(api|timescale|postgres|scheduler|worker|profit|kafka|grafana|auto_trader)"
# Esperado: todos "Up" (healthy onde aplicável)

# API alive? (endpoint correto é /health, NÃO /api/v1/health)
Invoke-RestMethod "http://localhost:8000/health"
# Esperado: {status: ok, env: production, version: 0.1.0}

# profit_agent alive?
Invoke-RestMethod "http://localhost:8002/health"
Invoke-RestMethod "http://localhost:8000/api/v1/agent/health"
# Ambos: {ok: true}

# WSL gateway IP estável? (se mudou, atualizar firewall + docker-compose.wsl.yml)
wsl -d Ubuntu-22.04 -- ip route show default
# Esperado: default via 172.17.80.1 (mesmo de antes)
```

**Pass**: tudo verde + WSL gateway = 172.17.80.1. ✅ Validado em 02/mai 10:00 BRT.
**Fail**: rodar `wsl --shutdown` + reiniciar containers + re-checar.

---

## 1. Cancel order pendente (validação 25/abr — pendência de ~5d)

> Fix em `dashboard.html:~3115-3140` (toast checa `r.ok`, polling 600ms/2s/5s, fallback `/positions/dll` em 10s) nunca foi validado live por bloqueio de pregão.

**Setup** (10:05 BRT, mercado aberto ~5min):
1. Abrir `/dashboard` no browser.
2. Aba **Ordem**: BUY PETR4 100 @ Limit, preço **5% abaixo do bid atual** (pra ficar pendente sem filar).
3. Confirmar ordem aparece em **Aba Ordens** com status `New` ou `PendingNew`.

**Execução**:
4. Clicar `✕` (cancel) na ordem aberta.
5. Esperado:
   - Toast: "Cancel enviado, aguardando confirmação..."
   - Em 5-10s ordem migra pra "Fechadas" com status `Canceled`
   - Se em 10s não migrou: fallback `/positions/dll` força reconcile

**Pass**: ordem fica `Canceled` em ≤10s sem precisar refresh manual.
**Fail**: toast some sem mudar status → reportar como bug residual + abrir item em Melhorias.md.

---

## 2. Drag-to-modify TP/SL (re-validar pós I1 B.2 + drag UI 30/abr)

> Drag SVG overlay validado live 30/abr (Playwright MCP) mas containers foram migrados em I1 B.2 desde então — confirmar que nada quebrou.

**Setup** (10:10 BRT):
1. `/dashboard` aba **OCO**: enviar BUY PETR4 100 @ Market com TP=`bid+2%` SL=`bid-2%`.
2. Aguardar fill (~1-2s) e OCO ativo.

**Execução**:
3. No chart, identificar handles verde (TP) e vermelho (SL) na borda direita.
4. **Drag TP**: arrastar 1% pra baixo → confirmar UPDATE em `/api/v1/agent/order/change` (network tab).
5. **Drag SL**: arrastar 1% pra cima → confirmar mesmo flow.
6. Verificar via `/api/v1/agent/oco/status/{tp_id}` que `take_profit` e `stop_loss` refletem novos valores.

**Pass**: ambos drags fazem `change_order` ao DLL e o OCO segue ativo.
**Fail**: handles não respondem → cache do SW (verificar versão em `dashboard.html` — última conhecida v101) ou regressão SVG overlay.

---

## 3. Robô R1.5+R2 dry-run end-to-end

> 38 unit tests verde mas smoke live nunca rodou. Routine `trig_013JvZLcbANEuRf8rSYiFhK5` automática 11h BRT — este é o playbook manual paralelo.

**Pré-req** (✅ DONE 02/mai pré-smoke):

Schema real: `robot_strategies (id INT autoincrement, name UNIQUE, enabled, config_json JSONB, account_id NULLABLE)`. Não tem `strategy_type` — o registry no worker (`auto_trader_worker.py:STRATEGY_REGISTRY`) usa `name` como chave (`ml_signals`, `tsmom_ml_overlay`, `dummy_heartbeat`). Como `name` é UNIQUE, só pode ter 1 instance de cada tipo.

```sql
-- Strategies seedadas (rodado 02/mai 10:14 BRT):
UPDATE robot_strategies
   SET config_json = '{"tickers":["PETR4","VALE3","ITUB4","BBDC4"],"is_daytrade":false,"kelly_fraction":0.25,"max_position_pct":0.10,"target_vol_annual":0.15,"capital_per_strategy":10000}'::jsonb
 WHERE name = 'ml_signals';

INSERT INTO robot_strategies (name, enabled, config_json, account_id, description)
VALUES (
  'tsmom_ml_overlay', TRUE,
  '{"tickers":["PETR4","VALE3"],"is_daytrade":false,"momentum_lookback_days":252,...}'::jsonb,
  NULL, 'Smoke 04/mai — TSMOM ∩ ML overlay'
)
ON CONFLICT (name) DO UPDATE SET enabled=EXCLUDED.enabled, config_json=EXCLUDED.config_json;
```

Estado pós-seed:
- 2 strategies enabled (`ml_signals` em 4 blue chips calibrados + `tsmom_ml_overlay` em PETR4/VALE3)
- account_id NULL → fallback `PROFIT_SIM_*` envvars (`trading_accounts` está vazio)
- Validado em dry-run 02/mai 10:20 BRT: 2 ciclos completos, log_signal grava OK, DRY_RUN respeitado (PETR4 SELL bloqueado em iteration 2 com reason `dry_run_mode`).

**Setup** (10:30 BRT — após mercado estabilizar):
1. Conferir kill switch off:
   ```powershell
   Invoke-RestMethod "http://localhost:8000/api/v1/robot/status"
   # paused: false, n_strategies_enabled: 2
   ```
2. Subir worker em modo **dry-run** primeiro:
   ```bash
   # Dentro do WSL Ubuntu:
   cd /mnt/d/Projetos/finanalytics_ai_fresh
   AUTO_TRADER_ENABLED=true AUTO_TRADER_DRY_RUN=true AUTO_TRADER_INTERVAL=30 \
   DATA_DIR_HOST=/mnt/e/finanalytics_data \
     docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.wsl.yml up -d --force-recreate auto_trader
   docker logs -f finanalytics_auto_trader
   ```
3. Validar — **NÃO usar docker logs apenas**: o worker chama `log_signal()` que escreve direto em `robot_signals_log` no DB. O único log via stdout no path normal é `auto_trader.starting` no boot. Conferir via API:
   ```powershell
   $login = Invoke-RestMethod -Method POST "http://localhost:8000/api/v1/auth/login" `
     -ContentType "application/json" `
     -Body '{"email":"marceloabisquarisi@gmail.com","password":"admin123"}'
   $h = @{Authorization="Bearer $($login.access_token)"}
   Invoke-RestMethod "http://localhost:8000/api/v1/robot/signals_log?limit=20" -Headers $h
   ```
   Esperado: rows com `action=BUY|SELL|HOLD|SKIP`, `reason_skipped=dry_run_mode` para BUY/SELL.

**Execução** (10:45 BRT — desligar dry-run):
4. Recriar service com `DRY_RUN=false`:
   ```bash
   AUTO_TRADER_DRY_RUN=false docker compose ... up -d --force-recreate auto_trader
   ```
5. Validar primeira ordem real:
   - `signal_log` com `sent_to_dll=true`
   - `robot_orders_intent` com `local_order_id` populado
   - `profit_orders` com `source='auto_trader'` + `cl_ord_id='robot:smoke_ml:PETR4:...'`
   - OCO atrelado se TP+SL configurados
6. Testar kill switch — **/pause e /resume requerem sudo_token** (header `X-Sudo-Token`):
   ```powershell
   # Login + sudo (uma única call, vars não persistem entre PS calls)
   $login = Invoke-RestMethod -Method POST "http://localhost:8000/api/v1/auth/login" `
     -ContentType "application/json" -Body '{"email":"marceloabisquarisi@gmail.com","password":"admin123"}'
   $h = @{Authorization="Bearer $($login.access_token)"}
   $sudo = Invoke-RestMethod -Method POST "http://localhost:8000/api/v1/auth/sudo" `
     -Headers $h -ContentType "application/json" -Body '{"password":"admin123"}'
   $hSudo = @{Authorization="Bearer $($login.access_token)"; "X-Sudo-Token"=$sudo.sudo_token}

   # PAUSE (PUT, sudo_token obrigatório)
   Invoke-RestMethod -Method PUT "http://localhost:8000/api/v1/robot/pause" `
     -Headers $hSudo -ContentType "application/json" -Body '{"reason":"smoke test"}'
   # → {paused: true, reason: smoke test, by: marceloabisquarisi@gmail.com}

   # Worker no próximo ciclo: log_signal HEARTBEAT com reason_skipped="paused: smoke test"

   # RESUME
   Invoke-RestMethod -Method PUT "http://localhost:8000/api/v1/robot/resume" -Headers $hSudo
   # → {paused: false}
   ```
   ✅ Pause/resume cycle validado em 02/mai 10:18 BRT.

**Pass**: 1+ ordem real enviada com handshake C5 correto + kill switch interrompe entradas em ≤1 ciclo.
**Fail**: signal computado mas `sent_to_dll=false` com reason desconhecido → checar `robot_signals_log.reason_skipped` + Pushover (naked_leg deve ter alertado).

---

## 4. R3 pairs trading dispatcher (R3.2.B + R3.3 UI)

> 2 pares cointegrados em 504d Engle-Granger (validado offline). Smoke é validar dispatch dual-leg + naked_leg recovery.

**Setup** (11:00 BRT):
1. `/pairs` no browser — confirmar lista mostra 2 pares ativos com z-score real-time.
2. Identificar par com `|z_score| > entry_threshold` (provavelmente nenhum no startup; pode precisar esperar).

**Execução** (oportunista — só se z-score abrir):
3. Worker dispatcher entra automaticamente quando `|z| > 2.0` (limiar default).
4. Validar:
   - Ambas as legs aparecem em `robot_pair_positions` com `status='OPENING'`
   - `leg_a_local_id` + `leg_b_local_id` populados
   - Se uma leg falhar (rejeição) → status `NAKED_LEG_*` + Pushover **critical** (siren) disparou
   - Reversão `|z| < exit_threshold` (default 0.5) fecha as duas legs

**Pass**: dispatch dual-leg simétrico OU naked_leg + Pushover OU nenhum trigger (z-scores baixos).
**Fail**: dispatch unilateral sem alerta → bug crítico, kill switch + investigar.

---

## 5. C5 handshake (se trading-engine merger Segunda)

> Passos 2-6 bloqueados pela migration do trading-engine R-06. Agente `trig_01VDzH3xriAC777KZku42SbK` p/ 21/mai. Mas se Marcelo merge à mão antes:

1. Confirmar `profit_orders.source='trading_engine'` em ordens originadas no engine externo.
2. Confirmar `_maybe_dispatch_diary` log `diary.suppressed_engine_origin` (não cria entry no diário).
3. Confirmar `cl_ord_id` ecoado na response p/ engine fechar reconcile.

**Pass**: nenhuma duplicata em `trade_journal_unified` VIEW (quando criada).

---

## Critérios de stop

Se qualquer um destes acontecer **durante o smoke**, parar e investigar:
- **Pushover critical** disparado (naked_leg, broker rejeitou >3 ordens em sequência, kill switch automático).
- `robot_risk_state.paused=true` automático (DD<-2% disparou circuit_breaker).
- API timeout >5s em `/api/v1/ml/signals` (perf regression — esperado 2.5s).
- Container `auto_trader` em loop `Restarting` (CrashLoopBackOff equivalente).

---

## Pós-smoke (12:00 BRT — após 1h de pregão)

```powershell
# Resumo
Invoke-RestMethod "http://localhost:8000/api/v1/robot/status"
# total signals 24h, sent vs skipped, P&L do dia

Invoke-RestMethod "http://localhost:8000/api/v1/robot/signals_log?limit=50"
# auditoria das últimas decisões
```

Documentar achados em `memory/project_session_04mai_smoke.md` (template):
- Que strategies dispararam? Qual taxa de skip por reason?
- Algum naked_leg? Se sim, recovery foi tempo bom?
- Drag UI ainda funciona? Cancel pendente fechou?
- DSR walk-forward calibrado nos resultados (rodar `scripts/backtest_demo_dsr.py --persist` em fim de pregão).
