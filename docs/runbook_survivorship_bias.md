# Survivorship Bias — Runbook (R5 fechado)

> Status (02/mai/2026): **Steps 0, 1 e 2 done**. R5 totalmente fechado.
>
> Histórico: Step 0 (01/mai) coletou 1863 candidatos CVM com placeholder `UNK_<cnpj>`. Step 1 (02/mai) pivotou de "PDFs IBOV" para **bridge via Fintz delta** — caminho mais barato e com cobertura superior (449 tickers reais vs ~150 estimados via PDF). Tickers com `last_date` Fintz < 2024-01-01 (404 high-conf) ou em 2024-01-01..2025-05-31 (45 borderline) e ausentes de `profit_subscribed_tickers` são candidatos legítimos a delisted. Validação manual: ENBR3, BRPR3, ALSO3, VIIA3, BOAS3, CIEL3, AESB3, RRRP3, TRPL4 — todos delistings/fusões/OPAs reais documentados.

## Por que importa

R5 backtest harness hoje opera só sobre tickers com dados em `fintz_cotacoes_ts` ou `ohlc_1m`. **Empresas que saíram da B3** (delisting voluntário, cancelamento de registro CVM, OPA, fusão, falência) **não aparecem no universo**. Backtest sobre IBOV histórico inclui implicitamente o viés "sobreviventes" — *survivorship bias positivo*.

DSR + walk-forward NÃO corrigem isso. É necessário coletar listagem histórica de delistados e:
- INCLUIR no universo de backtest com bars truncados na `delisting_date`
- Force-close posições a `last_known_price` + 0% slippage adicional (não dá pra vender após delisting)
- SKIP em signals_history backfill (não se pode tradear quem saiu)

## Step 0 — Coleta inicial (DONE 01/mai)

### Artefatos criados

| Arquivo | Conteúdo |
|---------|----------|
| `alembic/versions/0025_b3_delisted_tickers.py` | Schema Postgres (`ticker, cnpj, razao_social, delisting_date, delisting_reason, source, notes`). CHECK constraints + índices. |
| `scripts/survivorship_collect_cvm.py` | Fetcher do CVM `cad_cia_aberta.csv` + parser + UPSERT em `b3_delisted_tickers`. |

### Como rodar

```powershell
# Aplicar migration (Alembic 2 heads — usar revision específica):
docker exec finanalytics_postgres alembic upgrade 0025_b3_delisted_tickers

# Dry-run pra ver candidatos:
.venv\Scripts\python.exe scripts\survivorship_collect_cvm.py --dry --limit 100

# Persistir 1903 canceladas (com ticker placeholder UNK_<cnpj>):
.venv\Scripts\python.exe scripts\survivorship_collect_cvm.py --persist
```

### Resultado validado live

CSV CVM (`http://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv`, latin-1, 1.5MB) parseou:
- **1903 companhias com `SIT=CANCELADA`** — universo de candidatas a delisting
- Inclui empresas conhecidas: ABRIL SA, AES ELPA, ABYARA PLANEJAMENTO IMOBILIÁRIO, AÇOS VILLARES, etc.
- **Limitação CVM**: maioria das rows tem `DT_CANCEL_REG` vazio. CVM não preenche consistentemente — só temos a confirmação de cancelamento, não a data exata.

## Step 1 — Bridge via Fintz delta (DONE 02/mai)

### Caminho escolhido

Em vez de scraping de PDFs B3 (caminho original do runbook, ~1d), pivotou-se para **cruzar `fintz_cotacoes_ts` com `profit_subscribed_tickers`**:

- Universo Fintz: 884 tickers únicos cobrindo 2010→2025-12-30
- Filtro de confiança:
  - HIGH (404 tickers): `MAX(time)` Fintz < 2024-01-01
  - BORDERLINE (45 tickers): 2024-01-01 ≤ `MAX(time)` < 2025-06-01
  - DESCARTAR: `MAX(time)` ≥ 2025-06-01 — artefato do dataset Fintz (cutoff de freeze ~03/11/2025; ITUB3, ELET3, CSNA3 estão nessa janela mas são vivos)
- Anti-falso-positivo: `WHERE ticker NOT IN (SELECT ticker FROM profit_subscribed_tickers)` — descarta tickers que ainda têm subscribed ativo

### Artefatos

| Arquivo | Conteúdo |
|---------|----------|
| `scripts/survivorship_collect_fintz_delta.py` | Coleta + UPSERT 449 tickers FINTZ com `(ticker, last_known_date, last_known_price, source='FINTZ', notes='high_confidence' \| 'borderline_validar')` |

### Como rodar

```powershell
$env:DATABASE_URL_SYNC = "postgresql://finanalytics:secret@localhost:5432/finanalytics"
$env:TIMESCALE_DSN_SYNC = "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
.venv\Scripts\python.exe scripts\survivorship_collect_fintz_delta.py --dry
.venv\Scripts\python.exe scripts\survivorship_collect_fintz_delta.py --persist
```

### Resultado validado live

449 tickers persistidos com source=FINTZ. Validação manual de 10 amostras (ENBR3, BRPR3, ALSO3, VIIA3, BOAS3, SLED3, CIEL3, AESB3, RRRP3, TRPL4) — todos delistings/fusões/OPAs reais documentados em fontes públicas.

### Cobertura complementar (defer)

- **CVM placeholders (1863)** continuam no DB com `ticker LIKE 'UNK_%'`. Bridge CNPJ→ticker p/ resolvê-los seria útil mas baixa prioridade — Fintz já cobre o universo de interesse pro R5 (tickers que tinham OHLCV histórico).
- **B3 RWS** (XML autenticado) ou **PDFs IBOV trimestrais** ficam como reservas se aparecer cobertura faltante (ex: ticker pre-2010 não em Fintz mas conhecido por outras fontes).

## Step 2 — Integração com R5 (DONE 02/mai)

### Caminho efetivamente implementado

O plano original previa um `get_universe_for_backtest` central — não existe esse helper na arquitetura real (tickers vêm de chamadores externos). Estrutura entregue:

1. **Repo** `src/finanalytics_ai/infrastructure/database/repositories/delisted_tickers_repo.py`:
   - `DelistedTickerModel` (SQLAlchemy)
   - `DelistingInfo` DTO + `is_high_confidence` (`source='FINTZ'`)
   - `get_delisting_info(session, ticker)` — lookup, skip placeholders `UNK_*`
   - `list_delisted_in_range(session, start, end, only_high_confidence=False)` — universo expandido

2. **Engine** `src/finanalytics_ai/domain/backtesting/engine.py:run_backtest`:
   - Novos params `delisting_date: date | None`, `last_known_price: float | None`
   - Lógica: bars com `bar_date >= delisting_date` truncam loop; posição aberta força fechamento com `last_known_price` (ou close da bar se None) e `exit_reason="DELISTED em <date>"`
   - **Compat retro**: sem `delisting_date`, comportamento legacy preservado (validado por test).

3. **Optimizer** `src/finanalytics_ai/domain/backtesting/optimizer.py:grid_search`:
   - Aceita `delisting_date` + `last_known_price` e propaga ao `run_backtest`.

4. **Service** `src/finanalytics_ai/application/services/backtest_service.py:BacktestService`:
   - Construtor aceita `delisting_resolver: Callable[[str], Awaitable[DelistingInfo | None]] | None`
   - Em `run()`, se resolver passado, busca info do ticker e propaga ao engine
   - Fail-open: exception no resolver é logada, não interrompe execução

5. **Demo** `scripts/backtest_demo_dsr.py`:
   - Flag `--respect-delisting` consulta `b3_delisted_tickers` via psycopg2 sync e passa `(delisting_date, last_known_price)` ao `grid_search`.

### Tests (19 novos)

| Arquivo | Tests |
|---------|-------|
| `tests/unit/infrastructure/test_delisted_tickers_repo.py` | 10 (lookup, range, UNK skip, case insensitive, high-confidence filter) |
| `tests/unit/domain/test_backtesting.py::TestEngineSurvivorshipBias` | 5 (legacy compat, force-close c/ last_known, truncamento, sem posição, sem last_known) |
| `tests/unit/domain/test_backtesting.py::TestBacktestService` | 4 novos (resolver passa info, sem resolver, resolver=None, resolver raise) |

Resultado: **1488 testes domain+infrastructure+application verdes**, 3 skipped. Sem regressão.

### Smoke validado live

```
$env:DATABASE_URL_SYNC = "postgresql://finanalytics:secret@localhost:5432/finanalytics"
.venv\Scripts\python.exe scripts\backtest_demo_dsr.py --ticker ENBR3 --strategy rsi `
    --start 2018-01-01 --end 2023-12-31 --respect-delisting
# → "delisting: ENBR3 delistou em 2023-08-21 (last_close=24.08)"
# → grid search: 22 trials válidos / 1399 bars
# → DSR z=0.09 prob=53.6% (SINAL FRACO esperado p/ RSI single-ticker)
```

## Próximos passos imediatos

- [x] Migration 0025 ativa em DB live (criada manualmente em 02/mai porque `alembic_version` estava em 0025 mas tabela ausente — `stamp` sem `upgrade` anterior).
- [x] `survivorship_collect_cvm.py --persist` rodado: 1863 CNPJs únicos com placeholder UNK_*.
- [x] Step 1 Fintz delta done — 449 tickers reais persistidos.
- [x] **Step 2** done — engine respeita delisting_date, BacktestService injeta resolver opcional, demo `--respect-delisting` validado live com ENBR3.
- [x] **Validação anti-bias** rodada 03/mai (`backtest_runs/bias_validation/REPORT.md`). 6 tickers delisted × 2 variantes (com/sem fix) → diferença ≤ 0.001 em DSR (ruído numérico). **Achado**: bars Fintz cessam naturalmente na delisting_date, então force-close é defensivo (correto conceitualmente, no-op funcional com infra atual). **Survivorship bias real do projeto está no universo de seleção** (delisted ausentes da watchlist), não na granularidade do close. Mitigação completa requer backtest multi-ticker em 2 universos (sobreviventes vs sobreviventes+delisted) — defer.
- [ ] (defer) Bridge CNPJ→ticker p/ resolver os 1863 placeholders CVM. Não é caminho crítico — Fintz delta já cobre o universo de interesse para R5.
- [ ] (defer) Multi-ticker grid search universe-comparison (a) só ativos hoje vs (b) ativos + 449 delistados. Custo ~3-5h. Quantifica bias real do projeto.

## Validação anti-bias (uma vez fechado step 2)

Rodar backtest comparativo:
- Dataset A: só ativos atuais (com survivorship bias).
- Dataset B: ativos atuais + delistados no período.
- Métrica: diferença de Sharpe.

Se diferença Sharpe > 0.3, evidência empírica de bias significativo. R5 reports devem **sempre** rodar com Dataset B (incluir delistados).
