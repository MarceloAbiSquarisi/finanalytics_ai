# Smoke 04/mai/2026 — Resultados

**Outcome**: smoke **bloqueado por instabilidade do broker SIM 32003** (não por bug do robô). Infra do robô validada end-to-end. 3 bugs do `profit_agent` descobertos e corrigidos durante o debug. Refactor de retry/watch loop pra próximas tentativas.

## Cronologia

| Hora BRT | Evento |
|---|---|
| 09:00 | Setup pré-flight (containers, agent, strategies) |
| 09:25 | `ohlc_ingestor` recriado (stale image sem migration `0025`) |
| 09:34 | `cointegration_screen` manual rodado — PETR3-PETR4 cointegrado p=0.0002 |
| 09:55 | Drill kill switch (pause/resume) validado |
| 10:18 | Subscribe manual de 8 tickers (bug boot race, mem `feedback_agent_subscribe_boot_race.md`) |
| 10:27 | Flip `AUTO_TRADER_DRY_RUN=false` + restart auto_trader |
| 10:28 | Primeira ordem real: PETR4 SELL 20 → **rejeitada** `status=8` |
| 10:34 | Pause emergencial após 5 ordens auto rejeitadas |
| 10:42 | Diagnóstico: `is_daytrade=false` no seed → corrigido pra `true` |
| 11:02 | Pausado aguardando decisão do operador |
| 14:25 | User confirma broker ativo via Delphi client (mostra `RejectedMercuryLegacy "Cliente não está logado"`) |
| 14:35 | Fix `trading_msg_cb` retry pattern (codes 1,3,5,7,9,24 + 6 patterns) |
| 14:42 | Fix `watch_loop` fallback retry (silent status=8) |
| 14:47 | **Bug crítico descoberto**: `/agent/restart` quebrado desde 01/mai (`NameError _hard_exit`) |
| 15:11 | `Restart-Service FinAnalyticsAgent` (admin) → loaded fixes |
| 15:12 | Validado: `fallback_retry_scheduled` + `retry_attempt 2` em ação |
| 15:30 | Refactor performance: poll 5s→1s, retry 5s→1.5s, max 3→5, routing 30s→10s |
| 15:43 | Validado: 5 retries em ~62s (era ~75-95s) |
| 15:45 | Commit `172dbdc` |
| 15:50 | BUY final manual: ainda rejeitado → smoke encerrado |

## Validações VERDES

- Stack Docker (api, auto_trader, scheduler, timescale, postgres, agent, ohlc_ingestor) ✓
- `auto_trader` ciclando 60s, 829+ ciclos ✓
- Pipeline strategies → dispatcher → proxy FastAPI → agent → DLL → broker ✓
- `profit_agent`: market_connected, login_ok, db_connected, 3.2M+ ticks subscritos ✓
- Drill kill switch (pause efetivo em ≤5 ciclos, resume retoma próximo ciclo) ✓
- `cointegration_screen`: PETR3-PETR4 p=0.00024, β=1.090, hl=5.0d ✓
- `features_daily` zero NULLs em ATR/vol_21d ✓
- WINM26 + WDOM26 ticks live durante pregão ✓
- Robot dashboard `/robot` renderiza signals/strategies/banner pause ✓

## Bloqueio único

Broker SIM `32003 / 1000498192` rejeita **TODAS** as ordens (BUY e SELL, market e limit) com `order_status=8` em ~0.1s. Diagnóstico pelo log Delphi confirmou:

```
OrderCallback: PETR4 | ... | 204 | Cliente não está logado.
TradingMessageResultCallback: 32003 | 155075 | RejectedMercuryLegacy | Cliente não está logado.
```

Mesmo padrão para Delphi client direto. Broker subconnection flapping (`crDisconnected` cycles). Account válida (login_ok=true, account callback emite owner=MARCELO ABI), mas sessão é derrubada antes do broker processar a ordem.

**12 ordens enviadas no SIM hoje** (5 auto-trader + 7 manuais incluindo BUY/SELL/limit/market): todas `status=8`.

## Bugs descobertos e corrigidos

### Bug 1 — `/agent/restart` silencioso (crítico)
- **Causa**: sessão limpeza profunda 01/mai moveu handler HTTP pra `profit_agent_http.py` mas `_hard_exit` ficou em `profit_agent.py` sem import. `NameError` em stderr; stdout reportava `restarting`; processo nunca morria.
- **Impacto**: `/api/v1/agent/restart` quebrado por 3+ dias sem detecção. PID 23316 sobreviveu 6h+ e 4 chamadas falsas-positivas. Patches do retry P1 não carregavam.
- **Fix**: import explícito do `_hard_exit` dentro do thread do handler.
- **Memória**: `feedback_agent_restart_silent_nameerror.md`

### Bug 2 — Subscribe race no boot
- **Causa**: `subscribing_from_db count=0` sem fallback pra env quando DB conecta mas vazio.
- **Impacto**: agent sem subscriptions = `subscribed_tickers: []`, sem ticks chegando.
- **Mitigação**: subscribe manual via `POST /subscribe`. Fix definitivo defer (task #6).
- **Memória**: `feedback_agent_subscribe_boot_race.md`

### Bug 3 — Retry P1 cobertura limitada
- **Causa**: `trading_msg_cb` só triggava retry em `code=3 + msg "Cliente n/logado"`. Broker emite rejeições via outros codes (RejectedMercuryLegacy etc) AND callback pode ser dropped sob flapping intenso.
- **Fix dual**:
  - Expandido pattern: codes (1,3,5,7,9,24) + 6 padrões de blip
  - Novo: fallback retry no `watch_loop` quando `status=8` detectado em < 30s e `_retry_params` não started

## Refactor performance (`broker_blip` defense)

Tunáveis via env vars:

| Param | Antes | Agora | Env var |
|---|---|---|---|
| watch poll fresh orders | 5s | **1s** | `PROFIT_WATCH_FAST_POLL_SEC` |
| watch poll baseline | 5s | 5s | `PROFIT_WATCH_SLOW_POLL_SEC` |
| Threshold "fresh" | 30s | 30s | `PROFIT_WATCH_FRESH_AGE_SEC` |
| trading_msg retry delay | 5s | **1.5s** | `PROFIT_RETRY_DELAY_SEC` |
| Fallback retry delay | 5s | **1.5s** | `PROFIT_FALLBACK_RETRY_DELAY_SEC` |
| Max attempts | 3 | **5** | `PROFIT_RETRY_MAX_ATTEMPTS` |
| Routing wait | 30s | **10s** | `PROFIT_RETRY_ROUTING_WAIT_SEC` |
| Routing poll | 1s | **0.25s** | `PROFIT_RETRY_ROUTING_POLL_SEC` |

Validado live 15:43: ordem `392124` disparou 5 retries em 62s. Quando broker estabilizar ≥2s entre tentativas, retry pega janela.

## Lições

1. **NSSM rotation por restart** — checar `Get-CimInstance Win32_Process` (CreationDate) é mais confiável que ler logs pra confirmar restart.
2. **stderr deve ser monitorado** — `NameError` ficou invisível por 3 dias porque ninguém olhava `profit_agent.stderr.log`. Adicionar alerta Grafana se stderr cresce.
3. **Senha SIM com `$` no `.env`** — compose interpreta `$utD_$` como var; truncava senha pros containers. Irrelevante em sim path mas precisa escape `$$utD_$$` para production. Defer.
4. **Reference docs ESSENCIAIS** — `D:/Projetos/references/ProfitDLL/Exemplo Python/main.py` confirma struct fields corretos. Usar antes de assumir bug interno.
5. **Two simultaneous logins** — ProfitPro client + nosso agent na mesma conta SIM podem competir. Para próximo smoke, fechar Profit Pro antes de testar OR usar conta diferente OR investigar se Nelogica permite multi-session.

## Pendências

- [ ] **Verificar com Nelogica** se SIM 32003/1000498192 está ok / ativa / sem bloqueio
- [ ] **Smoke retomável** — basta `PUT /api/v1/robot/resume` quando broker estabilizar; retry P1 + watch fallback fará up to 5 tentativas por ordem
- [ ] Task #6 (subscribe race fix definitivo no boot)
- [ ] Limpar 5+ ordens fantasma em `robot_orders_intent` (não bloqueia)
- [ ] Escapar `$$` no `.env` PROFIT_SIM_ROUTING_PASSWORD (cosmético)

## Métricas finais

- Sinais 24h `robot_signals_log`: **831 entries** (incluindo HEARTBEATs durante pause)
- Ordens enviadas DLL hoje: **12** (todas `status=8`)
- Tentativas de retry executadas: 5 attempts × 2 testes = ~10 retries
- P&L: R$ 0,00 (zero fills)
- Commits novos: `172dbdc` (4 arquivos, +252 −25 linhas)
- Tests: 4 novos em `test_profit_agent_watch.py`. 42 total passam.
