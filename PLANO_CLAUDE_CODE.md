# FinAnalyticsAI — Plano Consolidado para Claude Code

> Documento único de execução. Cobre do estado em 18/abr/2026 até o fechamento do roadmap R1–R10. **Supera** `SPRINTS_CLAUDE_CODE.md` e `SPRINTS_PARALELO_BACKFILL.md` — use este como referência primária.

**Versão:** v1 — 18/abr/2026
**Escopo:** execução operacional completa para Claude Code entregar R1–R10.
**Contexto técnico:** `ESTADO_TECNICO.md` (topologia, schemas, API, patches). **Ler antes de começar.**

---

## 1. Estado atual (snapshot 18/abr/2026)

- **Backfill histórico (R1)**: rodando como serviço nssm `FinAnalyticsBackfill`. Janela 2020-01-02 → hoje. Watchlist VERDE+AMARELO (~135 ações) + WINFUT + WDOFUT. ETA 3–5 dias wall-clock.
- **Patches 17/abr aplicados**: 5 bugs do `/collect_history` corrigidos; 4 dias (2026-04-13..04-16) re-coletados; contaminação zerada.
- **Watchlist canônica**: ~135 tickers ativos (VERDE + AMARELO_parada_recente + AMARELO_coleta_fraca). VERMELHO_sem_profit = ~80 tickers aguardando decisão R5.
- **ohlc_1m**: continuous aggregate criado, backfill Fase 1 concluído, mas precisa refresh dos 4 dias re-coletados (Sprint 3).
- **Fintz EOD**: sincronizado até ~13/abr; 475 tickers atrasados (<out/2025) identificados. P23 refinado (Sprint 6) vai limpar isso.
- **Futuros**: stride variável (WINFUT ~160, WDOFUT ~70) — auditoria ponderada atual (stride 10 fixo) reporta falso-positivo; Sprint 4 corrige.

Tarefas pendentes no momento em que este plano começa:

| Área | Pendência | Sprint que resolve |
|------|-----------|---------------------|
| BBDC3 / BPAN4 / GUAR3 | status AMARELO_parada_recente não diagnosticado | Sprint 2 |
| `ohlc_1m` 2026-04-13..04-16 | candles do range não refletem ticks pós-patch | Sprint 3 |
| Auditoria futuros | fórmula falha em WINFUT/WDOFUT | Sprint 4 |
| gap_map_1m | não existe; usa patchwork de `diag_*.ps1` | Sprint 5 |
| Fintz gap-filling | `fill_fintz_gap.ps1` manual, frágil | Sprint 6 |
| VERMELHO_sem_profit | sem decisão de cobertura | Sprint 7 |
| Dashboards qualidade | inexistentes | Sprint 8 |
| Hospedagem 5y+ | planilha TCO com estimativas, não dados reais | Sprint 9 |
| Modelos / backtests | peças isoladas sem pipeline | Sprint 10 |

---

## 2. Regras operacionais (não violar)

Estas regras valem enquanto o Sprint 1 estiver ativo (`nssm status FinAnalyticsBackfill` = `SERVICE_RUNNING`).

1. **Nunca parar o serviço de backfill** exceto em recuperação de falha. Para parar graceful: `.\setup_backfill_service.ps1 stop` (Ctrl+Break, timeout 120s).
2. **Não chamar `/collect_history`** para o ticker que o backfill está processando. Conferir em:
   ```powershell
   Get-Content D:\Investimentos\FinAnalytics_AI\Melhorias\logs\backfill_historico_stdout.log -Tail 5
   ```
   A última linha tem `Processando <TICKER>`. Aguardar esse ticker sair do loop.
3. **Não executar `refresh_continuous_aggregate`** em um range que esteja sendo ingerido. O Sprint 3 refresha 2026-04-13..04-16 — seguro (dias passados, já completos).
4. **UPDATE em `watchlist_tickers.status` é aceitável** apenas quando Sprint 2 fechar diagnóstico (3 tickers pontuais). Nada além.
5. **Nenhum `pg_dump` full** do `market_history_trades` durante o backfill (lock pesado no container). Se necessário dump, usar `--data-only --table=nome_especifico`.
6. **Monitorar o serviço a cada 12–24h** rodando o smoke test da §12. Se o serviço cair, recuperar conforme §13.

---

## 3. Fluxo completo de execução

Todos os sprints são **por fase, não por calendário**. Cada um termina quando o DoD bate. A ordem abaixo respeita dependências reais; o que está marcado com **⚡ paralelo** pode rodar em concorrência com o Sprint 1.

```
Sprint 0  ─── pré-voo (requisito de tudo)
    │
    ▼
Sprint 1  ─── R1: backfill 2020-hoje (rodando; wall-clock 3-5d)
    │
    ├─ ⚡ Sprint 2  ─── R4: AMARELO (BBDC3/BPAN4/GUAR3)
    ├─ ⚡ Sprint 3  ─── R2: re-agregar ohlc_1m (dias 13-16/abr)
    └─ ⚡ Sprint 4  ─── R3: stride futuros + cobertura_v2
              │
              ▼
         Sprint 5  ─── R7: gap_map_1m (+ calendario_b3)
              │
              ├─── Sprint 6  ─── R6: Fintz refinado
              │           │
              │           ▼
              │      Sprint 7  ─── R5: decisão VERMELHO_sem_profit
              │
              └─── Sprint 8  ─── R9: dashboards qualidade

Sprint 9  ─── R8: decisão hospedagem (off-critical; rodar com dados reais)
Sprint 10 ─── R10: modelos + backtests (exige Sprint 1 completo + Sprint 5)
```

**Timing prático:**
- Dia 1–3 do backfill: Sprints 2, 3, 4 (paralelos).
- Dia 3–4: Sprint 5 (precisa de 3+4 prontos).
- Dia 4–5: preparação Sprint 6 (scaffold) + Sprint 8 (Grafana).
- Pós-backfill (a partir do dia 6): Sprints 6 → 7, 9, 10 na sequência.

---

## 4. Sprint 0 — Pré-voo

**Quando executar:** antes de qualquer outra coisa; confirmar a qualquer momento que houver suspeita de ambiente inconsistente.

**Objetivo:** garantir que Profit.exe, profit_agent, conda env, TimescaleDB e nssm estão operacionais.

**Passos:**

1. Profit.exe aberto e logado (manual, fora do Claude Code).
2. Subir profit_agent em janela dedicada:
   ```powershell
   conda activate finanalytics-ai
   cd D:\Projetos\finanalytics_ai_fresh
   uvicorn finanalytics_ai.profit_agent:app --port 8002
   ```
3. Validar:
   ```powershell
   Invoke-RestMethod http://localhost:8002/status | ConvertTo-Json -Depth 3
   ```
   Esperado: `market_connected: true`, `db_connected: true`, `total_assets > 0`.
4. Instalar nssm se ausente:
   ```powershell
   choco install nssm -y
   nssm --version
   ```
5. Validar sessão Admin:
   ```powershell
   [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
   ```
6. Validar banco:
   ```powershell
   docker ps --filter "name=finanalytics_timescale" --format "{{.Status}}"
   docker exec -i finanalytics_timescale psql -U finanalytics -d market_data -c "SELECT count(*) FROM watchlist_tickers WHERE status IN ('VERDE','AMARELO_parada_recente','AMARELO_coleta_fraca');"
   ```

**DoD:**
- `/status` retorna `market_connected: true` e `db_connected: true`.
- `nssm --version` ≥ 2.24.
- PowerShell Admin confirmado.
- Watchlist com ≥ 130 tickers ativos.

**Riscos:**
- Se `market_connected: false`, reiniciar Profit.exe e refazer login (profit_agent só enxerga DLL se Profit.exe está vivo).
- nssm fora do PATH faz `setup_backfill_service.ps1 install` abortar em `Require-Command`.

---

## 5. Sprint 1 — R1: Backfill histórico 2020-hoje

**Quando executar:** em curso desde 18/abr. Esta seção documenta monitoramento + DoD final.

**Objetivo:** popular `market_history_trades` com ticks de 02-jan-2020 até hoje para watchlist VERDE+AMARELO + WINFUT + WDOFUT.

**Monitoramento (rotina diária):**

1. Status do serviço:
   ```powershell
   nssm status FinAnalyticsBackfill
   Get-Content D:\Investimentos\FinAnalytics_AI\Melhorias\logs\backfill_historico_stdout.log -Tail 30
   ```

2. Progresso ano a ano:
   ```sql
   SELECT extract(year from trade_date) AS ano,
          count(DISTINCT ticker) AS tickers_ativos,
          count(DISTINCT (ticker, trade_date::date)) AS ticker_dia_pairs,
          count(*) AS ticks_totais
     FROM market_history_trades
    WHERE exchange = 'B'
    GROUP BY ano
    ORDER BY ano;
   ```
   Esperado crescente; 2020 com ~24 700 ticker_dia_pairs (247 pregões × ~100 tickers presentes em 2020).

3. Contaminação (tem que ser 0):
   ```powershell
   Select-String -Path D:\Investimentos\FinAnalytics_AI\Melhorias\logs\backfill_historico_stdout.log -Pattern "CONT_ticker|Contaminacoes" | Select-Object -Last 10
   ```

**DoD (quando `SERVICE_STOPPED`):**
- Último bloco `RESUMO` no log mostra `Contaminacoes: 0` e `Erros/warns` < 1% dos probes.
- Ao menos 80% dos tickers da watchlist com ≥ 1 000 pregões entre 2020-01-02 e 2025-12-30 (de ~1 500 disponíveis no período).
- ≥ 5 anos de dados (2020–2025) na query de monitoramento #2.

**Se falhar:**
- Profit.exe travado/crash: o serviço respawna em 30s, mas se DLL ficar sem `market_connected`, aborta de novo. Reiniciar Profit manualmente, depois:
  ```powershell
  D:\Investimentos\FinAnalytics_AI\Melhorias\setup_backfill_service.ps1 restart
  ```
- Rajada de HTTP 500 do profit_agent: aumentar `--delay` para 3–4s via `nssm edit FinAnalyticsBackfill` e reiniciar.

**Arquivos tocados:** nenhum código; dados em `market_history_trades`; logs em `logs/backfill_historico_*.log`.

---

## 6. Sprint 2 — R4: Investigar AMARELO (BBDC3 / BPAN4 / GUAR3)

**Quando executar:** ⚡ em paralelo com Sprint 1. Requer ~1–2h.

**Objetivo:** classificar cada um dos 3 tickers como (a) delisting/fusão real, (b) falha de coleta recuperável, ou (c) liquidez caída; atualizar `watchlist_tickers.status`.

**Pré-requisitos:** profit_agent up. Regra de ouro #2 da §2 vale para probe manual.

**Passos:**

1. Levantamento:
   ```sql
   SELECT w.ticker, w.status, w.mediana_vol_brl,
          w.ultimo_tick AS snapshot_ultimo_tick,
          (SELECT max(trade_date)::date FROM market_history_trades m WHERE m.ticker = w.ticker) AS real_ultimo_tick,
          (SELECT max(data) FROM fintz_cotacoes_ts f WHERE f.ticker = w.ticker) AS fintz_ultimo
     FROM watchlist_tickers w
    WHERE w.ticker IN ('BBDC3','BPAN4','GUAR3');
   ```

2. Cross-check Fintz:
   ```sql
   SELECT ticker, data, volume_negociado, close
     FROM fintz_cotacoes_ts
    WHERE ticker IN ('BBDC3','BPAN4','GUAR3')
      AND data >= '2026-01-01'
    ORDER BY ticker, data DESC
    LIMIT 60;
   ```
   **Leitura:** Fintz com volume > 0 + Profit parou = falha de coleta (recuperável); ambos parados = delisting/fusão.

3. Se falha de coleta, probe manual (respeitando regra de ouro #2):
   ```powershell
   $body = @{
     ticker="BBDC3"; exchange="B"
     dt_start="$(Get-Date -Format 'dd/MM/yyyy') 09:00:00"
     dt_end="$(Get-Date -Format 'dd/MM/yyyy') 18:30:00"
     timeout=120
   } | ConvertTo-Json
   Invoke-RestMethod -Uri http://localhost:8002/collect_history -Method POST -Body $body -ContentType "application/json"
   ```
   Aceito: `ticks > 0` **e** `first.ticker == last.ticker == "BBDC3"`.

4. UPDATE por ticker:
   ```sql
   -- Recuperado
   UPDATE watchlist_tickers SET status = 'VERDE', atualizado_em = now() WHERE ticker = 'BBDC3';
   -- Delisting/fusão
   UPDATE watchlist_tickers SET status = 'VERMELHO_sem_profit', atualizado_em = now() WHERE ticker = 'BPAN4';
   ```

5. Registrar no changelog de `ESTADO_CONSOLIDADO.md` §1.6 R4:
   ```markdown
   - **18/abr/2026**: R4 fechado.
     - BBDC3: falha de coleta, recuperado via probe manual → VERDE.
     - BPAN4: Fintz também parado desde <data>. Delisting confirmado → VERMELHO_sem_profit.
     - GUAR3: ...
   ```

**DoD:**
- 3 tickers com status final (VERDE ou VERMELHO_sem_profit).
- Changelog registrado.

**Arquivos tocados:** `watchlist_tickers`, `ESTADO_CONSOLIDADO.md`.

---

## 7. Sprint 3 — R2: Re-agregar ohlc_1m (2026-04-13..04-16)

**Quando executar:** ⚡ em paralelo com Sprint 1. Requer ~30–60 min.

**Objetivo:** recompor candles de 1 min para os 4 dias re-coletados em 17/abr, garantindo que `ohlc_1m` reflita os dados pós-patch.

**Pré-requisitos:** ticks dos 4 dias já completos em `market_history_trades` (confirmado pós-17/abr).

**Passos:**

1. Checar cobertura bruta:
   ```sql
   SELECT trade_date::date AS dia,
          count(DISTINCT ticker) AS tickers,
          count(*) AS ticks
     FROM market_history_trades
    WHERE trade_date::date IN ('2026-04-13','2026-04-14','2026-04-15','2026-04-16')
    GROUP BY trade_date::date
    ORDER BY trade_date::date;
   ```
   Esperado: ≥ 130 tickers/dia.

2. Backup preventivo:
   ```sql
   CREATE TABLE IF NOT EXISTS ohlc_1m_backup_20260417 AS
   SELECT * FROM ohlc_1m WHERE bucket >= '2026-04-13' AND bucket < '2026-04-17';
   ```

3. Pausar policy:
   ```sql
   SELECT alter_job(job_id, scheduled => false)
     FROM timescaledb_information.jobs
    WHERE application_name LIKE '%ohlc_1m%';
   ```

4. Refresh manual:
   ```sql
   CALL refresh_continuous_aggregate('ohlc_1m', '2026-04-13 00:00:00', '2026-04-17 00:00:00');
   ```

5. Validar candles por ticker-dia (esperar zero linhas):
   ```sql
   SELECT bucket::date AS dia, ticker, count(*) AS candles
     FROM ohlc_1m
    WHERE bucket::date IN ('2026-04-13','2026-04-14','2026-04-15','2026-04-16')
    GROUP BY bucket::date, ticker
   HAVING count(*) < 300
    ORDER BY candles ASC
    LIMIT 30;
   ```

6. Sanity OHLC em PETR4 09:30:
   ```sql
   WITH c AS (
     SELECT open, high, low, close, volume, trade_count
       FROM ohlc_1m
      WHERE ticker='PETR4' AND bucket='2026-04-15 09:30:00-03'
   ),
   t AS (
     SELECT first_value(price) OVER (ORDER BY trade_date) AS open_real,
            max(price) OVER () AS high_real,
            min(price) OVER () AS low_real,
            last_value(price)  OVER (ORDER BY trade_date
                                     ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS close_real,
            sum(quantity) OVER () AS vol_real,
            count(*) OVER () AS tn
       FROM market_history_trades
      WHERE ticker='PETR4'
        AND trade_date >= '2026-04-15 09:30:00-03'
        AND trade_date <  '2026-04-15 09:31:00-03'
      LIMIT 1
   )
   SELECT c.*, t.open_real, t.high_real, t.low_real, t.close_real, t.vol_real, t.tn
     FROM c, t;
   ```
   Esperado: open/high/low/close dentro de ±1 tick de preço.

7. Reativar policy:
   ```sql
   SELECT alter_job(job_id, scheduled => true)
     FROM timescaledb_information.jobs
    WHERE application_name LIKE '%ohlc_1m%';
   ```

**DoD:**
- Passo 5 retorna zero linhas para tickers VERDE.
- Passo 6 bate com ticks brutos (tolerância ±1 tick).
- Policy reativada.

**Arquivos tocados:** `ohlc_1m` (refreshed), `ohlc_1m_backup_20260417` (backup temporário; dropar após 7 dias).

**Riscos:** refresh pode demorar 5–30 min; rodar fora de janela de ingestão intraday se possível.

---

## 8. Sprint 4 — R3: Auditoria de stride em futuros

**Quando executar:** ⚡ em paralelo com Sprint 1. Requer ~1–2h.

**Objetivo:** criar fórmula de cobertura esperada que funcione para ações (stride 10) e futuros (stride variável). Entregar tabela `ticker_stride` e view `cobertura_diaria_v2`.

**Pré-requisitos:** ≥ 1 mês de futuros em `market_history_trades` (já tem via backfill inicial).

**Passos:**

1. Medir stride empírico por contrato e mês:
   ```sql
   WITH pares AS (
     SELECT ticker, trade_date::date AS dia, trade_number,
            lag(trade_number) OVER (PARTITION BY ticker, trade_date::date ORDER BY trade_number) AS tn_prev
       FROM market_history_trades
      WHERE exchange='F' AND trade_date::date >= '2025-01-01'
   )
   SELECT ticker,
          extract(year from dia) AS ano,
          extract(month from dia) AS mes,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY (trade_number - tn_prev)) AS stride_mediana,
          percentile_cont(0.95) WITHIN GROUP (ORDER BY (trade_number - tn_prev)) AS stride_p95
     FROM pares
    WHERE tn_prev IS NOT NULL AND (trade_number - tn_prev) BETWEEN 1 AND 10000
    GROUP BY ticker, extract(year from dia), extract(month from dia)
    ORDER BY ticker, ano, mes;
   ```
   Esperado: WINFUT mediana ~160, WDOFUT ~70.

2. Persistir `ticker_stride`:
   ```sql
   CREATE TABLE IF NOT EXISTS ticker_stride (
     ticker           text PRIMARY KEY,
     exchange         char(1) NOT NULL,
     stride_padrao    int NOT NULL,
     atualizado_em    timestamptz NOT NULL DEFAULT now()
   );

   INSERT INTO ticker_stride (ticker, exchange, stride_padrao)
   SELECT ticker, 'F', round(avg(stride_mediana))::int
     FROM (
       WITH pares AS (
         SELECT ticker, trade_date::date AS dia, trade_number,
                lag(trade_number) OVER (PARTITION BY ticker, trade_date::date ORDER BY trade_number) AS tn_prev
           FROM market_history_trades
          WHERE exchange='F' AND trade_date::date >= '2025-01-01'
       )
       SELECT ticker,
              percentile_cont(0.5) WITHIN GROUP (ORDER BY (trade_number - tn_prev)) AS stride_mediana
         FROM pares
        WHERE tn_prev IS NOT NULL AND (trade_number - tn_prev) BETWEEN 1 AND 10000
        GROUP BY ticker
     ) q
    GROUP BY ticker
   ON CONFLICT (ticker) DO UPDATE SET stride_padrao=EXCLUDED.stride_padrao, atualizado_em=now();

   INSERT INTO ticker_stride (ticker, exchange, stride_padrao)
   SELECT DISTINCT ticker, 'B', 10 FROM market_history_trades WHERE exchange='B'
   ON CONFLICT (ticker) DO NOTHING;
   ```

3. View `cobertura_diaria_v2`:
   ```sql
   CREATE OR REPLACE VIEW cobertura_diaria_v2 AS
   WITH por_ticker AS (
     SELECT m.ticker, m.trade_date::date AS dia,
            count(*) AS trades,
            CASE WHEN max(m.trade_number) - min(m.trade_number) > 0
                 THEN (max(m.trade_number) - min(m.trade_number)) / NULLIF(s.stride_padrao,0) + 1
                 ELSE 1
            END AS esperado,
            m.exchange
       FROM market_history_trades m
       LEFT JOIN ticker_stride s ON s.ticker = m.ticker
      GROUP BY m.ticker, m.trade_date::date, s.stride_padrao, m.exchange
   )
   SELECT dia, exchange, count(*) AS tickers,
          sum(trades) AS trades_total,
          sum(esperado) AS esperado_total,
          round(avg(100.0 * trades / NULLIF(esperado, 0)), 2)    AS pct_cob_media,
          round(100.0 * sum(trades) / NULLIF(sum(esperado), 0), 2) AS pct_cob_pond
     FROM por_ticker
    GROUP BY dia, exchange;
   ```

4. Validar nos 4 dias re-coletados:
   ```sql
   SELECT * FROM cobertura_diaria_v2
    WHERE dia BETWEEN '2026-04-13' AND '2026-04-16'
    ORDER BY dia, exchange;
   ```
   Esperado: stocks `pct_cob_pond ≥ 95%`, futuros idem.

5. Atualizar `ESTADO_TECNICO.md` §9.1 com link para a view.

**DoD:**
- `ticker_stride` com ≥ 130 stocks + 2 futuros.
- `cobertura_diaria_v2` existe e retorna linhas consistentes.
- `ESTADO_TECNICO.md` atualizado.

**Arquivos tocados:** `ticker_stride` (nova), `cobertura_diaria_v2` (nova view), `ESTADO_TECNICO.md`.

**Riscos:** stride de futuros pode mudar entre vencimentos; se precisão insuficiente, refinar para `(ticker, month)` em vez de apenas `ticker`.

---

## 9. Sprint 5 — R7: View `gap_map_1m` + calendário B3

**Quando executar:** após Sprints 3 e 4 fecharem. Requer ~1.5–2h.

**Objetivo:** uma view `gap_map_1m` com linha por `(ticker, dia)` + flags de cobertura Profit/Fintz. Substitui `diag_*.ps1`.

**Pré-requisitos:** Sprint 3 (ohlc_1m consistente) e Sprint 4 (`cobertura_diaria_v2`).

**Passos:**

1. Popular `calendario_b3`:
   ```sql
   CREATE TABLE IF NOT EXISTS calendario_b3 (
     dia        date PRIMARY KEY,
     eh_pregao  boolean NOT NULL,
     motivo     text
   );

   INSERT INTO calendario_b3 (dia, eh_pregao, motivo)
   SELECT d::date,
          NOT (extract(dow from d) IN (0,6)),
          CASE WHEN extract(dow from d) IN (0,6) THEN 'fim de semana' END
     FROM generate_series('2020-01-01'::date, '2027-12-31'::date, interval '1 day') d
   ON CONFLICT DO NOTHING;
   ```

2. Aplicar feriados (extrair de `scripts/backfill_historico_watchlist.py:HOLIDAYS_BR`):
   ```powershell
   python -c "from scripts.backfill_historico_watchlist import HOLIDAYS_BR; print('\n'.join(f\"UPDATE calendario_b3 SET eh_pregao=false, motivo='feriado nacional' WHERE dia='{d.isoformat()}';\" for d in HOLIDAYS_BR))" | Out-File -Encoding utf8 Melhorias\sql\calendario_feriados.sql
   Get-Content Melhorias\sql\calendario_feriados.sql | docker exec -i finanalytics_timescale psql -U finanalytics -d market_data
   ```

3. Sanity calendário:
   ```sql
   SELECT extract(year from dia) AS ano,
          count(*) FILTER (WHERE eh_pregao) AS pregoes
     FROM calendario_b3 GROUP BY ano ORDER BY ano;
   ```
   Esperado: 247–252 pregões/ano.

4. View `gap_map_1m`:
   ```sql
   CREATE OR REPLACE VIEW gap_map_1m AS
   WITH pregoes AS (
     SELECT dia FROM calendario_b3 WHERE eh_pregao = true AND dia >= '2020-01-02'
   ),
   watch AS (
     SELECT ticker, mediana_vol_brl FROM watchlist_tickers
      WHERE status = 'VERDE' OR status LIKE 'AMARELO_%'
   ),
   profit_cov AS (
     SELECT ticker, bucket::date AS dia, count(*) AS candles
       FROM ohlc_1m GROUP BY ticker, bucket::date
   ),
   fintz_cov AS (
     SELECT ticker, data AS dia, 1 AS tem_fintz FROM fintz_cotacoes_ts
   )
   SELECT w.ticker, p.dia,
          coalesce(pc.candles, 0)             AS candles_1m,
          (coalesce(pc.candles, 0) >= 300)    AS tem_profit_ok,
          coalesce(fc.tem_fintz, 0) = 1       AS tem_fintz,
          w.mediana_vol_brl,
          round(100.0 * coalesce(pc.candles, 0) / 420.0, 1) AS pct_cob_intraday
     FROM watch w
     CROSS JOIN pregoes p
     LEFT JOIN profit_cov pc ON pc.ticker = w.ticker AND pc.dia = p.dia
     LEFT JOIN fintz_cov  fc ON fc.ticker = w.ticker AND fc.dia = p.dia;
   ```

5. Índices de apoio:
   ```sql
   CREATE INDEX IF NOT EXISTS idx_ohlc_1m_ticker_bucket ON ohlc_1m (ticker, bucket);
   CREATE INDEX IF NOT EXISTS idx_fintz_ticker_data ON fintz_cotacoes_ts (ticker, data);
   ```

6. Query exemplo — top 20 gaps por liquidez:
   ```sql
   SELECT ticker, dia, candles_1m, tem_fintz, pct_cob_intraday, mediana_vol_brl
     FROM gap_map_1m
    WHERE NOT tem_profit_ok AND mediana_vol_brl > 1000000 AND dia >= '2024-01-01'
    ORDER BY mediana_vol_brl DESC, dia ASC
    LIMIT 20;
   ```

7. Documentar em `ESTADO_TECNICO.md` §9 como query padrão.

**DoD:**
- `count(*) FROM gap_map_1m` ≈ watchlist_ativa × pregoes_2020_hoje (~200 000).
- Query do passo 6 roda em < 10s.
- Calendário com 2020–2027 e feriados aplicados.

**Arquivos tocados:** `calendario_b3` (tabela), `gap_map_1m` (view), índices novos, `ESTADO_TECNICO.md`, `Melhorias/sql/calendario_feriados.sql`.

**Riscos:** threshold `300 candles = ok` é arbitrário. Para tickers de baixa liquidez, considerar threshold dinâmico (mediana dos candles históricos "saudáveis").

---

## 10. Sprint 6 — R6: Fintz P23 refinado

**Quando executar:** após Sprint 5 fechar **e** Sprint 1 terminar. A fase de scaffold pode iniciar antes (durante backfill). Requer ~4–6h de execução real + scaffolding ~2h.

**Objetivo:** substituir `fill_fintz_gap.ps1` por `scripts/fintz_sync_refinado.py` — resiliente, idempotente, com rate-limiting, retry, graceful shutdown, resume-safe.

**Pré-requisitos:** `gap_map_1m` existente.

**Passos:**

1. Levantar requisitos API Fintz (quota, rate limit, paginação):
   ```powershell
   curl -H "Authorization: Bearer $env:FINTZ_API_KEY" https://api.fintz.com/v1/quotas | Tee-Object Melhorias\fintz_api_levantamento.txt
   ```
   Registrar em `Melhorias/fintz_api_levantamento.md`: requests/min, requests/dia, batch máximo, schema.

2. Criar `scripts/fintz_sync_refinado.py` com estrutura:
   ```python
   """
   fintz_sync_refinado.py — sync resiliente e idempotente de gaps Fintz.

   Substitui fill_fintz_gap.ps1. Fonte de verdade: gap_map_1m.
   """
   from __future__ import annotations
   import argparse, asyncio, logging, os, signal
   from dataclasses import dataclass
   from datetime import date
   from pathlib import Path

   @dataclass
   class FintzSyncConfig:
       max_requests_per_min: int = 60
       batch_size: int = 50
       max_retries: int = 3
       backoff_base_s: float = 5.0
       resume_cursor_path: Path = Path("Melhorias/logs/fintz_sync_cursor.json")

   async def gaps_para_sincronizar(conn) -> list[tuple[str, date]]:
       """SELECT ticker, dia FROM gap_map_1m WHERE NOT tem_fintz AND eh_pregao."""
       ...

   async def sync_ticker_range(ticker: str, d_start: date, d_end: date, config: FintzSyncConfig) -> int:
       """Request Fintz, valida schema, INSERT ... ON CONFLICT (ticker, data) DO UPDATE."""
       ...

   def setup_graceful_shutdown() -> asyncio.Event:
       stop = asyncio.Event()
       loop = asyncio.get_event_loop()
       for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, 'SIGBREAK', signal.SIGTERM)):
           try: loop.add_signal_handler(sig, stop.set)
           except NotImplementedError: pass
       return stop

   def main():
       p = argparse.ArgumentParser()
       p.add_argument('--dry-run', action='store_true')
       p.add_argument('--only', help='CSV de tickers')
       p.add_argument('--all', action='store_true')
       p.add_argument('--resume', action='store_true')
       args = p.parse_args()
       asyncio.run(run(args))

   if __name__ == '__main__':
       main()
   ```

3. Incluir logging estruturado (mesmo padrão de `backfill_historico_watchlist.py`).

4. Teste controlado com 3 tickers:
   ```powershell
   python scripts/fintz_sync_refinado.py --only "BBAS3,VALE3,PETR4" --dry-run
   python scripts/fintz_sync_refinado.py --only "BBAS3,VALE3,PETR4"
   ```
   Esperado dry-run: lista de (ticker, dia) a sincronizar, sem hitting API. Execução: n inserts e zero erros.

5. Sync full resume-safe:
   ```powershell
   python scripts/fintz_sync_refinado.py --all --resume
   ```

6. (Opcional) empacotar em serviço nssm `FinAnalyticsFintzSync` com schedule diário:
   ```powershell
   Copy-Item Melhorias\setup_backfill_service.ps1 Melhorias\setup_fintz_sync_service.ps1
   # Editar ServiceName, DisplayName, ScriptPath — instalar:
   .\setup_fintz_sync_service.ps1 install
   ```

7. Arquivar legado:
   ```powershell
   New-Item -ItemType Directory -Force Melhorias\legado
   Move-Item Melhorias\fill_fintz_gap.ps1 Melhorias\legado\
   ```

**DoD:**
- `count(*) FROM gap_map_1m WHERE NOT tem_fintz AND dia < CURRENT_DATE` ≤ 100 (gaps residuais aceitos para suspensão/ajuste).
- Para tickers VERDE, cobertura Fintz ≥ 99,5% de 2024-01-01 a hoje.
- Script com retry + graceful shutdown + logging estruturado.
- `fill_fintz_gap.ps1` em `legado/`.

**Arquivos tocados:** novo `scripts/fintz_sync_refinado.py`, opcional `Melhorias/setup_fintz_sync_service.ps1`, `Melhorias/fintz_api_levantamento.md`, `Melhorias/legado/fill_fintz_gap.ps1`.

**Riscos:** Fintz pode ter quota diária baixa; script deve persistir cursor e retomar no dia seguinte.

---

## 11. Sprint 7 — R5: Decisão VERMELHO_sem_profit

**Quando executar:** após Sprint 6. Requer ~2h técnico + decisão de negócio (Marcelo).

**Objetivo:** decidir se contratar cobertura Profit adicional para ~80 tickers ou aceitar só-Fintz (EOD).

**Pré-requisitos:** Sprint 6 (Fintz completo; permite comparar trade-off).

**Passos:**

1. Listar VERMELHO com liquidez relevante:
   ```sql
   SELECT ticker, mediana_vol_brl, mediana_trades_dia
     FROM watchlist_tickers
    WHERE status = 'VERMELHO_sem_profit'
    ORDER BY mediana_vol_brl DESC;
   ```

2. Cruzar com universo de interesse (small caps, ETFs, BDRs relevantes).

3. Consulta manual à Nelogica sobre planos que cobrem tickers listados.

4. TCO em planilha `Melhorias/fintz_vs_profit_tco.xlsx`:
   - Custo Profit adicional R$/mês vs valor analítico intraday.
   - Alternativa: Fintz intraday (se existir).

5. Decisão em `Melhorias/decisao_R5_vermelho_sem_profit.md`.

**DoD:**
- Decisão escrita (subscrever N tickers ou aceitar só-Fintz).
- Se subscrever, lista + data esperada de ativação.
- `watchlist_tickers.status` atualizado para `AMARELO_*`/`VERDE` após início da cobertura.

**Arquivos tocados:** `Melhorias/decisao_R5_vermelho_sem_profit.md`, `Melhorias/fintz_vs_profit_tco.xlsx`, `watchlist_tickers`.

**Riscos:** decisão de negócio, não só técnica. Claude Code entrega levantamento; decisão final é do Marcelo.

---

## 12. Sprint 8 — R9: Dashboards de qualidade de dados

**Quando executar:** após Sprints 4 e 5. Pode começar subindo Grafana durante Sprint 1 (painel 2 já funciona). Requer ~3–4h total.

**Objetivo:** MVP de 3 painéis que respondem "a base está saudável hoje?" sem abrir psql.

**Pré-requisitos:** `cobertura_diaria_v2` (Sprint 4) e `gap_map_1m` (Sprint 5) para painéis finais.

**Passos:**

1. Subir Grafana:
   ```powershell
   docker network create finanalytics_net 2>$null
   docker network connect finanalytics_net finanalytics_timescale
   docker run -d --name finanalytics_grafana `
     --network finanalytics_net `
     -p 3000:3000 `
     -v finanalytics_grafana_data:/var/lib/grafana `
     grafana/grafana:latest
   ```

2. Login `admin/admin`, trocar senha, adicionar datasource Postgres:
   - Host: `finanalytics_timescale:5432`
   - DB: `market_data` / user: `finanalytics` / pass: `timescale_secret`
   - TLS: Disabled

3. **Painel 1 — Heatmap cobertura (ticker × dia, 30 dias):**
   ```sql
   SELECT dia AS time, ticker, pct_cob_intraday
     FROM gap_map_1m
    WHERE dia >= now() - interval '30 days' AND mediana_vol_brl > 10000000
    ORDER BY dia, ticker;
   ```

4. **Painel 2 — Latência de ticks (table + threshold):**
   ```sql
   SELECT ticker,
          EXTRACT(EPOCH FROM (now() - max(trade_date)))/3600 AS atraso_horas
     FROM market_history_trades WHERE exchange='B'
    GROUP BY ticker
    ORDER BY atraso_horas DESC
    LIMIT 30;
   ```
   Alert: `atraso > 24h` em horário de pregão.

5. **Painel 3 — Gaps em `ohlc_1m` hoje (bar chart por minuto):**
   ```sql
   WITH m AS (
     SELECT generate_series(date_trunc('day', now()) + interval '10 hours',
                            date_trunc('day', now()) + interval '17 hours',
                            interval '1 minute') AS bucket
   )
   SELECT m.bucket, count(o.ticker) AS tickers_com_candle
     FROM m LEFT JOIN ohlc_1m o ON o.bucket = m.bucket
    GROUP BY m.bucket ORDER BY m.bucket;
   ```

6. Exportar dashboard JSON: Grafana UI → Share → Export → salvar em `Melhorias/grafana_dashboards/qualidade_dados.json`.

**DoD:**
- Grafana em `http://localhost:3000`, datasource Postgres OK.
- 3 painéis populados.
- Dashboard JSON versionado.

**Arquivos tocados:** container `finanalytics_grafana`, rede `finanalytics_net`, `Melhorias/grafana_dashboards/qualidade_dados.json`.

**Riscos:** `--link` deprecated; usar rede dedicada.

---

## 13. Sprint 9 — R8: Decisão de hospedagem

**Quando executar:** após Sprint 1 terminar (precisa de dados de uso reais). Requer ~2h.

**Objetivo:** consolidar TCO entre local (workstation), VM dedicada (AWS/Azure/GCP), e híbrido (dual-GPU on-prem + cloud para dados). Decidir.

**Pré-requisitos:** Sprint 1 completo.

**Passos:**

1. Medir consumo:
   ```sql
   SELECT pg_size_pretty(pg_database_size('market_data')) AS db_tamanho;

   SELECT h.hypertable_name,
          pg_size_pretty(hypertable_size(format('%I.%I', h.hypertable_schema, h.hypertable_name)::regclass)) AS tamanho
     FROM timescaledb_information.hypertables h
    ORDER BY hypertable_size(format('%I.%I', h.hypertable_schema, h.hypertable_name)::regclass) DESC;

   SELECT count(*) AS ticks_30d, pg_size_pretty(count(*) * 80) AS estimativa_30d
     FROM market_history_trades
    WHERE trade_date >= now() - interval '30 days';
   ```

2. Projeção 1y e 5y (considerar compressão Timescale ~10x em dados antigos).

3. Atualizar `FinAnalyticsAI_Comparativo_Hospedagem.xlsx`:
   - Aba "Premissas": storage 1y/5y reais.
   - Aba "Local vs Cloud": TCO recalculado.

4. Revisar `proposta_decisao_15_dualgpu.md` e cruzar com métricas.

5. Decisão em `Melhorias/decisao_R8_hospedagem.md`.

6. Se migrar:
   - Script de backup (`pg_dump` + `docker cp` de `fintz_cotacoes_ts`).
   - Plano de migração (janela 12–24h).
   - Testar restore em ambiente secundário.

**DoD:**
- Decisão escrita com TCO justificado.
- Se local: plano de upgrade de storage (NVMe 4TB sugerido).
- Se migrar: plano de corte + rollback.

**Arquivos tocados:** `FinAnalyticsAI_Comparativo_Hospedagem.xlsx`, `Melhorias/decisao_R8_hospedagem.md`, `Melhorias/metricas_consumo_<data>.md`.

**Riscos:** ProfitDLL é Windows-only; migração cloud obriga Windows VM (custo > Linux) ou híbrido (DLL local, Timescale cloud).

---

## 14. Sprint 10 — R10: Framework de modelos e backtests

**Quando executar:** após Sprint 1 completo **e** Sprint 5 (gap_map limpo). Escopo MVP estrito: 1 ticker, 1 target, 1 modelo. Requer ~6–10h.

**Objetivo:** pipeline end-to-end — features diárias → treino → backtest out-of-sample → serving via API.

**Pré-requisitos:** Sprint 1 (6 anos de dados), Sprint 5 (gap_map_1m), Sprint 8 (dashboards para monitorar modelo em produção, opcional).

**Passos:**

1. Auditar código ML existente:
   ```powershell
   ls D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\application\ml\
   ls D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\interfaces\api\routes\forecast.py
   ls D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\interfaces\api\routes\backtest.py
   ls D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\domain\backtesting\strategies\technical.py
   ```
   Resumo em `Melhorias/auditoria_ml_existente.md`: o que `return_forecaster` já faz, strategies implementadas, rotas API, gaps vs MVP.

2. Schema `features_daily`:
   ```sql
   CREATE TABLE IF NOT EXISTS features_daily (
     ticker     text    NOT NULL,
     dia        date    NOT NULL,
     r_1d numeric, r_5d numeric, r_21d numeric,
     atr_14 numeric, vol_21d numeric,
     vol_rel_20 numeric,
     close numeric, sma_50 numeric, sma_200 numeric, rsi_14 numeric,
     PRIMARY KEY (ticker, dia)
   );
   SELECT create_hypertable('features_daily', 'dia', if_not_exists => true, migrate_data => true);
   ```

3. Job de materialização — `scripts/features_daily_builder.py`:
   ```python
   """
   features_daily_builder.py — popula features_daily incrementalmente a partir de ohlc_1m.

   Uso:
     python scripts/features_daily_builder.py --backfill --start 2020-01-02
     python scripts/features_daily_builder.py --incremental
   """
   # Agrega ohlc_1m → diário, calcula indicadores, INSERT ON CONFLICT DO UPDATE.
   ```
   Backfill inicial:
   ```powershell
   python scripts/features_daily_builder.py --backfill --start 2020-01-02
   ```

4. Padronizar interface de strategy em `domain/backtesting/strategies/`:
   ```python
   # Cada strategy expõe:
   # .generate_signals(df: pl.DataFrame) -> pl.DataFrame[timestamp, ticker, signal]
   # BacktestEngine: sinal × preço × custo × slippage → equity curve
   ```

5. **MVP — pipeline completo:**
   - Ticker: **PETR4**.
   - Target: retorno 1 dia à frente.
   - Features: subconjunto de `features_daily`.
   - Split: 2020–2023 treino, 2024 validação, 2025 teste.
   - Modelo: **LightGBM**.
   - Métricas: IC (information coefficient), hit rate, Sharpe de long-short simples.

6. Serving — endpoint `/predict` em rota nova:
   - Input: `{ticker, dia}`.
   - Output: `{predicted_return, confidence}`.

7. Registrar modelo:
   - MLflow (se infra permitir) ou fallback: pickle em `models/` + metadata JSON.

8. Runbook em `Melhorias/runbook_R10_modelos.md`.

**DoD:**
- `features_daily` populado para 2020-hoje para watchlist VERDE.
- 1 modelo treinado; IC > 0.05 e Sharpe > 0 no teste out-of-sample.
- `/predict` respondendo para 5 tickers de amostra.
- Runbook escrito.

**Arquivos tocados:** `scripts/features_daily_builder.py`, `features_daily` (hypertable), arquivos em `application/ml/` e `interfaces/api/routes/`, `Melhorias/runbook_R10_modelos.md`, `Melhorias/auditoria_ml_existente.md`.

**Riscos:** R10 é o mais aberto. Delimitar escopo MVP rigorosamente antes de codar. Expansão vem em R11+.

**Nota:** a discussão de "Breakout Probability (melhor target intraday)" ficou parqueada pelo usuário — candidata a R11 após R10.

---

## 15. Smoke tests (rotina)

Rodar a cada fechamento de sprint e, durante Sprint 1, ao menos 1×/dia:

```powershell
# 1. profit_agent vivo
Invoke-RestMethod http://localhost:8002/status

# 2. Backfill
nssm status FinAnalyticsBackfill
Get-Content D:\Investimentos\FinAnalytics_AI\Melhorias\logs\backfill_historico_stdout.log -Tail 10

# 3. Banco
docker exec -i finanalytics_timescale psql -U finanalytics -d market_data -c "SELECT 1;"
docker exec -i finanalytics_timescale psql -U finanalytics -d market_data -c "SELECT status, count(*) FROM watchlist_tickers GROUP BY status;"

# 4. Volume do último pregão
docker exec -i finanalytics_timescale psql -U finanalytics -d market_data -c @"
SELECT count(*) FROM market_history_trades
 WHERE trade_date::date = CURRENT_DATE - interval '1 day' * (CASE WHEN extract(dow from now()) = 1 THEN 3 ELSE 1 END);
"@

# 5. Grafana (após Sprint 8)
Invoke-RestMethod http://localhost:3000/api/health

# 6. Artefatos recentes
docker exec -i finanalytics_timescale psql -U finanalytics -d market_data -c "\d gap_map_1m"
docker exec -i finanalytics_timescale psql -U finanalytics -d market_data -c "\d calendario_b3"
docker exec -i finanalytics_timescale psql -U finanalytics -d market_data -c "\d cobertura_diaria_v2"
docker exec -i finanalytics_timescale psql -U finanalytics -d market_data -c "\d ticker_stride"
docker exec -i finanalytics_timescale psql -U finanalytics -d market_data -c "\d features_daily"
```

---

## 16. Cenários de recuperação

### 16.1 Profit.exe travou / DLL sem connect
1. Reiniciar Profit.exe manualmente; logar.
2. Validar `/status` em `localhost:8002` — deve voltar a `market_connected: true`.
3. Se serviço estiver stopped, restart:
   ```powershell
   D:\Investimentos\FinAnalytics_AI\Melhorias\setup_backfill_service.ps1 restart
   ```

### 16.2 Backfill crashando em loop
Sintoma: `nssm status` alterna `SERVICE_RUNNING` / `SERVICE_STOPPED` rapidamente.

1. Verificar log:
   ```powershell
   Get-Content D:\Investimentos\FinAnalytics_AI\Melhorias\logs\backfill_historico_stderr.log -Tail 50
   ```
2. Diagnosticar: se HTTP 500 do profit_agent → DLL timeout em ticker muito volumoso. Aumentar delay:
   ```powershell
   nssm edit FinAnalyticsBackfill
   # Na aba Arguments, acrescentar: --delay 4
   .\setup_backfill_service.ps1 restart
   ```

### 16.3 Refresh contínuo travado
```sql
SELECT pid, state, query_start, query
  FROM pg_stat_activity
 WHERE query LIKE '%refresh_continuous_aggregate%';

SELECT pg_cancel_backend(<pid>);
```
Restaurar do backup se corrompeu:
```sql
INSERT INTO ohlc_1m
SELECT * FROM ohlc_1m_backup_20260417
ON CONFLICT DO NOTHING;
```

### 16.4 Grafana perdeu config
```powershell
docker stop finanalytics_grafana; docker rm finanalytics_grafana
# Re-rodar Sprint 8 passo 1; importar JSON de Melhorias\grafana_dashboards\
```

### 16.5 Fintz API esgotou quota
Cursor em `Melhorias/logs/fintz_sync_cursor.json` deve estar persistido. Retomar no dia seguinte:
```powershell
python scripts/fintz_sync_refinado.py --all --resume
```

### 16.6 `features_daily` corrompido
```sql
TRUNCATE features_daily;
```
Re-rodar:
```powershell
python scripts/features_daily_builder.py --backfill --start 2020-01-02
```

---

## 17. Entregáveis esperados ao final de R1–R10

| # | Entregável | Sprint | Localização |
|---|------------|--------|-------------|
| 1 | `market_history_trades` populado 2020-hoje | S1 | banco |
| 2 | BBDC3/BPAN4/GUAR3 resolvidos | S2 | `watchlist_tickers`, `ESTADO_CONSOLIDADO.md` |
| 3 | `ohlc_1m` consistente (incl. 13-16/abr) | S3 | banco |
| 4 | `ticker_stride`, `cobertura_diaria_v2` | S4 | banco |
| 5 | `calendario_b3`, `gap_map_1m` | S5 | banco + `Melhorias/sql/` |
| 6 | `scripts/fintz_sync_refinado.py` + serviço nssm | S6 | repo + Windows services |
| 7 | `Melhorias/decisao_R5_vermelho_sem_profit.md` | S7 | `Melhorias/` |
| 8 | Grafana com 3 painéis | S8 | `localhost:3000` + `Melhorias/grafana_dashboards/` |
| 9 | `Melhorias/decisao_R8_hospedagem.md` + xlsx atualizado | S9 | `Melhorias/` |
| 10 | `features_daily`, modelo MVP, `/predict`, runbook | S10 | banco + repo + `Melhorias/` |

---

## 18. Referências cruzadas

- **Contexto técnico completo:** `ESTADO_TECNICO.md` (topologia, schemas, API, patches 17/abr).
- **Estado executivo:** `ESTADO_CONSOLIDADO.md` (visão de alto nível + changelog R1–R10).
- **Catálogo day trade:** `CATALOGO_DAYTRADE_ROBOS.md` (25 técnicas para robôs signal-only — fase 1 pós-R10).
- **Top 500 líquidos:** `B3_500_LIQUIDOS.md` + `list_top300_liquidos.py` (referência para watchlist e signal-only).
- **Serviço backfill:** `setup_backfill_service.ps1` (gerenciamento nssm do Sprint 1).

---

## 19. Changelog

- **18/abr/2026 — v1**: documento consolidado único. Substitui `SPRINTS_CLAUDE_CODE.md` e `SPRINTS_PARALELO_BACKFILL.md`. Cobre R1–R10 com estado atual, regras operacionais, fluxo completo (sprints 0–10), smoke tests, recuperação, entregáveis.
