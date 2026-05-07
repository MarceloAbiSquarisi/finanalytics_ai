# Runbook — profit_agent (Windows host :8002)

> Troubleshooting dos bugs P1-P7 + O1 catalogados em `Melhorias.md`.
> Última revisão: 07/mai/2026 (P0+P1+P2 boot resilience — commit `d35cb96` + healthcheck script).

## Boot resilience (sessão 07/mai)

Se agent fica `Running` no NSSM mas `:8002` não responde, ou `/status` retorna `boot_phase != ready` por mais de 10min, ou DLL trava mid-runtime:

### Diagnóstico rápido
```powershell
# Phase atual + history
Invoke-WebRequest http://localhost:8002/status -UseBasicParsing | Select -Expand Content | ConvertFrom-Json | Format-List boot_phase, boot_elapsed_s, boot_phase_history, subscribe_progress

# Se a chamada timeout, agent está stuck antes do HTTP server (improvável agora — HTTP sobe na fase 0)
```

Fases esperadas (em ordem): `init → starting → loading_dll → dll_initialize_login → wait_market_connected → db_connect → db_setup → subscribe_tickers → ready`. Onde travou identifica a causa raiz.

### Mitigações automáticas
- **Boot watchdog interno** (P0): mata processo se não chegar em `ready` em 300s (env `PROFIT_BOOT_TIMEOUT_S`). NSSM restarta. Logs: `boot.watchdog.timeout`.
- **DB statement_timeout** (P1): 10s default. Lock contention vira erro rápido. Override via `PROFIT_DB_STATEMENT_TIMEOUT_MS`.
- **Subscribe parcial > boot stuck** (P2): subscribe loop em thread daemon; main parte pra `ready` em 120s mesmo se travado (env `PROFIT_SUBSCRIBE_TIMEOUT_S`). Logs: `subscribe.timeout_partial`.
- **Healthcheck externo** (P2): `scripts/healthcheck_profit_agent.ps1` deve rodar via Task Scheduler 1×/min. Cobre o caso pos-ready (agent vivo mas DLL stuck mid-runtime).

### Setup do healthcheck externo (1×, auto-eleva via UAC)
```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\setup_healthcheck_task.ps1
```

O script `setup_healthcheck_task.ps1` faz auto-elevação UAC (caso não esteja em PowerShell Admin), remove task antiga se houver, registra nova como `SYSTEM` com RunLevel Highest e RepetitionInterval 1min. Idempotente. Para remover: `... -File scripts\setup_healthcheck_task.ps1 -Remove`. Para usar usuário interativo em vez de SYSTEM: `... -User`.

Logs do healthcheck: `D:\Projetos\finanalytics_ai_fresh\logs\agent_healthcheck.log`. Cooldown de 180s entre restarts evita loop.



## Restart do agent

**Caminho preferido — via API com sudo** (não exige admin Windows):

```bash
# 1. Obter sudo_token (TTL 5min)
SUDO=$(curl -s -X POST http://localhost:8000/api/v1/auth/sudo \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"password":"admin123","ttl_minutes":5}' | jq -r .sudo_token)

# 2. Restart
curl -X POST http://localhost:8000/api/v1/agent/restart \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Sudo-Token: $SUDO"
# → {"ok":true,"message":"restarting"}
```

Boot leva ~10-15s (DLL initialization domina).

> **Senha sudo**: `admin123` (definida em `scripts/reset_password.py`). Mesma do login. Não rotacione sem atualizar o script.

**Caminho manual (admin requerido)**:

```powershell
Restart-Service FinAnalyticsAgent -Force
```

Roda como `LocalSystem` via NSSM. Stop-Service em terminal user-level falha com permission denied.

## Sintomas → diagnóstico → fix

### P5 — Tick callback congelado após restart

**Sintoma**: `total_ticks` no `/status` não sobe (mesmo com market_connected=true e ticks chegando ao DB via outros caminhos).

```bash
T1=$(curl -s http://localhost:8002/status | jq .total_ticks); sleep 5
T2=$(curl -s http://localhost:8002/status | jq .total_ticks)
echo "delta=$((T2-T1))"  # esperado > 0 em pregão
```

**Diagnóstico**: tick callback DLL morto (NSSM mata processo abruptamente, ConnectorThread fica zombie).

**Fix**: Restart via API (não NSSM). `_hard_exit()` via TerminateProcess garante shutdown limpo.

### P11 — Restart loop com session Nelogica server-side stuck (smoke 06/mai)

**Sintoma**: Após `Restart-Service FinAnalyticsAgent` (ou `/agent/restart`), agent boot OK (login_ok=True, market_connected=True, 369 subscribed), MAS `total_ticks=0` por 5+ min em mercado aberto. Watchdog dispara `heal_triggered reason=no_ticks_market_open` a cada 5min, NSSM relança, novo PID continua sem ticks. Loop infinito.

```bash
# Confirmar: heal_triggered repetido a cada 5min
grep "heal_triggered" logs/profit_agent.log | tail -10
# total_ticks_queued não cresce
curl -s http://localhost:8002/status | jq .total_ticks_queued
```

**Diagnóstico**: TerminateProcess (sem DLL.DLLFinalize antes) deixa subscription session viva no servidor Nelogica. Novo PID consegue login mas server acha que ainda está pusheando dados pro PID anterior, então não envia ticks pro novo.

**Fix automático** (commit `<TBD>` 06/mai): `_self_heal_restart` e handler `/restart` agora chamam `DLLFinalize()` com timeout 2s ANTES do TerminateProcess. Happy path: server invalida session limpa. Bad path (Finalize trava): cai pro hard_exit como antes.

**Fallback manual** (se fix automático não resolver — ex. session presa há horas):

1. **Login no Profit Pro UI Windows** com a mesma credencial PROFIT_USERNAME do `.env`
2. Esperar conexão estabelecer (icone verde Nelogica)
3. **Logout via UI** (Menu → Sair)
4. Aguardar 30s
5. `Restart-Service FinAnalyticsAgent` no PowerShell
6. Validar: `curl http://localhost:8002/status | jq .total_ticks_queued` deve crescer em 30s

Isso força o servidor Nelogica a invalidar todas as sessions ativas dessa credencial. Próximo login da DLL via NSSM começa fresh.

**Último recurso** (sessions presas em múltiplas instâncias): contatar suporte Nelogica pra reset server-side da conta.

### P6/O1 — Zombie pair (2 listeners em :8002)

**Sintoma**: `/oco/groups` retorna `count: 0` mas log diz `oco.state_loaded n=2`. State inconsistente entre requests.

```bash
netstat -ano | findstr ":8002" | findstr LISTENING
# Esperado: 1 PID. Se 2+ → zombie pair.
```

**Diagnóstico**: NSSM relançou processo enquanto velho ainda LISTENING. Cada request vai pra um random.

**Fix**: já automático. `_kill_zombie_agents()` roda no boot do `_start_http`, scanea netstat e taskkill /F outros PIDs.

Manual (se precisar): `Stop-Process -Id <pid> -Force` (admin).

**Workaround se ainda houver state vazio pós-restart**:

```bash
curl http://localhost:8002/oco/state/reload
# → {"ok":true,"groups_loaded":N}
```

### P4 — Order callback recebia struct corrupted

**Sintoma histórico** (corrigido em `27e04d3`): logs com ticker `䱐Ǆ` (Chinese garbage) + UnicodeEncodeError em stderr.

```bash
ls -la .profit_agent.err.log
# Esperado: 0 bytes. Se >0 → callback voltou a ler errado.
```

**Diagnóstico**: callback declarado `WINFUNCTYPE(None, POINTER(TConnectorOrder))` (152 bytes) mas Delphi passa `TConnectorOrderIdentifier` (24 bytes).

**Fix**: já no código. Callback agora recebe `POINTER(TConnectorOrderIdentifier)` (24 bytes match).

### P2 — DB stale com `cl_ord_id=NULL`

**Sintoma**: dashboard mostra ordem PendingNew mas DLL diz CANCELED ou FILLED.

```sql
SELECT local_order_id, cl_ord_id, order_status FROM profit_orders
 WHERE cl_ord_id IS NULL AND order_status IN (0,10) AND created_at < NOW()-INTERVAL '5 min'
 LIMIT 10;
```

**Diagnóstico**: envio inicial grava NULL (DLL preenche cl_ord_id depois via callback). Reconcile filtrava `WHERE cl_ord_id=%s` → 0 rows.

**Fix**: já no código. `WHERE local_order_id=%s OR cl_ord_id=%s` em `get_positions_dll`.

### P7 — Trailing sem ratchet

**Sintoma**: log mostra `oco_group.attached` + `oco_group.dispatched` mas nunca `trailing.adjusted`. `trail_high_water` no DB permanece NULL.

**Diagnóstico**: broker simulator rejeita `SendChangeOrderV2` em ordens stop-limit (`ret=-2147483645` = `0x80000003`).

**Fix**: já no código. Trail_monitor tenta change primeiro; se falha, faz cancel+create automaticamente. Log esperado: `trailing.cancel_create group=... old_sl=X new_sl_id=Y new_sl=Z.ZZ`.

Métrica: `profit_agent_oco_trail_fallbacks_total` incrementa cada vez que cancel+create é acionado.

### P1 — "Cliente não está logado" auth blip

**Sintoma**: `trading_msg code=3 status=8 msg=Cliente não está logado` no log + ordem rejeitada com status=204.

**Diagnóstico**: micro-disconnect na subconnection broker ↔ HadesProxy. Não dispara `crDisconnected` (routing_connected stays true).

**Fix**: já automático. `trading_msg_cb` detecta + `_retry_rejected_order` agenda re-send em 5s. Max 3 tentativas.

Sequence esperada no log:
```
retry_scheduled local_id=X attempts=1
retry_attempt local_id=X (waiting routing_ok)
retry_dispatched local_id=X
```

Se vir `retry_aborted (max_attempts=3)`: broker está com degradação além do que retry resolve. Aguardar normalização ou trocar de conta.

### P3 — DI1 worker silencioso

**Sintoma**: `di1_worker_ticks_total` em /metrics:9101 não sobe; Kafka topic `market.rates.di1` zerado durante pregão.

```bash
curl -s http://localhost:9101/metrics | grep di1_worker_ticks_total
```

**Diagnóstico**: cursor antigo usava `MAX(trade_number)` da sessão anterior. B3 reseta tn por sessão → query nunca encontra novos.

**Fix**: já no código. Cursor por timestamp (`time > worker_start`). Restart do worker resolve cenários antigos:

```powershell
docker compose restart di1_realtime_worker
```

## Cleanup ordens pendentes acumuladas

Job automático às 23h BRT (`cleanup_stale_pending_orders_job`). Manual:

```bash
curl -X POST http://localhost:8001/jobs/cleanup_stale_pending  # se exposto
# OU forçar via DB:
docker exec finanalytics_timescale psql -U finanalytics -d market_data -c \
  "UPDATE profit_orders SET order_status=8, error_message='cleanup_manual' \
   WHERE order_status IN (0,10) AND created_at < NOW()-INTERVAL '24 hours';"
```

Configurável via env:
- `SCHEDULER_STALE_PENDING_HOUR` — default 23
- `PROFIT_STALE_PENDING_HOURS` — default 24

## Health check rápido

```bash
# Tudo verde (esperado em pregão):
curl -s http://localhost:8002/status | jq '
  {market_connected, routing_connected, login_ok, db_connected,
   total_ticks, total_orders, subs: (.subscribed_tickers|length)}'

# Listeners únicos:
netstat -ano | findstr ":8002" | findstr LISTENING | wc -l   # = 1

# Métricas Prometheus:
curl -s http://localhost:8002/metrics | grep -E 'order_callbacks_total|oco_groups_active|trail_'

# stderr limpo:
ls -la .profit_agent.err.log   # = 0 bytes
```

## Métricas Prometheus expostas (port 8002 /metrics)

| Métrica | Tipo | Significado |
|---------|------|-------------|
| `profit_agent_total_ticks` | counter | Ticks processados desde boot |
| `profit_agent_total_orders` | counter | Ordens enviadas |
| `profit_agent_subscribed_tickers` | gauge | Tickers em real-time |
| `profit_agent_market_connected` | gauge 0/1 | DLL conectada ao mercado |
| `profit_agent_db_connected` | gauge 0/1 | TimescaleDB alcançável |
| `profit_agent_order_callbacks_total` | counter | Callbacks recebidos (DLL viva) |
| `profit_agent_oco_groups_active` | gauge | OCO groups in-memory |
| `profit_agent_oco_trail_adjusts_total` | counter | Ratchets sucesso (change ou cancel+create) |
| `profit_agent_oco_trail_fallbacks_total` | counter | Vezes que change_order falhou e cancel+create rodou |
| `profit_agent_last_order_callback_age_seconds` | gauge | Segundos desde último callback (alerta se >120s em pregão) |
| `profit_agent_probe_duration_seconds_*` | hist | Duração `/collect_history` |

## Alert rules associados (Grafana)

- `order_callback_stale` — rate=0 em 10min durante pregão (warn) — possível P5
- `profit_agent_db_disconnect` — `db_connected=0` por 2min (critical)
- `di1_tick_age_high` — `last_tick_age > 120s` por 3min (critical)

Ver `docker/grafana/provisioning/alerting/rules.yml` para definições completas.
