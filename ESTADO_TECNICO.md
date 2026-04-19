# FinAnalyticsAI — Estado Técnico (17/abr/2026)

> Referência técnica pura: schemas, scripts, endpoints, bugs, comandos. Sem overview executivo — ver `ESTADO_CONSOLIDADO.md` para isso.

---

## Sumário

- 1. Stack e topologia local
- 2. Modelo de dados TimescaleDB
- 3. profit_agent: API e bugs
- 4. Patch-bundle 17/abr/2026 (detalhado)
- 5. Pipeline Fintz
- 6. Watchlist canônica: DDL e critérios
- 7. Scripts de backfill: referência completa
- 8. Serviço nssm: configuração exata
- 9. Consultas SQL de referência
- 10. Calendário B3 2020–2026 (feriados codificados)
- 11. Paths, DSNs e variáveis de ambiente
- 12. Pendências técnicas (R1–R10)

---

## 1. Stack e topologia local

```
Profit.exe (GUI Nelogica, logado)
        │ (ctypes → ProfitDLL.dll)
        ▼
profit_agent (FastAPI/uvicorn @ localhost:8002)
        │ (psycopg2)
        ▼
TimescaleDB (Docker: finanalytics_timescale @ localhost:5433)
  ├── market_history_trades  (hypertable, tick-level)
  ├── ohlc_1m                (continuous aggregate)
  ├── fintz_cotacoes_ts      (hypertable, EOD)
  └── watchlist_tickers      (tabela comum, ~135 linhas)
        ▲                         ▲
        │                         │
backfill_historico_watchlist.py  fill_fintz_gap.ps1
(nssm service: FinAnalyticsBackfill)
```

Nenhum componente é cloud. Tudo roda no workstation do usuário. Profit.exe e ProfitDLL são Windows-only — o profit_agent e todos os scripts Python rodam nativamente em Windows dentro da conda env `finanalytics-ai`.

## 2. Modelo de dados TimescaleDB

### 2.1 `market_history_trades` (hypertable)

Particionada por `trade_date` (chunk ~7 dias). Uma linha por tick histórico.

```
ticker          text        NOT NULL
trade_date      timestamptz NOT NULL
trade_number    bigint      NOT NULL
price           numeric
quantity        bigint
agressor        char(1)            -- 'C'=comprador, 'V'=vendedor, 'D'=direto, ' '=indefinido
exchange        char(1)            -- 'B'=à vista B3, 'F'=futuros B3
trade_id        bigint
PRIMARY KEY (ticker, trade_date, trade_number)
```

Inserts em `profit_agent._insert_trades` usam `ON CONFLICT (ticker, trade_date, trade_number) DO NOTHING` → backfill é idempotente, restart do serviço é seguro, re-coleta de dias existentes não duplica.

**Propriedade estrutural do `trade_number`:** na B3 à vista, o stride é 10 por trade (`trade_number` vai 10, 20, 30, …). Isso permite estimar cobertura esperada: `(max(trade_number) − min(trade_number)) / 10 + 1`. **Não vale para futuros:** WINFUT tem stride ~160, WDOFUT ~70, e varia por vencimento. Qualquer auditoria ponderada que usa essa fórmula precisa filtrar `exchange = 'B'`.

### 2.2 `ohlc_1m` (continuous aggregate)

Deriva de `market_history_trades` via `CREATE MATERIALIZED VIEW … WITH (timescaledb.continuous) AS SELECT time_bucket('1 minute', trade_date) AS bucket, ticker, first(price, trade_date) AS open, max(price) AS high, min(price) AS low, last(price, trade_date) AS close, sum(quantity) AS volume, count(*) AS trade_count …`.

Refresh: `CALL refresh_continuous_aggregate('ohlc_1m', <start>, <end>)`. Script em `fase1_backfill_ohlc_1m.sql`. Policy incremental também está configurada para o fim do pregão.

Pregão B3 padrão (10:00–17:00) gera ~420 candles por ticker × dia quando há fluxo contínuo. Dias com call/auction têm comportamento diferente nos minutos de abertura/fechamento.

### 2.3 `fintz_cotacoes_ts` (hypertable)

EOD bars da Fintz. Granularidade diária. Cobertura histórica > ProfitDLL (começa antes de 2020), mas sem intraday. Usada para (i) ranking de liquidez base da Fase 0, (ii) fallback quando Profit indisponível.

Campos principais: `ticker`, `data`, `open`, `high`, `low`, `close`, `volume_negociado` (BRL), `quantidade_negociada` (shares), `trades`.

### 2.4 `watchlist_tickers`

DDL completo em `create_watchlist_tickers.sql`:

```sql
CREATE TABLE watchlist_tickers (
    ticker              text     PRIMARY KEY,
    mediana_vol_brl     numeric  NOT NULL,
    media_vol_brl       numeric,
    mediana_trades_dia  numeric,
    dias_cobertura      integer,
    ticks_2026          integer  NOT NULL DEFAULT 0,
    ultimo_tick         date,
    status              text     NOT NULL
        CHECK (status IN ('VERDE',
                          'AMARELO_parada_recente',
                          'AMARELO_coleta_fraca',
                          'VERMELHO_sem_profit')),
    atualizado_em       timestamptz NOT NULL DEFAULT now()
);
```

## 3. profit_agent: API e bugs

**Arquivo:** `D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\profit_agent.py` (FastAPI + uvicorn).

**Pré-requisito runtime:** Profit.exe aberto e logado. A DLL resolve o usuário via sessão ativa do Profit. Sem isso, `/status` retorna `market_connected: false`.

**Endpoints em uso:**

| Método | Path                | Body / Query                                                                 | Retorno                                                                                     |
|--------|---------------------|------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------|
| GET    | `/status`           | —                                                                            | `{market_connected, db_connected, total_assets}`                                            |
| POST   | `/collect_history`  | `{ticker, exchange, dt_start, dt_end, timeout}` (`dt_*` = `"dd/mm/yyyy hh:mm:ss"`) | `{ticks, inserted, v1_count, v2_count, first:{ticker,trade_date,...}, last:{...}, status}`  |
| POST   | `/subscribe`        | `{ticker, exchange}`                                                         | `{subscribed: true}`                                                                        |
| POST   | `/unsubscribe`      | `{ticker, exchange}`                                                         | `{unsubscribed: true}`                                                                      |
| GET    | `/ticks`            | `?ticker=…&limit=…`                                                          | streaming JSON                                                                              |

**Callbacks DLL:** dois callbacks coexistem. V1 (simples) é invocado pela DLL em `SetHistoryTradeCallback`. V2 (struct detalhada) em `SetHistoryTradeCallbackV2`. O agent mantém buffers separados (`v1_count`, `v2_count`) para diagnóstico. No happy path, ambos convergem nos mesmos trade_numbers.

## 4. Patch-bundle 17/abr/2026 (detalhado)

Contexto: auditoria em 17/abr revelou cobertura de 0,04% / 1,84% / 14,16% / 9,84% nos dias 13 a 16/abr. Causa raiz: cinco bugs no `/collect_history` que só se manifestavam em probes consecutivas rápidas (exatamente o padrão de backfill). Patch-bundle de sete alterações:

**Bug 1 — filtro de ticker ausente no callback histórico.**
O callback registrado em `SetHistoryTradeCallback` recebe ticks de qualquer assinatura ativa no Profit, não apenas do ticker alvo do probe. Se outro ticker estivesse subscribed em paralelo, ticks dele vazavam para o buffer.

Patch:
```python
def _on_history_trade_v1(asset, trade):
    if asset.ticker != self._current_probe_ticker:
        return  # ignora ticks de outras assinaturas
    self._v1_buffer.append((asset.ticker, trade.trade_date, ...))
```

**Bug 2 — contaminação entre probes.**
Buffers `_v1_buffer` e `_v2_buffer` não eram limpos no início do probe seguinte. Segunda chamada a `/collect_history` incluía ticks residuais do primeiro.

Patch:
```python
async def collect_history(req):
    self._v1_buffer.clear()
    self._v2_buffer.clear()
    self._current_probe_ticker = req.ticker
    ...
```

**Bug 3 — race em `done.set()`.**
O callback de término (`SetHistoryTradeStatusCallback`) sinalizava `done` assim que a DLL indicava “fim”, mas a DLL despacha os últimos batches em thread separada. Resultado: `done.wait()` retornava antes dos últimos N ticks serem drenados.

Patch:
```python
def _on_history_status(status, ...):
    if status == HISTORY_FINISHED:
        # flush pendente antes de sinalizar
        time.sleep(0.5)
        while self._pending_batches > 0 and time.time() - t0 < 5:
            time.sleep(0.1)
        self._history_done.set()
```

**Bug 4 — janela temporal sem validação.**
`dt_start` e `dt_end` invertidos ou inválidos eram aceitos; o probe rodava até timeout sem dados.

Patch:
```python
if dt_start_parsed >= dt_end_parsed:
    raise HTTPException(400, "dt_start must be < dt_end")
```

**Bug 5 — buffer V2 struct compartilhada.**
Callback V2 reusava uma instância de `TCallbackHistoryTradeV2` entre callbacks. Se a append no Python fosse lenta (GC, lock), a próxima invocação sobrescrevia a struct antes do deepcopy.

Patch:
```python
def _on_history_trade_v2(asset_ptr, trade_ptr):
    # deepcopy imediato antes que a DLL reaproveite o ponteiro
    trade = copy.deepcopy(trade_ptr.contents)
    asset = copy.deepcopy(asset_ptr.contents)
    if asset.ticker != self._current_probe_ticker:
        return
    self._v2_buffer.append(...)
```

**Alterações adicionais (6 e 7):** logging explícito de `(v1_count, v2_count)` no retorno, e guardas defensivas no `/subscribe` para não interferir com probe em andamento (mutex `_probe_in_progress`).

**Validação em escala:** `backfill_recentes_4dias.py` em 17/abr → 540 probes (135 tickers × 4 dias), 5,3M ticks, 0 contaminações. Log em `backfill_4dias_log.txt`.

## 5. Pipeline Fintz

Cliente Fintz em Python via HTTP (key em `.env` como `FINTZ_API_KEY`). Sink: `fintz_cotacoes_ts`. Rate limit diário — por isso operado manualmente hoje.

**Scripts ativos:**

| Script                                    | Propósito                                                          |
|-------------------------------------------|--------------------------------------------------------------------|
| `fill_fintz_gap.ps1`                      | Preenche gaps pontuais em datas específicas                        |
| `diag_fintz_atrasados_readonly.ps1`       | Lista tickers com `max(data) < hoje − 7`                           |
| `diag_fintz_gap_readonly.ps1`             | Mapeamento ticker × janela de gap                                  |
| `diag_fintz_script_validacao.ps1`         | Valida consistência Fintz vs ProfitDLL (EOD vs agregado de ticks)  |

**Inconsistência conhecida:** MBRF3 tem cobertura Fintz curta (~30 dias pós-fusão) mas cobertura Profit completa desde a fusão. Tratada via inclusão manual na watchlist (tiering VERDE apesar do `dias_cobertura < 150`).

## 6. Watchlist canônica: DDL e critérios

**Critério de entrada** (Fase 0, executado 17/abr):

```sql
SELECT ticker
  FROM fintz_cotacoes_ts
 WHERE data BETWEEN '2024-11-01' AND '2025-11-30'
 GROUP BY ticker
HAVING percentile_cont(0.5) WITHIN GROUP (ORDER BY volume_negociado) > 500000
   AND count(*) >= 150
 ORDER BY percentile_cont(0.5) WITHIN GROUP (ORDER BY volume_negociado) DESC;
```

**Exclusões explícitas:**
- `IBOV` — é índice, não tem ticks individuais em `market_history_trades`.
- `BRFS3`, `MRFG3` — fundiram em `MBRF3` em mar/2026; manter os códigos antigos geraria dados órfãos.
- `NTCO3` — delisted.

**Inclusão manual:** `MBRF3` (cobertura Fintz curta mas Profit completa).

**Tiers (regras de classificação):**

| Status                      | Regra                                                                   | Ação                                     |
|-----------------------------|-------------------------------------------------------------------------|------------------------------------------|
| `VERDE`                     | `ultimo_tick >= hoje − 3` AND `ticks_2026 > 10k`                        | backfill rotineiro                       |
| `AMARELO_parada_recente`    | `ultimo_tick` caiu, sem explicação óbvia                                | investigar (R4)                          |
| `AMARELO_coleta_fraca`      | Cobertura presente mas `trades/dia << esperado`                         | monitorar, possível liquidez baixa real  |
| `VERMELHO_sem_profit`       | Presente em Fintz, ausente em `market_history_trades`                   | fora do plano Profit atual               |

Refresh dos flags: manual via consultas nos diagnósticos.

## 7. Scripts de backfill: referência completa

Todos em `D:\Projetos\finanalytics_ai_fresh\scripts\`.

### 7.1 `backfill_historico_watchlist.py` (principal, 480 linhas)

**Universo:** watchlist VERDE+AMARELO (~135 stocks, exchange='B') + futuros default `[WINFUT, WDOFUT]` (exchange='F'). Carregado dinamicamente do DB ordenado por `mediana_vol_brl DESC` — os mais líquidos são processados primeiro.

**Timeouts:** `TIMEOUT_STOCK_S=300`, `TIMEOUT_FUT_S=2400`.

**Retry:** `RETRY_MAX=3`, backoff `RETRY_DELAY_BASE=10` → 10s, 20s, 30s. Apenas em `URLError`/`TimeoutError`/`ConnectionError`; exceções não-transitórias falham rápido.

**Graceful shutdown:** handlers para SIGINT/SIGTERM/SIGBREAK (Windows). Primeiro sinal marca `_shutdown=True`; o loop interrompe entre tickers (ou entre dias de um mesmo ticker). Segundo sinal força saída via `sys.exit(1)`.

**Idempotência:** `get_collected_dates(ticker, start, end)` consulta `SELECT DISTINCT trade_date::date FROM market_history_trades WHERE ticker=%s AND trade_date::date BETWEEN %s AND %s` e remove esses dias do plano antes de iniciar. Combinado com `ON CONFLICT DO NOTHING` no insert, o script pode ser interrompido e reiniciado a qualquer momento sem perda nem duplicação.

**Validação anti-contaminação:** cada resposta de `/collect_history` devolve `first.ticker` e `last.ticker`. O script flagga `CONT_ticker` se algum dos dois diverge do `ticker` pedido. Com o patch-bundle, esperado = 0.

**CLI:**

```
python backfill_historico_watchlist.py
  [--start YYYY-MM-DD]         (default 2020-01-02)
  [--end YYYY-MM-DD]           (default hoje)
  [--delay SEGUNDOS]           (default 2)
  [--from-ticker TICKER]       retoma a partir deste ticker
  [--only "T1,T2,..."]         ignora watchlist, usa lista CLI
  [--futures "F1,F2,..."]      (default "WINFUT,WDOFUT")
  [--no-futures]
  [--dry-run]                  lista plano sem chamadas
```

### 7.2 `backfill_recentes_4dias.py`

Re-coleta dias 2026-04-13 a 04-16 na watchlist VERDE+AMARELO. Script scaffold para futuras re-coletas pontuais. Validação embutida e resumo final com contador de contaminações.

### 7.3 `backfill_2025_top50.py`

Cenário B original (top 50 por liquidez 2025, ranking fixo no script). Dados já no banco — não precisa re-rodar. Deixado como referência histórica.

### 7.4 `backfill_history.py` (legado)

Primeira versão do backfill genérico. Mantido por compatibilidade.

### 7.5 `backfill_resume.py`

Reexecução de probes específicos a partir de CSV `(ticker, data)`. Usa quando `backfill_historico_watchlist.py` acumula muitos erros e quer-se retry seletivo.

## 8. Serviço nssm: configuração exata

**Wrapper PowerShell:** `D:\Investimentos\FinAnalytics_AI\Melhorias\setup_backfill_service.ps1`.

**Parâmetros fixados no `install`:**

```
ServiceName         FinAnalyticsBackfill
DisplayName         FinAnalytics - Backfill Historico (ProfitDLL)
Application         <python.exe da env finanalytics-ai>
Arguments           D:\Projetos\finanalytics_ai_fresh\scripts\backfill_historico_watchlist.py
AppDirectory        D:\Projetos\finanalytics_ai_fresh
AppStdout           D:\Investimentos\FinAnalytics_AI\Melhorias\logs\backfill_historico_stdout.log
AppStderr           D:\Investimentos\FinAnalytics_AI\Melhorias\logs\backfill_historico_stderr.log
AppRotateFiles      1
AppRotateOnline     1
AppRotateBytes      104857600           (100 MB)
AppTimestampLog     1
AppExit Default     Restart             (auto-restart em crash)
AppRestartDelay     30000               (30s antes do respawn)
AppThrottle         10000               (mínimo 10s entre tentativas = anti-fork-bomb)
AppStopMethodConsole 120000             (graceful: envia Ctrl+Break, espera 120s)
AppStopMethodSkip   0                   (tenta todos os métodos de stop em sequência)
Start               SERVICE_DEMAND_START (não sobe no boot; exige Profit.exe logado primeiro)
```

**Requisito de instalação:** PowerShell rodando em sessão com conda env `finanalytics-ai` ativa + privilégios de Administrador. O path do `python.exe` da env é fixado no serviço no momento do `install` e persiste mesmo quando a env não está ativa no futuro.

**Ações disponíveis:** `install`, `start`, `stop`, `restart`, `status`, `tail`, `uninstall`.

**Stop gracioso:** `nssm stop` envia Ctrl+Break (SIGBREAK no Windows) → handler Python marca `_shutdown=True` → script termina o ticker corrente → sai. Espera até 120s. Se exceder, nssm mata o processo; ON CONFLICT garante que não há corrupção.

## 9. Consultas SQL de referência

### 9.1 Cobertura ponderada por dia (stocks only)

```sql
WITH por_ticker AS (
    SELECT ticker,
           trade_date::date AS dia,
           count(*) AS trades,
           CASE WHEN max(trade_number) - min(trade_number) > 0
                THEN (max(trade_number) - min(trade_number)) / 10 + 1
                ELSE 1
           END AS esperado
      FROM market_history_trades
     WHERE trade_date::date IN ('2026-04-13','2026-04-14','2026-04-15','2026-04-16')
       AND exchange = 'B'
     GROUP BY ticker, trade_date::date
)
SELECT dia,
       count(*) AS tickers,
       sum(trades) AS trades_total,
       sum(esperado) AS esperado_total,
       round(avg(100.0 * trades / NULLIF(esperado, 0)), 2)    AS pct_cob_media,
       round(100.0 * sum(trades) / NULLIF(sum(esperado), 0), 2) AS pct_cob_pond
  FROM por_ticker
 GROUP BY dia
 ORDER BY dia;
```

### 9.2 Último tick por ticker (detecta coleta parada)

```sql
SELECT ticker,
       max(trade_date) AS ultimo_tick,
       now() - max(trade_date) AS atraso
  FROM market_history_trades
 GROUP BY ticker
 ORDER BY ultimo_tick ASC
 LIMIT 30;
```

### 9.3 Candidatos a AMARELO_parada_recente

```sql
SELECT w.ticker, w.status, w.ultimo_tick, w.ticks_2026,
       (SELECT max(trade_date) FROM market_history_trades m WHERE m.ticker = w.ticker) AS ultimo_real
  FROM watchlist_tickers w
 WHERE w.status = 'AMARELO_parada_recente'
 ORDER BY w.mediana_vol_brl DESC;
```

### 9.4 Ranking de liquidez (base da watchlist)

```sql
SELECT ticker,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY volume_negociado) AS mediana_vol_brl,
       count(*) AS dias
  FROM fintz_cotacoes_ts
 WHERE data BETWEEN '2024-11-01' AND '2025-11-30'
 GROUP BY ticker
HAVING count(*) >= 150
   AND percentile_cont(0.5) WITHIN GROUP (ORDER BY volume_negociado) > 500000
 ORDER BY mediana_vol_brl DESC;
```

### 9.5 Detectar contaminação residual (pós-patch)

Se o patch está ativo, `first.ticker == last.ticker == requested`. A query abaixo detecta qualquer linha onde o ticker em `market_history_trades` não bate com o contexto do probe — proxy: buscar tickers que aparecem em dias que não fazem sentido (ex.: tickers que sabemos que só têm fluxo em horários restritos).

```sql
SELECT ticker, trade_date::date AS dia, count(*) trades,
       min(trade_date) primeiro, max(trade_date) ultimo
  FROM market_history_trades
 WHERE trade_date::date = '2026-04-17'
   AND (extract(hour from trade_date) < 10 OR extract(hour from trade_date) > 18)
 GROUP BY ticker, trade_date::date
 ORDER BY trades DESC;
```

### 9.6 Executar no container

```powershell
docker exec -i finanalytics_timescale psql -U finanalytics -d market_data -c "<SQL>"
```

ou com arquivo:

```powershell
Get-Content D:\Investimentos\FinAnalytics_AI\Melhorias\audit.sql |
  docker exec -i finanalytics_timescale psql -U finanalytics -d market_data
```

## 10. Calendário B3 2020–2026 (feriados codificados)

Lista em `backfill_historico_watchlist.py`, função `is_trading_day`. Um dia é pregão se `weekday() < 5 AND date not in HOLIDAYS_BR`.

```
2020: 1/1, 24/2, 25/2, 10/4, 21/4, 1/5, 11/6, 9/7, 7/9, 12/10, 2/11, 15/11, 24/12, 25/12, 31/12
2021: 1/1, 25/1, 15/2, 16/2, 2/4, 21/4, 1/5, 3/6, 7/9, 12/10, 2/11, 15/11, 24/12, 25/12, 31/12
2022: 1/1, 25/1, 28/2, 1/3, 15/4, 21/4, 1/5, 16/6, 7/9, 12/10, 2/11, 15/11, 25/12
2023: 1/1, 20/2, 21/2, 7/4, 21/4, 1/5, 8/6, 7/9, 12/10, 2/11, 15/11, 25/12
2024: 1/1, 25/1, 12/2, 13/2, 29/3, 21/4, 1/5, 30/5, 7/9, 12/10, 2/11, 15/11, 20/11, 24/12, 25/12, 31/12
2025: 1/1, 3/3, 4/3, 5/3, 18/4, 21/4, 1/5, 19/6, 7/9, 12/10, 2/11, 15/11, 20/11, 24/12, 25/12, 31/12
2026: 1/1, 16/2, 17/2, 18/2, 3/4, 21/4, 1/5, 4/6, 7/9, 12/10, 2/11, 15/11, 20/11, 25/12
```

## 11. Paths, DSNs e variáveis de ambiente

**Repositório de código:** `D:\Projetos\finanalytics_ai_fresh\`
- `src\finanalytics_ai\profit_agent.py` — FastAPI wrapper
- `src\finanalytics_ai\` — pacote Python (ingestão, DB, modelos)
- `scripts\` — scripts de backfill e diagnóstico
- `.env` — credenciais (PROFIT_TIMESCALE_DSN, FINTZ_API_KEY, …)

**Workbench de operação:** `D:\Investimentos\FinAnalytics_AI\Melhorias\`
- `*.ps1` — scripts operacionais (diagnósticos, fill_fintz, setup_backfill)
- `*.sql` — SQLs de auditoria, agregação, criação de tabelas
- `*.md` — briefings, planos, documentos de estado
- `logs\` — logs do serviço nssm (stdout + stderr rotacionados)
- `Obsidian\` — notas relacionadas

**DSN TimescaleDB:**
```
postgresql://finanalytics:timescale_secret@localhost:5433/market_data
```
Env var: `PROFIT_TIMESCALE_DSN`.

**profit_agent URL:** `http://localhost:8002`

**Container Docker:** `finanalytics_timescale` (imagem baseada em timescale/timescaledb-ha:pg16).

**Conda env:** `finanalytics-ai`. Python 3.11+. Pacotes core: `fastapi`, `uvicorn`, `psycopg2-binary`, `pydantic`, `httpx`.

## 12. Pendências técnicas (R1–R10)

| ID  | Descrição                                                   | Status       | Bloqueia  | Bloqueado por |
|-----|-------------------------------------------------------------|--------------|-----------|---------------|
| R1  | Instalar nssm + `setup_backfill_service.ps1 install/start`  | próximo      | R2, R10   | —             |
| R2  | Re-aggregate `ohlc_1m` para 2026-04-13..04-16               | pendente     | R9        | R1 (parcial)  |
| R3  | Auditoria ponderada adaptada a futuros (stride variável)    | pendente     | R7        | —             |
| R4  | Investigar BBDC3, BPAN4, GUAR3 (AMARELO_parada_recente)     | pronto p/ ir | —         | —             |
| R5  | Assinar Profit para ~80 VERMELHO_sem_profit                 | decisão      | —         | —             |
| R6  | P23 Fintz refinado (Fase 2)                                 | pendente     | R9        | —             |
| R7  | View `gap_map_1m` consolidada (Fase 3)                      | em andamento | R9        | R3            |
| R8  | Decisão hospedagem / dual-GPU                               | em análise   | R10       | —             |
| R9  | Dashboards de qualidade de dados                            | pendente     | —         | R7, R2        |
| R10 | Sistema de modelos e backtests                              | pendente     | —         | R1            |

**Tasks do sistema relacionadas (IDs internos):** #10 (Fase 3 gap map), #11 (Fase 4 backfill), #6 (arquitetura 5y+), #9 (Fase 2 P23 Fintz).

---

## Mudanças pendentes no código (out-of-scope desta sessão)

- **`profit_agent.py`:** adicionar métrica Prometheus (`/metrics`) para expor latência por probe, count de contaminações, e tamanho de buffer — hoje só há `print`.
- **`backfill_historico_watchlist.py`:** adicionar flag `--exchange` global (hoje 'B' é hardcoded para stocks no loop `universe.append((t, "B", ...))`). Caso apareçam ativos em outra exchange B3 que não 'B' (ex: opções 'O'), o código precisa ajuste.
- **`fase1_backfill_ohlc_1m.sql`:** parametrizar range de datas em vez de hardcode para viabilizar re-run incremental por range.

---

## Changelog

- **17/abr/2026 — v1** deste arquivo: primeiro dump técnico consolidado. Reflete estado pós patch-bundle das 5 contaminações e pré-início do serviço FinAnalyticsBackfill.
