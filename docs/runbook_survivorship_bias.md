# Survivorship Bias — Runbook (R5 último item aberto)

> Status (01/mai/2026): **Step 0 done**. Step 1 (CNPJ→ticker bridge) defer — bloqueado por dependência manual ou scraping B3.

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

## Step 1 — CNPJ→ticker bridge (DEFER)

A CVM identifica companhias por CNPJ, **não por ticker B3**. Sem esse bridge, `b3_delisted_tickers.ticker` fica em placeholder `UNK_<cnpj_short>` e R5 não consegue cruzar com `fintz_cotacoes_ts`.

### Fontes possíveis (escolher uma)

1. **B3 RWS Service Center** (oficial)
   - XML com cadastro completo de instrumentos vinculados por CNPJ.
   - Requer scraping autenticado em https://www.b3.com.br/
   - **Custo**: ~1-2d engineering pra montar scraper resiliente

2. **Wikipedia + fundamentus.com.br** (manual-assistido)
   - Wikipedia mantém tabela "Empresas com ações na B3" + alguns artigos com histórico.
   - Fundamentus tem ticker → CNPJ pra ativos.
   - **Custo**: ~3-5d trabalho semi-manual + dataset incompleto

3. **Lista IBOV histórica B3** (parcial)
   - B3 publica revisões trimestrais da carteira IBOV (PDFs em www.b3.com.br/lumis/portal/file/fileDownload).
   - Cobre só o que entrou no IBOV — ações líquidas mas exclui small caps.
   - **Custo**: ~1d parser PDF + cobertura ~150 tickers históricos

4. **dataset comercial** (Economatica, Refinitiv)
   - Tem `ticker, cnpj, delisting_date, motivo` consolidado.
   - **Custo**: licença R$ 5-10k/mês — fora do orçamento

### Recomendação operacional

**Híbrido**: começar com (3) parser PDF da carteira IBOV histórica (~150 tickers que entraram/saíram do IBOV desde 2010), depois enriquecer com (1) scraper B3 RWS pra small caps. Dataset comercial só se R5 mostrar valor mensurável e backer aceitar custo.

## Step 2 — Integração com R5 (DEFER)

Após step 1 completo, modificar:

1. `src/finanalytics_ai/infrastructure/database/repositories/candle_repository.py`
   ```python
   def get_universe_for_backtest(self, date_inicial: date, date_final: date) -> list[str]:
       # tickers ativos
       active = self._fetch_active_tickers()
       # tickers que delistaram NO INTERVALO do backtest (incluir!)
       delisted_in_range = self._fetch_delisted_between(date_inicial, date_final)
       return active + delisted_in_range
   ```

2. `src/finanalytics_ai/domain/backtesting/engine.py`
   ```python
   def run_backtest(..., delisting_date: date | None = None):
       # se posição aberta na delisting_date: force close at last_known_price
       if delisting_date and current_bar_time.date() >= delisting_date:
           if position.is_open:
               position.close(last_known_price, reason="DELISTED")
   ```

3. `scripts/backtest_demo_dsr.py`
   - Add flag `--include-delisted` (default `False` mantém compat).
   - Quando `True`, gera DSR comparativo: com/sem survivorship bias.

## Próximos passos imediatos

- [ ] Ativar a migration 0025 em DB live (`alembic upgrade 0025_b3_delisted_tickers`) — bloqueado pela complexidade dos 2 heads do Alembic.
- [ ] Rodar `survivorship_collect_cvm.py --persist` pra popular 1903 candidatos com placeholder.
- [ ] Decidir caminho do step 1 (B3 RWS vs PDF IBOV vs híbrido).
- [ ] Smoke depois da Segunda 04/mai — não bloquear pré-pregão.

## Validação anti-bias (uma vez fechado step 2)

Rodar backtest comparativo:
- Dataset A: só ativos atuais (com survivorship bias).
- Dataset B: ativos atuais + delistados no período.
- Métrica: diferença de Sharpe.

Se diferença Sharpe > 0.3, evidência empírica de bias significativo. R5 reports devem **sempre** rodar com Dataset B (incluir delistados).
