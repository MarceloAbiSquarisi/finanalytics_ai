# FinAnalyticsAI — Estado Consolidado (17/abr/2026)

> Documento vivo. Consolida o que já foi feito, o que está rodando agora, e as melhorias planejadas com suas dependências. Sucessor da série de briefings pontuais (`CLAUDE_01.md`, `briefing_claude_code_pos_sessao_16abr.md`, `plano_proximos_passos_17-19abr.md`, `sessao_pausa_17abr_0450.md`).

---

## Sumário

- Parte 1 — Overview Executivo
  - 1.1 Visão e objetivo do produto
  - 1.2 Arquitetura em camadas
  - 1.3 Estado das fases (0–4)
  - 1.4 O que está rodando agora
  - 1.5 Destaques da sessão 17/abr/2026
  - 1.6 Roadmap de melhorias (R1–R10) e suas necessidades
- Parte 2 — Apêndices Técnicos
  - A. Modelo de dados TimescaleDB
  - B. profit_agent: bugs, patches, endpoints
  - C. Pipeline Fintz e gap handling
  - D. Watchlist canônica (Fase 0): critérios e tiers
  - E. Scripts de backfill: inventário e uso
  - F. Runbooks operacionais
  - G. Consultas SQL de referência
  - H. Paths, serviços e portas

---

# PARTE 1 — OVERVIEW EXECUTIVO

## 1.1 Visão e objetivo

FinAnalyticsAI é uma plataforma de análise quantitativa do mercado acionário brasileiro voltada a trading sistemático e research. O objetivo é manter uma base de dados histórica completa e de alta granularidade (tick-by-tick para ativos líquidos) que alimente modelos, backtests e dashboards próprios, sem depender de vendors caros em produção.

As fontes primárias são: (i) ProfitDLL (Nelogica) para ticks históricos e tempo real do mercado à vista brasileiro e futuros B3; (ii) Fintz para cotações diárias (EOD) agregadas e metadados fundamentalistas. O foco inicial é construir 6 anos de profundidade (2020–hoje) para ~135 stocks líquidos + contratos cheios de mini-índice e mini-dólar (WINFUT, WDOFUT).

## 1.2 Arquitetura em camadas

Resumida em prosa: na camada de persistência, um TimescaleDB (Postgres + extensão) roda em Docker local (porta 5433) e hospeda tanto o banco transacional de ticks (`market_data`) quanto a tabela de watchlist canônica. Acima dele fica o `profit_agent`, uma FastAPI que embrulha a ProfitDLL como um serviço HTTP local em `localhost:8002`, normalizando callbacks V1/V2 e gravando ticks em `market_history_trades` com `ON CONFLICT DO NOTHING`. Acima disso ficam os scripts de backfill (Python) e diagnóstico (PowerShell + SQL) no repositório `D:\Projetos\finanalytics_ai_fresh`. Uma pasta paralela em `D:\Investimentos\FinAnalytics_AI\Melhorias` serve de workbench: briefings, scripts .ps1 auxiliares, SQLs de auditoria e planos de fase. O fluxo é: ProfitDLL → profit_agent → TimescaleDB (ticks) → continuous aggregate `ohlc_1m` → consumidores (notebooks, backtests, dashboards futuros).

## 1.3 Estado das fases (0–4)

**Fase 0 — Watchlist canônica (concluída):** a tabela `watchlist_tickers` é a fonte de verdade sobre quais ativos o sistema monitora. Critério: mediana diária de `volume_negociado` > R$ 500k no período nov/2024 a nov/2025, ≥ 150 dias de cobertura em Fintz. Exclusões: IBOV (índice), BRFS3 e MRFG3 (fundiram em MBRF3), NTCO3 (delisted). Inclusão manual: MBRF3 (só 30 dias de Fintz por ser fusão recente, mas cobertura Profit completa). Resultado: ~135 tickers líquidos distribuídos em quatro tiers (VERDE / AMARELO_parada_recente / AMARELO_coleta_fraca / VERMELHO_sem_profit), ordenáveis por `mediana_vol_brl DESC`. Detalhes e DDL em Apêndice D.

**Fase 1 — Agregação OHLC 1m (concluída para 2025; re-run parcial pendente):** o continuous aggregate `ohlc_1m` deriva candles de 1 minuto diretamente de `market_history_trades`. A política de refresh é incremental. A re-agregação dos 4 dias 2026-04-13 a 04-16 está **adiada** até o backfill histórico terminar, porque esses dias sofreram contaminação do patch antigo e precisam ser re-coletados antes de re-agregar (o que já foi feito em 17/abr — vide 1.5). SQL em `aggregation_ohlc_1m_from_ticks.sql` e `fase1_backfill_ohlc_1m.sql`.

**Fase 2 — Sync Fintz refinado (pendente):** pipeline P23 para sincronizar Fintz com o estado canônico da watchlist. Hoje o `fill_fintz_gap.ps1` supre gaps manualmente. O refinamento envolve mapear faixas de datas atrasadas por ticker e disparar fetchs em lote respeitando quotas.

**Fase 3 — Mapeamento de gaps por ticker (em andamento):** já há scripts de diagnóstico (`diag_fintz_atrasados_readonly.ps1`, `diag_fintz_gap_readonly.ps1`) que produzem inventário de gaps. Falta consolidar em uma view `gap_map_1m` que junte Profit + Fintz por ticker × data e aponte exatamente onde falta cobertura em cada fonte.

**Fase 4 — Backfill histórico 2020–hoje (em andamento, HOJE):** universo = watchlist (~135 stocks) + futuros (WINFUT, WDOFUT). Período: 02-jan-2020 a hoje, ~1.500 pregões. Script `backfill_historico_watchlist.py` pronto, resiliente (retry 3×, graceful shutdown via signals, idempotente via ON CONFLICT). Falta: instalar nssm, criar o serviço via `setup_backfill_service.ps1`, dar start.

## 1.4 O que está rodando agora

**TimescaleDB** — container `finanalytics_timescale`, exposto em `localhost:5433`, volume nomeado persistente. Usuário `finanalytics`, banco `market_data`. Hoje armazena milhões de ticks de 2025-01 até 2026-04-17, watchlist completa, agregado `ohlc_1m` para 2025.

**profit_agent (FastAPI)** — em `localhost:8002`, embrulha a ProfitDLL. Endpoints principais: `GET /status`, `POST /collect_history`, `POST /subscribe`, `POST /unsubscribe`, `GET /ticks`. Desde 17-abr roda com o patch-bundle que corrige as 5 classes de bugs de contaminação (detalhes em Apêndice B). Pré-requisito operacional: Profit.exe aberto e logado antes de subir o agent, senão `/status` retorna `market_connected=false`.

**Backfill histórico (a iniciar)** — `backfill_historico_watchlist.py` pronto para rodar sob nssm como serviço `FinAnalyticsBackfill`. Logs rotacionam em 100 MB. AppStopMethodConsole=120 000ms garante graceful stop. ETA estimado para universo completo: 3–5 dias wall-time, dependendo de throughput do ProfitDLL.

**Nada de produção externa ainda** — tudo roda local no workstation do usuário. A decisão de hospedagem está documentada em `FinAnalyticsAI_Relatorio_Hospedagem.docx` e `proposta_decisao_15_dualgpu.md`.

## 1.5 Destaques da sessão 17/abr/2026

Três descobertas importantes nesta sessão, na ordem em que ocorreram:

**(a) 5 bugs em `/collect_history` causaram contaminação severa em 13–16 de abril.** Auditoria em `market_history_trades` revelou cobertura catastrófica nos quatro dias de 13/abr a 16/abr (0,04% / 1,84% / 14,16% / 9,84%). As causas: filtro de ticker ausente no callback histórico, contaminação cruzada entre probes consecutivos, race condition no `done.set()` (sinalizava término antes do último batch drenar), janela temporal mal-validada e re-aproveitamento de buffer entre tickers. Patch-bundle (7 alterações) aplicado em 17-abr. Detalhes em Apêndice B.

**(b) Validação do patch em escala: 540 probes, 0 contaminações.** O script `backfill_recentes_4dias.py` re-coletou a watchlist VERDE+AMARELO nos 4 dias problemáticos (135 tickers × 4 dias = 540 probes), registrando 5,3M ticks e **zero** violações de `first.ticker == last.ticker == requested`. Confirma empiricamente que o patch segura. Log completo em `backfill_4dias_log.txt`.

**(c) A fórmula `(tn_max − tn_min)/10 + 1` não serve para futuros.** Na auditoria pós-backfill, a cobertura ponderada ficou entre 25% e 82% em alguns dias, mesmo com a cobertura média em 99%+. Diagnóstico mostrou que todas as ações ficaram em pct=100% (stride de `trade_number` é 10 na B3 para à vista); os ~82% vieram inteiramente de WINFUT e WDOFUT, cujo stride é variável (~160 e ~70 respectivamente, não 10). A fórmula de “esperado” é stocks-specific e precisa ser adaptada para futuros quando for feita a auditoria ponderada daquela classe de ativos. Isso é uma tarefa de Fase 3.

Consequência prática: a **profundidade histórica** do ProfitDLL está validada (PETR4 2020-01-07 devolveu 45k ticks em probe isolada; janela de 7 dias em 2020 gerou 356k ticks sem erro), e é seguro deixar o backfill de 6 anos rodando. O script foi projetado para retomar sem perda de dados se o serviço reiniciar.

## 1.6 Roadmap de melhorias (R1–R10) e suas necessidades

**R1. Backfill histórico 2020–hoje (em execução iminente).** Necessidades: nssm instalado (`choco install nssm -y` ou download em nssm.cc), profit_agent up em 8002, Profit.exe logado. Comando: `.\setup_backfill_service.ps1 install` seguido de `start` e `tail`. Saída esperada em `logs\backfill_historico_stdout.log`.

**R2. Re-agregação ohlc_1m para 13–16/abr (pendente).** Necessidades: R1 concluído (ou ao menos para esses 4 dias), script `fase1_backfill_ohlc_1m.sql` adaptado com cláusula `WHERE trade_date::date IN (…)`. Após isso, auditar o agregado para garantir que o número de candles por ticker × dia bate com o esperado de pregão (horário oficial de 10:00 a 17:00 dá ~420 candles).

**R3. Auditoria ponderada adaptada a futuros (Fase 3).** Necessidades: descobrir o stride típico de `trade_number` em WINFUT e WDOFUT por contrato e por intensidade (pode variar com vencimento). Criar uma CTE que, por `exchange`, aplique o divisor correto (10 para stocks, stride empírico para futuros). Alternativa: abandonar a fórmula baseada em `trade_number` e medir cobertura em termos de janela temporal (minutos com ≥ 1 tick / minutos totais do pregão).

**R4. Investigar 3 tickers AMARELO_parada_recente.** BBDC3, BPAN4, GUAR3 caíram para AMARELO por parada recente de coleta. Necessidades: consulta `SELECT max(trade_date) FROM market_history_trades WHERE ticker IN (…)`, cruzar com Fintz e calendário B3 para distinguir delisting real, fusão, ou falha de coleta. Se for falha de coleta, forçar probe manual e reverter status para VERDE.

**R5. Assinar ProfitDLL para cobrir os ~80 VERMELHO_sem_profit.** Necessidades: revisar lista de VERMELHO, calcular custo/benefício da assinatura extra da Nelogica para desbloquear tickers fora do plano atual. Alternativa: aceitar cobertura parcial via Fintz-EOD apenas para esses.

**R6. Pipeline P23 Fintz refinado (Fase 2).** Necessidades: refatorar `fill_fintz_gap.ps1` em um job resiliente (idealmente também serviço), com rate-limiting (Fintz tem quota), paginação robusta e escrita idempotente em `fintz_cotacoes_ts`. Incluir monitoramento de latência de atualização por ticker.

**R7. View `gap_map_1m` consolidada (Fase 3).** Necessidades: join de `market_history_trades` (ticker × dia × count(*)) com `fintz_cotacoes_ts` e `calendario_b3`. Output: uma linha por ticker × dia com flags `tem_profit`, `tem_fintz`, `pct_cobertura_intraday`. Base para qualquer dashboard de qualidade de dados.

**R8. Decisão de hospedagem e deploy.** Necessidades: finalizar comparativo de TCO em `FinAnalyticsAI_Comparativo_Hospedagem.xlsx`, decidir entre workstation local persistente × VM dedicada × híbrido (dual-GPU). Documento em `proposta_decisao_15_dualgpu.md` cobre opções. Impacta plano de uptime do ProfitDLL, uma vez que a DLL é Windows-only e exige Profit.exe rodando.

**R9. Dashboards de qualidade de dados.** Necessidades: stack de visualização (Grafana + Postgres? Streamlit? Next.js?). Painéis mínimos: (i) cobertura por ticker × dia nos últimos 30 dias; (ii) latência de ticks (diferença entre `now()` e `max(trade_date)` por ticker); (iii) gaps em `ohlc_1m` por minuto no dia corrente. Dependência: R7.

**R10. Sistema de modelos e backtests.** Necessidades: R1 terminar (6 anos de histórico), arquitetura definida para features incrementais (materialized views com joins Fintz ↔ Profit), framework de backtest (VectorBT, Zipline fork, ou custom). Decisão de GPU (dual-GPU) impacta custo/tempo de treino de modelos maiores.

Priorização prática imediata: R1 (rodando) → R4 (rápido, descarta delisting) → R2 (depende de R1) → R7 (destranca R9) → R10.

---

# PARTE 2 — APÊNDICES TÉCNICOS

## A. Modelo de dados TimescaleDB

**`market_history_trades`** (hypertable, partition por `trade_date`): uma linha por tick. Chave composta `(ticker, trade_date, trade_number)` usada no `ON CONFLICT DO NOTHING`. Colunas principais: `ticker text`, `trade_date timestamptz`, `trade_number bigint`, `price numeric`, `quantity bigint`, `agressor char(1)`, `exchange char(1)`, `trade_id bigint`. `trade_number` é monotônico dentro de um pregão na B3 com stride 10 para ações (à vista); em futuros o stride varia (ver 1.5).

**`ohlc_1m`** (continuous aggregate de `market_history_trades`): candles de 1 minuto por `(ticker, bucket)`. Refresh incremental via `refresh_continuous_aggregate` chamado no fim do pregão ou sob demanda via `fase1_backfill_ohlc_1m.sql`. Colunas: `ticker`, `bucket timestamptz`, `open`, `high`, `low`, `close`, `volume`, `trade_count`.

**`watchlist_tickers`** (tabela comum, ~135 linhas): fonte canônica da watchlist. DDL em `create_watchlist_tickers.sql`. Campos: `ticker (PK)`, `mediana_vol_brl`, `media_vol_brl`, `mediana_trades_dia`, `dias_cobertura`, `ticks_2026`, `ultimo_tick`, `status` (enum textual: `VERDE`, `AMARELO_parada_recente`, `AMARELO_coleta_fraca`, `VERMELHO_sem_profit`). Refresh é manual após re-running dos diagnósticos de Fase 0.

**`fintz_cotacoes_ts`** (hypertable): EOD bars vindos da Fintz. Cobertura histórica mais ampla (começa antes de 2020) porém diária, sem granularidade intraday. Usada para cross-check e para fechar gaps quando ProfitDLL está indisponível.

## B. profit_agent: bugs, patches, endpoints

**Localização:** `D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\profit_agent.py` (FastAPI) embrulhando `ProfitDLL` da Nelogica via `ctypes`.

**Bugs identificados e corrigidos em 17-abr (patch-bundle v1):**

1. **Filtro de ticker ausente no callback histórico.** O callback V1 registrado via `SetHistoryTradeCallbackV2` (nome confuso da DLL) recebia ticks de **qualquer** assinatura ativa, não só do ticker solicitado. Patch: filtrar `if asset.ticker != requested_ticker: return` no callback.

2. **Contaminação entre probes consecutivos.** Buffer `self._history_buffer` não era limpo entre chamadas a `/collect_history`. Segunda chamada herdava ticks da primeira. Patch: `self._history_buffer.clear()` no início de cada probe.

3. **Race condition em `done.set()`.** O evento era sinalizado quando o callback de término disparava, mas batches ainda podiam estar a caminho na thread do callback. Patch: introduzido `time.sleep(0.5)` antes do `done.set()` e flush explícito via `pending_batches == 0`.

4. **Janela temporal mal-validada.** `dt_start` e `dt_end` eram aceitos sem checagem de consistência (fim < início passava). Patch: validação explícita + `raise HTTPException(400)` em requests inválidos.

5. **Re-aproveitamento de buffer V2.** Callback V2 (detalhado) reusava uma struct compartilhada entre probes, corrompendo dados do probe seguinte. Patch: deepcopy da struct antes de enfileirar.

**Endpoints (em uso ativo):** `GET /status` (retorna `market_connected`, `db_connected`, `total_assets`); `POST /collect_history` com body `{ticker, exchange, dt_start, dt_end, timeout}` retorna `{ticks, inserted, v1_count, v2_count, first, last, status}`; `POST /subscribe` e `POST /unsubscribe` para realtime; `GET /ticks` para leitura em streaming.

## C. Pipeline Fintz e gap handling

**Finalidade:** cotações diárias (EOD) para o universo B3 completo + metadados (setor, indicadores). Serve de backup quando ProfitDLL está indisponível, de fonte de verdade para o ranking de liquidez (base do critério Fase 0), e de dado de referência para validação cruzada.

**Tabela destino:** `fintz_cotacoes_ts` (hypertable).

**Scripts relevantes:**
- `fill_fintz_gap.ps1`: fecha gaps pontuais em datas específicas (uso manual).
- `diag_fintz_atrasados_readonly.ps1`: diagnóstico read-only de tickers com dados atrasados.
- `diag_fintz_gap_readonly.ps1`: mapeamento de gaps por ticker × período.
- `diag_fintz_script_validacao.ps1`: valida consistência Fintz vs ProfitDLL.

**Limitações conhecidas:** Fintz tem quota diária; rate-limiting é manual hoje. MBRF3 tem cobertura Fintz só desde a data da fusão (mar/2026) — é por isso que está marcada como AMARELO_coleta_fraca no campo Fintz mas VERDE no Profit.

## D. Watchlist canônica (Fase 0): critérios e tiers

**Arquivo de criação:** `D:\Investimentos\FinAnalytics_AI\Melhorias\create_watchlist_tickers.sql`.

**Critério de entrada:** mediana(`volume_negociado`) > R$ 500.000/dia no período 2024-11-01 a 2025-11-30, com ≥ 150 dias de cobertura em `fintz_cotacoes_ts`. Excluídos: IBOV (é índice, não ativo), BRFS3 e MRFG3 (fundiram em MBRF3 em mar/2026), NTCO3 (delisted).

**Inclusão manual:** MBRF3, pois só tem 30 dias de Fintz (pós-fusão) mas cobertura Profit completa.

**Tiers:**
- **VERDE:** ativo hoje, cobertura estável, tick flow OK.
- **AMARELO_parada_recente:** tinha fluxo até recentemente, parou. Candidatos a delisting/fusão ou falha de coleta. Hoje: BBDC3, BPAN4, GUAR3 (investigar — R4).
- **AMARELO_coleta_fraca:** tick flow abaixo do esperado; coleta funciona mas produz pouco tick/dia. Pode ser liquidez real baixa (BDRs pouco negociados) ou problema de subscrição.
- **VERMELHO_sem_profit:** presentes na Fintz mas ausentes em `market_history_trades`. Provavelmente fora da assinatura Nelogica atual (~80 tickers — R5).

**DDL CHECK:**

```sql
status text NOT NULL CHECK (status IN (
    'VERDE',
    'AMARELO_parada_recente',
    'AMARELO_coleta_fraca',
    'VERMELHO_sem_profit'
))
```

## E. Scripts de backfill: inventário e uso

**`scripts/backfill_historico_watchlist.py`** (18,4 KB) — **o principal**. Cobertura completa 2020–hoje. Carrega watchlist dinamicamente (VERDE + AMARELO_*), ordenado por `mediana_vol_brl DESC`. Inclui WINFUT e WDOFUT por default (ajustável via `--futures` / `--no-futures`). Timeout diferenciado: 300s para stocks, 2400s para futuros. Retry 3× com backoff 10/20/30s. Graceful shutdown via SIGINT/SIGTERM/SIGBREAK. Ideal para rodar sob nssm.
Uso típico: `python backfill_historico_watchlist.py` (defaults: 2020-01-02 até hoje, delay 2s).
Flags: `--start`, `--end`, `--delay`, `--from-ticker` (retoma a partir de um ticker), `--only "PETR4,VALE3"` (substitui watchlist), `--futures "WINFUT,WDOFUT"`, `--no-futures`, `--dry-run`.

**`scripts/backfill_recentes_4dias.py`** (11 KB) — re-coleta dos 4 dias 2026-04-13 a 04-16 pós-patch. Já rodou com sucesso em 17-abr (540 probes, 0 contaminação). Deixar como scaffold reutilizável para futuras re-coletas pontuais.

**`scripts/backfill_2025_top50.py`** (9 KB) — cenário B de ingestão inicial (top 50 por liquidez em 2025, ranking fixo no script). Foi o teste de carga original antes da watchlist canônica. Dados que ele gerou estão no banco — não precisa re-rodar.

**`scripts/backfill_history.py`** (13,5 KB) — script genérico mais antigo. Mantido por compatibilidade, mas os três acima cobrem todos os casos práticos.

**`scripts/backfill_resume.py`** (10 KB) — reexecução de probes específicos por (ticker, data) a partir de um CSV. Útil para retry manual de erros.

**`setup_backfill_service.ps1`** (`D:\Investimentos\FinAnalytics_AI\Melhorias`) — wrapper nssm para gerenciar o serviço `FinAnalyticsBackfill`. Ações: `install`, `start`, `stop`, `restart`, `status`, `tail`, `uninstall`. Logs em `D:\Investimentos\FinAnalytics_AI\Melhorias\logs\backfill_historico_{stdout,stderr}.log` com rotação em 100 MB.

## F. Runbooks operacionais

**Subir o backfill histórico (primeira vez):**

1. Abrir Profit.exe e fazer login.
2. Subir profit_agent: entrar na conda env `(finanalytics-ai)`, rodar `uvicorn finanalytics_ai.profit_agent:app --port 8002`. Verificar `curl http://localhost:8002/status` → `market_connected: true`.
3. Em PowerShell Admin, com a env ativa: `cd D:\Investimentos\FinAnalytics_AI\Melhorias`.
4. `.\setup_backfill_service.ps1 install` (cria serviço `FinAnalyticsBackfill`).
5. `.\setup_backfill_service.ps1 start`.
6. `.\setup_backfill_service.ps1 tail` (acompanha log; Ctrl+C sai sem afetar o serviço).

**Parar o backfill de forma limpa:** `.\setup_backfill_service.ps1 stop`. nssm envia Ctrl+Break, Python intercepta (SIGBREAK), finaliza o ticker corrente, sai. Espera até 120 s antes de força-bruta.

**Retomar após restart do Profit/workstation:** o serviço tem `Start SERVICE_DEMAND_START` (não sobe no boot). Isso é intencional — Profit.exe precisa estar logado primeiro. Passos: 1) abrir e logar Profit, 2) subir profit_agent, 3) `.\setup_backfill_service.ps1 start`. O script Python retoma automaticamente porque `get_collected_dates` pula qualquer dia já presente no banco e `ON CONFLICT DO NOTHING` torna qualquer duplicata benigna.

**Diagnosticar uma queda do backfill:** `.\setup_backfill_service.ps1 status` mostra últimas 40 linhas de stdout + 20 de stderr. Se `market_connected=false`, o problema está no Profit.exe ou na conexão DLL. Se for erro transitório repetido, ajustar `RETRY_MAX`/`RETRY_DELAY_BASE` no script e reiniciar.

**Re-agregar ohlc_1m após re-coleta de ticks:** `psql` no container, rodar `fase1_backfill_ohlc_1m.sql` com `WHERE trade_date::date IN (…)` nos dias afetados. A chamada de `refresh_continuous_aggregate('ohlc_1m', start, end)` é idempotente.

## G. Consultas SQL de referência

**Cobertura por dia (stocks apenas, ponderada por trade_number stride 10):**

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
       AND exchange = 'B'   -- exclui futuros
     GROUP BY ticker, trade_date::date
)
SELECT dia,
       count(*) AS tickers,
       sum(trades) AS trades_total,
       sum(esperado) AS esperado_total,
       round(avg(100.0 * trades / NULLIF(esperado, 0)), 2) AS pct_cob_media,
       round(100.0 * sum(trades) / NULLIF(sum(esperado), 0), 2) AS pct_cob_pond
  FROM por_ticker
 GROUP BY dia
 ORDER BY dia;
```

**Últimos tick por ticker (detecta coleta parada):**

```sql
SELECT ticker, max(trade_date) AS ultimo_tick
  FROM market_history_trades
 GROUP BY ticker
 ORDER BY ultimo_tick ASC
 LIMIT 30;
```

**Ranking de liquidez 2025 (base da watchlist):**

```sql
SELECT ticker, percentile_cont(0.5) WITHIN GROUP (ORDER BY volume_negociado) AS mediana_vol_brl
  FROM fintz_cotacoes_ts
 WHERE data BETWEEN '2024-11-01' AND '2025-11-30'
 GROUP BY ticker
HAVING count(*) >= 150
   AND percentile_cont(0.5) WITHIN GROUP (ORDER BY volume_negociado) > 500000
 ORDER BY mediana_vol_brl DESC;
```

**Detectar contaminação cruzada (validação pós-patch):**

```sql
SELECT ticker, trade_date::date AS dia, count(DISTINCT agressor) AS agressores
  FROM market_history_trades
 WHERE trade_date::date = '2026-04-17'
 GROUP BY ticker, trade_date::date
HAVING count(DISTINCT agressor) > 3;
-- esperado: agressor é 'C'/'V'/'D'/' ', até 4. Mais que isso é ruído.
```

## H. Paths, serviços e portas

**Repositório principal:** `D:\Projetos\finanalytics_ai_fresh`.
- `src/finanalytics_ai/profit_agent.py` — FastAPI wrapper.
- `scripts/backfill_*.py` — scripts de ingestão.
- `scripts/diag_*.py` — diagnósticos Python.

**Workbench de melhorias:** `D:\Investimentos\FinAnalytics_AI\Melhorias`.
- `*.ps1` — diagnósticos e scripts operacionais PowerShell.
- `*.sql` — SQLs de auditoria, agregação e criação.
- `*.md` — briefings, planos, este documento.
- `logs/` — logs do serviço nssm.

**Serviços locais:**
- TimescaleDB (Docker) em `localhost:5433`, container `finanalytics_timescale`, banco `market_data`, user `finanalytics`.
- profit_agent em `localhost:8002` (FastAPI/uvicorn).
- Serviço planejado: `FinAnalyticsBackfill` (nssm), status `SERVICE_DEMAND_START`.

**DSN padrão (env `PROFIT_TIMESCALE_DSN`):** `postgresql://finanalytics:timescale_secret@localhost:5433/market_data`.

**Conda env:** `finanalytics-ai` — deve estar ativa para rodar scripts Python e para `setup_backfill_service.ps1 install` (fixa o python.exe da env no serviço).

---

## Changelog do documento

- **17/abr/2026 — v1** (este arquivo): consolidação inicial pós-sessão de debug dos 5 bugs. Estado Fase 0 concluída, Fase 1 parcial, Fase 4 em imminent start.
