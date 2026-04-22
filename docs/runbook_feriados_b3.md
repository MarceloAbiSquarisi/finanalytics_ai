# Runbook — Silenciar alertas pregão-only em feriados B3

> Sprint Pregão Mute (22/abr/2026). Mute automático cobre Mon-Fri 09:30-18:30 BRT
> e fins de semana inteiros, mas **não cobre feriados B3** (variam ano-a-ano).

## Quando usar

Em **dia útil de calendário (Mon-Fri)** que coincide com **feriado B3**, os alertas
com label `pregao_only=true` vão disparar — porque o sistema espera ticks no
horário comercial, mas eles não chegam (mercado fechado).

**6 alerts afetados**:
- `di1_tick_age_high` (critical)
- `profit_agent_db_disconnect` (critical)
- `di1_kafka_errors` (warning)
- `probe_duration_spike` (warning)
- `brapi_errors_high` (warning)
- `scheduler_reconcile_errors_high` (warning)

## Como silenciar (1 dia)

### Opção A — Via UI Grafana (recomendado, 30s)

1. http://localhost:3000 → login admin/admin
2. Sidebar → **Alerting** → **Silences** → **+ New silence**
3. **Matcher**: `pregao_only = true`
4. **Starts at**: hoje 00:00
5. **Ends at**: amanhã 03:00 (pega a fronteira UTC com folga)
6. **Comment**: "Feriado [nome] DD/MM"
7. **Save**

### Opção B — Via API (script)

```bash
DATE=$(date -u +%Y-%m-%d)
TOMORROW=$(date -u -d "tomorrow" +%Y-%m-%d)

curl -sk -u admin:admin -X POST \
  "http://localhost:3000/api/alertmanager/grafana/api/v2/silences" \
  -H "Content-Type: application/json" \
  -d "{
    \"matchers\": [{\"name\":\"pregao_only\",\"value\":\"true\",\"isEqual\":true,\"isRegex\":false}],
    \"startsAt\": \"${DATE}T00:00:00Z\",
    \"endsAt\":   \"${TOMORROW}T03:00:00Z\",
    \"createdBy\": \"holiday-script\",
    \"comment\":  \"Feriado B3\"
  }"
```

### Opção C — PowerShell

```powershell
$today    = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd")
$tomorrow = (Get-Date).AddDays(1).ToUniversalTime().ToString("yyyy-MM-dd")
$body = @{
  matchers  = @(@{name="pregao_only"; value="true"; isEqual=$true; isRegex=$false})
  startsAt  = "${today}T00:00:00Z"
  endsAt    = "${tomorrow}T03:00:00Z"
  createdBy = "holiday-script"
  comment   = "Feriado B3"
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri "http://localhost:3000/api/alertmanager/grafana/api/v2/silences" `
  -Method POST -Body $body -ContentType "application/json" `
  -Credential (Get-Credential -UserName admin -Message "Grafana password")
```

## Calendário B3 — feriados sem pregão

### 2026 (referência)
- 1 jan (Quarta) — Confraternização Universal
- 16-17 fev (Seg/Ter) — Carnaval (16 sem pregão; 17 sem pregão)
- 18 fev (Quarta cinzas) — pregão começa às 13h00 (parcial; alerts podem firing manhã)
- 3 abr (Sexta) — Sexta-feira Santa
- 21 abr (Terça) — Tiradentes
- 1 mai (Sexta) — Dia do Trabalho
- 4 jun (Quinta) — Corpus Christi
- 7 set (Segunda) — Independência
- 12 out (Segunda) — N. Sra. Aparecida
- 2 nov (Segunda) — Finados
- 15 nov (Domingo, sem efeito)
- 20 nov (Sexta) — Consciência Negra
- 24 dez (Quinta) — só pregão da manhã
- 25 dez (Sexta) — Natal
- 31 dez (Quinta) — só pregão da manhã

**Fonte oficial**: https://www.b3.com.br/pt_br/solucoes/plataformas/puma-trading-system/para-participantes-e-traders/calendario-de-negociacao/feriados/

## Automação futura

Para evitar silenciamento manual em cada feriado, considerar:
1. **Cron task** que chama API B3 calendar uma vez por ano e cria silences batch
2. **Custom mute timing** com lista hardcoded de datas (atualizar 1×/ano)
3. **`is_market_open` gauge** no profit_agent — alerta vira `expr AND is_market_open == 1`

Esforço: ~1h. Não prioritário enquanto silenciamento manual é raro (~10×/ano).
