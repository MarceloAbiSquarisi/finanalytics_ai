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
- 9. Consultas SQL e views de referência
  - 9.1 Cobertura ponderada (`cobertura_diaria_v2`)
  - 9.2 Último tick por ticker
  - 9.3 Candidatos a AMARELO_parada_recente
  - 9.4 Ranking de liquidez (base da watchlist)
  - 9.5 Detectar contaminação residual
  - 9.6 Executar no container
  - 9.7 Gap-map por ticker × dia (`gap_map_1m`)
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

### 2.2 `ohlc_1m` (hypertable)

**Correção 19/abr/2026:** `ohlc_1m` **não é** continuous aggregate — é hypertable Timescale comum (27 chunks) com `PRIMARY KEY (time, ticker)`. Colunas: `time`, `ticker`, `open`, `high`, `low`, `close`, `volume`, `trades`, `vwap`, `source`. Confirmado via `SELECT * FROM timescaledb_information.continuous_aggregates` → 0 linhas e `\d ohlc_1m` (ver schema real).

Duas fontes populam a mesma tabela, diferenciadas por `source`:
- `source = 'brapi'` — escrito por `OHLC1mService._refresh` (`src/finanalytics_ai/application/services/ohlc_1m_service.py`) e pelo ingestor contínuo `workers/ohlc_1m_ingestor.py`. Puxa barras 1m da API pública brapi durante o pregão com polling de 60s.
- `source = 'tick_agg_v1'` — agregado manualmente de `market_history_trades` via `Melhorias/fase1_backfill_ohlc_1m.sql` (full watchlist) ou `Melhorias/fase1_backfill_ohlc_1m_range.sql` (range 4 dias, fork 19/abr). Usa `time_bucket('1 minute', trade_date)` + `array_agg` para OHLC + `sum(quantity)` + `count(*)`. INSERT com `ON CONFLICT (time, ticker) DO UPDATE` filtrando `source IN ('tick_agg_v1','brapi')` para não sobrescrever fontes futuras.

Jobs ativos em `ohlc_1m`: **apenas Columnstore Policy 1003** (compressão após 7 dias). **Não há job de refresh** — agregação por ticks é manual/sob-demanda.

Pregão B3 padrão (10:00–17:00) gera ~420 candles por ticker × dia quando há fluxo contínuo. Tickers VERDE de liquidez marginal (ENJU3, IFCM3, MATD3, AZTE3 etc.) podem ficar abaixo de 300 candles/dia mesmo com coleta completa — threshold absoluto arbitrário.

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

**Arquivo:** `D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\workers\profit_agent.py` (BaseHTTPRequestHandler + HTTPServer, 5606+ linhas).

**Endpoint `/metrics` (Prometheus exposition format, implementado 19/abr/2026):**
Expõe em `http://localhost:8002/metrics` (`text/plain; version=0.0.4`):
- `profit_agent_total_ticks` (counter) — ticks processados desde boot
- `profit_agent_total_orders` (counter)
- `profit_agent_total_assets` (gauge)
- `profit_agent_db_queue_size` (gauge) — fila de writes do DB
- `profit_agent_subscribed_tickers` (gauge)
- `profit_agent_market_connected` (gauge 0/1)
- `profit_agent_db_connected` (gauge 0/1)
- `profit_agent_total_probes` (counter) — chamadas a `/collect_history`
- `profit_agent_total_contaminations` (counter) — `first|last.ticker != requested`
- `profit_agent_probe_duration_seconds_sum` / `_count` (histograma-lite)

Instrumentação em `do_POST` via `agent._instrument_probe(body, result, duration_s)`. Requer restart do profit_agent (impacta Sprint 1 brevemente — retry 3x do backfill protege).

**Integração Grafana (follow-up):** o datasource "Prometheus" existente no Grafana aponta diretamente para `finanalytics_api:8000/metrics` — configuração inválida (Prometheus datasource precisa de Prometheus server, não endpoint de métricas). Para consumir `/metrics` do profit_agent é necessário:
1. Subir container `prometheus` com scrape config incluindo `profit_agent` (via `host.docker.internal:8002`)
2. Redirecionar datasource Grafana para o Prometheus server real
3. Adicionar painéis no dashboard `FinAnalytics AI — Qualidade de Dados`

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

**Arquitetura (confirmada no Sprint 6, 19/abr/2026):** stack DDD completa no repo:
- `workers/fintz_sync_worker.py` — entrypoint. Suporta `RUN_ONCE=true` (one-shot) ou schedule diário às 22:05 BRT via `run_scheduled`. Filtro opcional via `SYNC_DATASETS="ds1,ds2,..."`. SIGTERM/SIGINT graceful.
- `application/services/fintz_sync_service.py` — orquestra os 80 datasets; emite eventos (`fintz_sync.dataset.start|skip|completed`).
- `application/rules/fintz_sync_rule.py|fintz_post_sync_rule.py|fintz_sync_failed_rule.py` — regras de decisão.
- `infrastructure/adapters/fintz_client.py|fintz_market_client.py` — HTTP clients. `max_retries=3`, `api_timeout_s=30`, `link_timeout_s=300`.
- `infrastructure/database/repositories/fintz_repo.py` + `timescale/fintz_repo.py` — escrita idempotente.
- `domain/fintz/entities.py|ports.py|timescale_port.py` — domain + `ALL_DATASETS` (80 specs).

**Idempotência**: SHA-256 por dataset em `fintz_sync_log`. Datasets que não mudaram no vendor são `skip` antes de tocar o DB.

**Execução operacional (Sprint 6):**
```powershell
cd D:\Projetos\finanalytics_ai_fresh
docker compose --profile workers run --rm `
    -e RUN_ONCE=true `
    --entrypoint "" `
    fintz_sync_worker `
    python -m finanalytics_ai.workers.fintz_sync_worker
```

Ou com filtro: `-e SYNC_DATASETS="cotacoes_ohlc"`. Schedule daily embutido no próprio worker — não precisa nssm.

**Resultado 19/abr/2026 (full sync, 7m 16s):** `ok=5, skip=75, error=0, total_rows=13 961 262`. Artefato de log em `Melhorias/logs/fintz_sync_s6_*.log`.

**Sinks:**
- `fintz_cotacoes_ts` — cotações OHLC diárias (dataset `cotacoes_ohlc`)
- `fintz_indicadores_ts` — indicadores (P/L, ROE, etc. — dataset `indicador_*`)
- `fintz_itens_contabeis_ts` — itens contábeis (receita, custo, EBIT — dataset `item_*`)

**Bloqueio externo identificado (Sprint 6):** `fintz_cotacoes_ts` está congelado em `max(time)=2025-12-30`. O dataset `cotacoes_ohlc` é servido pela Fintz com SHA idêntico desde então — hash_unchanged skip automático. Não é bug nosso: a API Fintz/Varos não está publicando cotações pós-dez/2025. Requer contato com vendor para destravar.

**Bug `fintz_indicadores_ts` — fixed 19/abr/2026:**
- **Causa 1 (tipo)**: `fintz_sync_service._execute_sync` chamava `_upsert(df)` e depois `_write_timescale(df)` com o **mesmo df bruto**; a normalização (`FintzRepo._normalize_indicadores/_normalize_itens_contabeis`) acontece dentro do upsert, não propaga. O writer esperava `data_publicacao` mas o df vinha com `data` (parquet raw) → rename pulado → coluna `time` ausente/string → asyncpg rejeitava tipo.
- **Causa 2 (DSN)**: após fix do tipo, emergiu o erro de `Connect call failed ('127.0.0.1', 5433)` — `build_timescale_writer` lia `PROFIT_TIMESCALE_DSN` do `.env` do host (localhost:5433), inválido dentro do container.
- **Fixes aplicados**:
  1. `infrastructure/database/repositories/timescale_writer.py`: `write_indicadores` e `write_itens_contabeis` agora chamam `_ensure_data_publicacao` (rename `data`→`data_publicacao` se bruto) e `_ensure_tipo_periodo` (puxa de `spec.params['tipoPeriodo']`). Writer aceita df bruto ou normalizado.
  2. `docker-compose.yml` (service `fintz_sync_worker`): adicionado override `PROFIT_TIMESCALE_DSN=postgresql://finanalytics:...@finanalytics_timescale:5432/market_data`. Container precisa ser recriado (`docker compose up -d fintz_sync_worker`) para pegar o novo env no schedule diário.
- **Validação**: 5 indicadores que falhavam foram reparados: `indicador_L_P` (2 991 991 rows), `indicador_EBITDA_DespesasFinanceiras` (2 545 434), `indicador_EBITDA_EV` (2 712 201), `indicador_EBIT_EV` (2 714 925), `indicador_Passivos_Ativos` (2 996 711). Total em `fintz_indicadores_ts`: **13 961 262 rows**, 2011→2025.
- **Follow-up residual**: 75 de 80 datasets Fintz estão em `hash_unchanged` no `fintz_sync_log` desde syncs anteriores (pré-fix); nunca chegaram ao timescale writer. Para backfill completo do Timescale: `TRUNCATE fintz_sync_log` + full sync (4–6h). Não é urgente — OLTP tem os dados.

**Scripts diagnóstico (read-only, seguem ativos):**

| Script                                    | Propósito                                                          |
|-------------------------------------------|--------------------------------------------------------------------|
| `diag_fintz_atrasados_readonly.ps1`       | Lista tickers com `max(time) < hoje − 7`                           |
| `diag_fintz_gap_readonly.ps1`             | Mapeamento ticker × janela de gap                                  |
| `diag_fintz_script_validacao.ps1`         | Valida consistência Fintz vs ProfitDLL (EOD vs agregado de ticks)  |

**Legado (arquivado em `Melhorias/legado/`):**
- `fill_fintz_gap.ps1` — wrapper PS1 que chamava `fintz_sync_worker` RUN_ONCE. Substituído pela execução direta documentada acima.

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

## 9. Consultas SQL e views de referência

### 9.1 Cobertura ponderada por dia (via view `cobertura_diaria_v2`)

**Artefato oficial (Sprint 4, 19/abr/2026):** view `cobertura_diaria_v2` + tabela `ticker_stride` (215 stocks + 2 futuros, todos stride=10 conforme validação empírica nos 4 dias). DDL completa em `Melhorias/sprint4_ticker_stride.sql`.

```sql
-- uso rapido (filtrar por `dia::date` fara seq scan; use trade_date abaixo):
SELECT * FROM cobertura_diaria_v2
 WHERE dia BETWEEN '2026-04-13' AND '2026-04-16';
```

**Limitacao importante — uso performatico.** O filtro `dia BETWEEN ...` na view
aplica cast `trade_date::date` no predicate-pushdown, bloqueando o Index Only
Scan no PK `(ticker, trade_date, trade_number)` da `market_history_trades`.
Para janelas grandes (ou enquanto o backfill roda), executar o CTE direto com
`trade_date >= ts_start AND trade_date < ts_end_excl`:

```sql
WITH por_ticker AS (
    SELECT m.ticker, m.trade_date::date AS dia, s.classe,
           count(*) AS trades,
           CASE WHEN max(m.trade_number) - min(m.trade_number) > 0
                THEN (max(m.trade_number) - min(m.trade_number)) / NULLIF(s.stride_padrao, 0) + 1
                ELSE 1
           END AS esperado
      FROM market_history_trades m
      JOIN ticker_stride s ON s.ticker = m.ticker
     WHERE m.trade_date >= '2026-04-13'      -- usa index range
       AND m.trade_date <  '2026-04-17'
     GROUP BY m.ticker, m.trade_date::date, s.classe, s.stride_padrao
)
SELECT dia, classe, count(*) AS tickers,
       sum(trades) AS trades_total, sum(esperado) AS esperado_total,
       round(avg(100.0 * trades / NULLIF(esperado, 0)), 2)     AS pct_cob_media,
       round(100.0 * sum(trades) / NULLIF(sum(esperado), 0), 2) AS pct_cob_pond
  FROM por_ticker GROUP BY dia, classe ORDER BY dia, classe;
```

**Resultado nos 4 dias 13–16/abr/2026:**

| Dia        | Classe | Tickers | trades_total | esperado_total | pct_cob_pond |
|------------|--------|--------:|-------------:|---------------:|-------------:|
| 2026-04-13 | stock  |     133 |    1 378 941 |      1 378 974 |     100.00 % |
| 2026-04-13 | future |       2 |        1 741 |        297 445 |       0.59 % |
| 2026-04-14 | stock  |     133 |    1 440 838 |      1 440 838 |     100.00 % |
| 2026-04-14 | future |       2 |      313 048 |      5 034 355 |       6.22 % |
| 2026-04-15 | stock  |     133 |    1 416 400 |      1 416 400 |     100.00 % |
| 2026-04-15 | future |       2 |      729 925 |      4 990 775 |      14.63 % |
| 2026-04-16 | stock  |     133 |    1 108 156 |      1 108 163 |     100.00 % |
| 2026-04-16 | future |       2 |      554 061 |      5 491 751 |      10.09 % |

**Leitura:**
- **Stocks** — 100% cobertura ponderada nos 4 dias pós-patch. DoD OK.
- **Futuros** — a fórmula `(tn_max − tn_min) / stride + 1` super-estima o esperado em futuros. Medição empírica (19/abr) mostrou `stride_mediana=10` e `stride_p95=10` entre pares consecutivos (igual às ações), mas o intervalo total de `trade_number` no dia em WINFUT/WDOFUT contém **gaps largos** (> 10 000) entre lotes de ticks. Resultado: `esperado_total` fica 10–100× acima do real. **Não usar `pct_cob_pond` de futuros como KPI.** Sprint 5 (`gap_map_1m`) vai substituir por métrica temporal (minutos com ≥ 1 tick / minutos totais de pregão).
- **Tickers não-watchlist** (opções, BDRs fora da seleção etc.) são **excluídos** pela view (INNER JOIN com `ticker_stride`). Atualmente 96–104 tickers extras aparecem em `market_history_trades` nos 4 dias mas ficam fora da auditoria — trabalho de Sprint 5.

**`ticker_stride` (schema):**

```sql
CREATE TABLE ticker_stride (
    ticker         text PRIMARY KEY,
    classe         text NOT NULL CHECK (classe IN ('stock','future')),
    stride_padrao  int  NOT NULL,
    fonte          text NOT NULL DEFAULT 'manual',
    atualizado_em  timestamptz NOT NULL DEFAULT now()
);
```

Populado por `Melhorias/sprint4_ticker_stride.sql` — WINFUT/WDOFUT com fonte `empirical_2026-04-13..16`; stocks da watchlist com fonte `b3_stride_10`.

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

### 9.7 Gap-map por ticker × dia (via view `gap_map_1m`)

**Artefatos oficiais (Sprint 5, 19/abr/2026, Rota B):**
- Tabela `calendario_b3` (2 922 dias, 2020-01-01 → 2027-12-31, 101 feriados nacionais; 248–250 pregões/ano; 2027 sem feriados até B3 publicar).
- Tabela `profit_daily_cov` (materializada; PK `(ticker, dia)`, campos `minutos_com_tick` + `ticks`).
- Procedure `pop_profit_daily_cov(d_start date)` — popula por ticker com COMMIT após cada (progresso visível). **Janela expandida (19/abr/2026): 2026-01-02 → 2026-04-17** (135 tickers × 72 dias úteis = 9 489 linhas). Performance: stocks <10s cada, WDOFUT ~1m 36s, WINFUT ~43m (I/O contention com S1). Re-rodar periodicamente conforme S1 avança — usa `ON CONFLICT DO UPDATE`.
- View `gap_map_1m` — join de `calendario_b3` × `watchlist_tickers` (VERDE+AMARELO) + `profit_daily_cov` + `fintz_cotacoes_ts`; inclui `WINFUT`/`WDOFUT`.

DDL completa em:
- `Melhorias/sprint5_calendario_b3.sql`
- `Melhorias/sprint5_profit_daily_cov.sql` (DO block full — custoso em I/O)
- `Melhorias/sprint5_pop_cov_procedure.sql` (PROCEDURE com COMMIT — preferido)
- `Melhorias/sprint5_populate_cov_window.sql` (alternativa DO janela fixa)
- `Melhorias/sprint5_gap_map_1m.sql`

**Colunas de `gap_map_1m`:**
```
ticker, classe, dia, minutos_com_tick, ticks, pct_cob_intraday,
threshold_minutos, tem_profit_ok, tem_fintz, mediana_vol_brl
```

**Threshold tiered (não adaptativo por ticker — ver §dívidas):**

| classe | mediana_vol_brl | threshold_minutos |
|--------|-----------------|-------------------|
| future | —               | 200               |
| stock  | ≥ 100 M         | 300               |
| stock  | ≥ 10 M          | 200               |
| stock  | ≥ 1 M           | 100               |
| stock  | ≥ 100 k         | 50                |
| stock  | < 100 k         | 20                |

**Query exemplo (top-20 gaps com liquidez > 1M, 19/abr/2026, 8.99 s):**
```sql
SELECT ticker, classe, dia, minutos_com_tick, tem_fintz,
       pct_cob_intraday, mediana_vol_brl
  FROM gap_map_1m
 WHERE NOT tem_profit_ok
   AND (mediana_vol_brl > 1000000 OR classe='future')
   AND dia >= '2026-03-20' AND dia <= '2026-04-17'
 ORDER BY coalesce(mediana_vol_brl, 999999999999) DESC, dia ASC
 LIMIT 20;
```

**Dívidas técnicas (follow-ups Sprint 5 → 5.1):**
1. **`profit_daily_cov` atualmente cobre só 30 dias** (2026-03-20 → 2026-04-17). Expandir conforme Sprint 1 avança: re-rodar `CALL pop_profit_daily_cov('<data_mais_antiga>')` quando houver margem de I/O. DoD completo do plano (`count(*) ≈ 200 k`) já passa via `gap_map_1m` × `pregoes` (270 405) mas `minutos_com_tick`=0 nos dias não-populados.
2. **Threshold adaptativo (baseline por ticker)**: tentativa 90d / 60d teve custo de I/O proibitivo em disputa com Sprint 1. Implementar `ticker_profit_baseline` pós-S1 completo (script ficou em queda; ressuscitar quando houver janela de I/O livre).
3. **Fintz atrasado** até 2025-11-03 → `tem_fintz=false` para ~160 dias recentes em ~215 tickers. Sprint 6 resolve.
4. **Tickers não-watchlist** (opções etc) excluídos da view por design — Sprint 5.1 pode opcionalmente expandir para todos os tickers em `market_history_trades`.

## 10. Calendário B3 2020–2026 (feriados codificados)

> **Consolidado em `calendario_b3` no banco desde Sprint 5** — ver §9.7. Lista em código abaixo para referência e manutenção do Python.

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

**Grafana:** `http://localhost:3000` (container `finanalytics_grafana` 10.4.0, imagem `grafana/grafana:10.4.0`, volume persistente `finanalytics_ai_fresh_grafana_data`). Admin login: `admin` / `finanalytics2026` (resetado 19/abr/2026 via `grafana cli admin reset-admin-password`; trocar pela UI em produção).

**Datasources Grafana:**
- `Prometheus` (default) → `http://finanalytics_api:8000/metrics` (pré-existente, cobre métricas API)
- `TimescaleDB` (UID `afjkz1gl2ky68e`, Sprint 8) → `finanalytics_timescale:5432`/`market_data`, user `finanalytics`, `sslmode=disable`, `timescaledb=true`

**Dashboards Grafana:**
- `FinAnalytics AI — API Overview` (pré-existente)
- `FinAnalytics AI — Business Metrics` (pré-existente)
- `FinAnalytics AI — System Resources` (pré-existente)
- `FinAnalytics AI — Qualidade de Dados` (UID `finanalytics-data-quality`, Sprint 8, 19/abr/2026) — 3 painéis:
  - **Painel 1** Cobertura intraday 30d (table com cores gradiente via `gap_map_1m`, filtra liquidez >10M ou futures)
  - **Painel 2** Latência por ticker em horas desde último dia em `profit_daily_cov` (table com thresholds 24h/72h)
  - **Painel 3** Tickers com candle por minuto no dia corrente (bar timeseries sobre `ohlc_1m` 10:00-17:00)
  - JSON versionado em `Melhorias/grafana_dashboards/qualidade_dados.json` (re-deploy via `curl -u admin:... POST /api/dashboards/db --data @qualidade_dados.json`)

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
