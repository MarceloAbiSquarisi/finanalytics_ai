# Runbook — `profit_daily_bars` escala mista (bug N1, 27/abr/2026)

> Origem: Sprint #23 (28/abr). Decisão 21 (`populate_daily_bars` default `1m`) nasceu daqui.

## Sintomas

1. Endpoint `/api/v1/indicators/{ticker}/levels` retorna `data_quality_warning` com mensagem "Dados de baixa qualidade: X/N bars filtrados como outliers".
2. `swing` e `williams` retornam `null` no payload (apenas `classic` continua funcional).
3. Query direta no Timescale mostra escala fracionária:

```sql
SELECT ticker, MIN(close), MAX(close), AVG(close)::numeric(10,2) AS avg
FROM profit_daily_bars
WHERE ticker IN ('ABEV3','BBDC4','ITUB4','PETR4','VALE3','WEGE3')
GROUP BY ticker;
```

Resultado típico do bug:
```
PETR4 | 0.2968 | 49.55 | 1.92   ← min muito menor que esperado, avg fracionário
```

Resultado correto (pós-fix):
```
PETR4 | 14.66 | 49.61 | 38.55
```

## Causa raiz

`market_history_trades` (ticks brutos da DLL Profit) chega com **escala /100 intermitente** — alguns dias 100% buggy (todos preços `~0.47`), outros mistos (preços corretos `~48` e fracionados `~0.47` no mesmo dia). Causa exata na DLL desconhecida; possível interação entre `close` e `close_ajustado` ou `split_factor` dinâmico (nota em `features_daily_builder.py:207-213`).

**`ohlc_1m` (source `tick_agg_v1`) NÃO tem o bug** — o agregador filtra/corrige durante a ingestão. Fix do N1 explora isso.

## Diagnóstico

```bash
# 1. Confirmar que ticks brutos têm bug em algum período
docker exec finanalytics_timescale psql -U finanalytics -d market_data -c \
  "SELECT trade_date::date, MIN(price), MAX(price), COUNT(*)
   FROM market_history_trades
   WHERE ticker='PETR4' AND trade_date >= '2026-04-01'
   GROUP BY trade_date::date ORDER BY 1;"
```

Padrão buggy: dias com `MIN(price)=0.47, MAX(price)=0.49` intercalados com dias normais.

```bash
# 2. Confirmar que ohlc_1m está limpo no mesmo período
docker exec finanalytics_timescale psql -U finanalytics -d market_data -c \
  "SELECT time::date, source, MIN(close), MAX(close), COUNT(*)
   FROM ohlc_1m
   WHERE ticker='PETR4' AND time >= '2026-04-08'
   GROUP BY 1,2 ORDER BY 1;"
```

Esperado: todas as linhas com `MIN(close) ≥ 30` (PETR4) e source=`tick_agg_v1`.

## Fix

### 1. Backup (CRÍTICO antes de DELETE)

```bash
docker exec finanalytics_timescale psql -U finanalytics -d market_data -c \
  "CREATE TABLE IF NOT EXISTS profit_daily_bars_backup_$(date +%Y%m%d) AS
   SELECT * FROM profit_daily_bars;"
```

### 2. Identificar tickers afetados

```bash
docker exec finanalytics_timescale psql -U finanalytics -d market_data -c \
  "SELECT ticker, COUNT(*) FILTER (WHERE close < 5) AS rows_low,
          COUNT(*) FILTER (WHERE close >= 5) AS rows_ok
   FROM profit_daily_bars
   WHERE ticker NOT IN ('WDOFUT','WINFUT','MTRE3')
   GROUP BY ticker
   HAVING COUNT(*) FILTER (WHERE close < 5) > 0;"
```

Note: `WDOFUT`/`WINFUT` (futuros) têm escalas grandes (50-5017 / 1627-198885); `MTRE3` real-mente cota ~3-4 (não filtrar).

### 3. DELETE + repopulate via 1m

Para cada ticker afetado:

```bash
docker exec finanalytics_timescale psql -U finanalytics -d market_data -c \
  "DELETE FROM profit_daily_bars WHERE ticker IN ('ABEV3','BBDC4','ITUB4','PETR4','VALE3','WEGE3');"

# Regenerar via ohlc_1m (limpo)
for t in ABEV3 BBDC4 ITUB4 PETR4 VALE3 WEGE3; do
  .venv/Scripts/python.exe scripts/populate_daily_bars.py --source 1m --ticker $t
done
```

### 4. Validar

```bash
docker exec finanalytics_timescale psql -U finanalytics -d market_data -c \
  "SELECT ticker, COUNT(*), MIN(close)::numeric(10,2), MAX(close)::numeric(10,2)
   FROM profit_daily_bars
   WHERE ticker IN ('ABEV3','BBDC4','ITUB4','PETR4','VALE3','WEGE3')
   GROUP BY ticker;"
```

Esperado: ABEV3 13-17, BBDC4 18-22, ITUB4 39-50, PETR4 14-50, VALE3 51-90, WEGE3 45-54.

```bash
# Validação funcional do endpoint:
curl -s "http://localhost:8000/api/v1/indicators/PETR4/levels?methods=classic,swing,williams" \
  | python -c "import sys,json; d=json.load(sys.stdin); print('warning:', d.get('data_quality_warning'), 'outliers:', d.get('outliers_dropped'))"
```

Esperado: `warning: None`, `outliers: 0`.

### 5. Cleanup do backup

Após confirmar que tudo OK por alguns dias:

```bash
docker exec finanalytics_timescale psql -U finanalytics -d market_data -c \
  "DROP TABLE profit_daily_bars_backup_YYYYMMDD;"
```

## Prevenção (Decisão 21, 28/abr/2026)

`scripts/populate_daily_bars.py` default `auto` agora tenta **`ohlc_1m` primeiro**, fallback para ticks. Inversão da ordem original. Quem rodar `populate_daily_bars.py --ticker XYZ` sem flag explícita pega bars limpos automaticamente.

**Não usar `--source ticks`** em produção para tickers com `ohlc_1m` disponível. Exceção autorizada: futuros (WDOFUT/WINFUT) que só têm ticks.

## Observabilidade

Alert rule `scheduler_data_jobs_errors` (warning) dispara quando `yahoo_bars` ou `fii_fund` falham repetidamente — ajuda detectar problemas correlatos (e.g. ohlc_1m vazio para um ticker faria yahoo_bars subir como fallback).

Não há alerta direto para "escala mista detectada" — adicionar essa métrica é follow-up futuro (custo médio).
