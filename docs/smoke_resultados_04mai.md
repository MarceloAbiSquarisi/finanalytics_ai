# Smoke pós-pregão — Segunda 04/mai/2026

> Routine automática `trig_013JvZLcbANEuRf8rSYiFhK5` rodou 11h BRT em ambiente simulação. Este relatório consolida resultados pós-fechamento (18h BRT).
>
> **Status do preenchimento**: TEMPLATE — preencher rodando os comandos abaixo localmente.

## 1. Tail robot_signals_log (8h pregão)

```bash
docker exec finanalytics_timescale psql -U finanalytics -d market_data -c "
SELECT id, strategy_name, ticker, action, sent_to_dll, reason_skipped, computed_at
FROM robot_signals_log
WHERE computed_at >= NOW() - INTERVAL '8 hours'
ORDER BY id DESC LIMIT 100;"
```

### Resultados

| Métrica | Valor |
|---------|-------|
| Total signals computed | _______ |
| BUY count | _______ |
| SELL count | _______ |
| HOLD count | _______ |
| SKIP count | _______ |
| sent_to_dll=true | _______ |

**Breakdown por strategy_name**:

| strategy_name | total | sent | skipped |
|---|---|---|---|
| ml_signals | | | |
| tsmom_ml_overlay | | | |

**Top 5 reason_skipped** (query auxiliar):

```sql
SELECT reason_skipped, COUNT(*) AS qtd
FROM robot_signals_log
WHERE computed_at >= NOW() - INTERVAL '8 hours' AND reason_skipped IS NOT NULL
GROUP BY reason_skipped ORDER BY qtd DESC LIMIT 5;
```

| reason_skipped | count |
|---|---|
| 1. | |
| 2. | |
| 3. | |
| 4. | |
| 5. | |

## 2. Naked legs (pair trading)

```bash
docker exec finanalytics_postgres psql -U finanalytics -d finanalytics -c "
SELECT pair_key, status, leg_a_local_id, leg_b_local_id, entry_zscore, opened_at
FROM robot_pair_positions
WHERE status LIKE 'NAKED%' OR opened_at >= CURRENT_DATE
ORDER BY opened_at DESC;"
```

### Resultados

- [ ] Sem naked legs detectadas — saudável
- [ ] **🚨 ALERTA CRÍTICO**: naked_leg_* detectada (preencher pair_key + status abaixo)

| pair_key | status | leg_a_local_id | leg_b_local_id | opened_at |
|---|---|---|---|---|
| | | | | |

## 3. P&L do dia

```powershell
$login = Invoke-RestMethod -Method POST "http://localhost:8000/api/v1/auth/login" `
    -ContentType "application/json" `
    -Body '{"email":"marceloabisquarisi@gmail.com","password":"admin123"}'
$h = @{Authorization="Bearer $($login.access_token)"}
Invoke-RestMethod "http://localhost:8000/api/v1/robot/status" -Headers $h | ConvertTo-Json -Depth 4
```

### Resultados

| Métrica | Valor |
|---|---|
| pnl_today.total | _______ |
| pnl_today.realized | _______ |
| positions_count | _______ |
| paused | _______ |
| n_strategies_enabled | _______ |
| signals_24h.total | _______ |
| signals_24h.sent_to_dll | _______ |

## 4. Saúde do auto_trader container

```bash
docker ps --filter "name=auto_trader" --format "{{.Status}}"
docker logs finanalytics_auto_trader --tail 30
```

### Resultados

- [ ] Container "Up" healthy
- [ ] **ALERTA**: container em loop Restarting (CrashLoopBackOff equivalente)

Logs notáveis (últimos 30):

```
(colar aqui — apenas linhas relevantes; cortar HEARTBEATs repetidos)
```

## 5. Circuit breaker

```bash
docker exec finanalytics_timescale psql -U finanalytics -d market_data -c "
SELECT * FROM robot_risk_state WHERE date=CURRENT_DATE;"
```

### Resultados

- [ ] paused=false (sem circuit breaker disparado)
- [ ] **🚨 ALERTA**: paused=true automático
  - Reason: _______
  - paused_at: _______
  - Drawdown que disparou: _______

## 6. Conclusão

*[Preencher após análise dos itens 1-5]*

- Robô rodou conforme esperado: ☐ sim ☐ não
- Strategies que dispararam ordens reais: _______
- Strategies que skipped 100% do dia (motivo): _______
- Achados notáveis: _______
- Próximos passos: _______

### Ação pendente

- [ ] Merge deste PR após preenchimento
- [ ] Criar memória `memory/project_session_04mai_smoke.md` com aprendizados (template em README do diretório memory)
- [ ] Se houver naked_leg ou circuit breaker disparado: investigar pré-Terça pregão
