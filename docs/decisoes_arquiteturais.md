# Decisões Arquiteturais (Imutáveis)

> Não revogar sem evidência empírica nova. Detalhamento histórico de cada decisão (origem, justificativa, aplicação) em `git log` dos commits que as introduziram.

## Decisão 15 — Dual-GPU: separação estrita

**Regras vinculantes:**
1. Compute ML executa **exclusivamente na GPU 0** (bus `01:00.0`, headless).
2. GPU 1 reservada ao Windows/desktop. **Nunca** recebe workload de compute em produção.
3. Service Docker que precisa de GPU declara `deploy.resources.reservations.devices` com `device_ids: ["0"]` + `capabilities: [gpu, utility, compute]`. `CUDA_VISIBLE_DEVICES: "0"` por redundância.
4. **Proibido**: paralelismo puro multi-GPU (Modo 3 — DDP, `device_map="auto"`, DataParallel) com a PSU atual.
5. **Modo 2 autorizado**: workloads ML *distintos* por GPU APENAS para jobs offline com `nvidia-smi -pl 320` ativo em ambas. Nunca em horário de pregão.
6. Se cabos físicos forem remanejados, validar mapeamento via comando da seção Hardware antes de subir container com compute.
7. Para liberar Modo 3: PSU ≥1.600W ATX 3.0/3.1 Titanium com 2 cabos 12V-2×6 nativos, OU colocation. PSU atual (Corsair HX1500i 1500W Platinum) NÃO atende.

## Decisão 16 — Helper-driven UI

**Regras vinculantes:**
1. Toda página HTML privada deve carregar pelo menos: `auth_guard.js`, `sidebar.js`, `theme.css`, `theme_toggle.js`, `i18n.js`, `error_handler.js`, `toast.js`.
2. Novo asset compartilhado segue pattern IIFE expondo `window.FAXxx`, com `ensureStyles()` auto-injetado e idempotente. Ver `STATIC_HELPERS.md`.
3. **Distribuição em massa**: tocar N páginas → script Python idempotente em `scripts/refactor_*.py`. Edição manual em >5 páginas sinaliza que falta script.
4. **Anchor pattern**: novos `<script>` tags via `replace(ANCHOR, ANCHOR + '\n  ' + TAG)` em scripts já existentes (estável: `sidebar.js`, `auth_guard.js`, `error_handler.js`).
5. Não substituir `confirm()`/`alert()` nativos por implementações próprias página a página — usar `FAModal.confirm` / `FAToast.*`.
6. `data-fa-table` no `<table>` é o padrão para sort/filter automático (FATable auto-init).

## Decisão 17 — FOUC prevention para light theme
Snippet inline no `<head>` ANTES do `<link rel="stylesheet" href="/static/theme.css">` em todas as páginas:
```html
<script>(function(){try{var t=localStorage.getItem('fa_theme');
  if(t==='light'||t==='dark')document.documentElement.dataset.theme=t;}catch(e){}})();</script>
```

## Decisão 18 — i18n por fall-through (PT default + EN fallback)
`FAI18n.t(key)` resolve `_dict[locale][key]` e cai para `_dict['pt'][key]` se ausente. Chave inexistente em ambos retorna a própria key. PT é canônico; EN é tradução. Não migrar texto in-page de uma vez — usar `data-i18n="key"` em elementos novos.

## Decisão 19 — `:root{...}` per-page é identidade visual intencional
Blocos `:root{...}` em páginas individuais NÃO são duplicatas dos globals de `theme.css`. Várias páginas têm identidade visual própria. **Não migrar** automaticamente para vars globais — quebraria visual identity.

## Decisão 20 — BRAPI é último fallback; DLL Profit + DB são primários
Ordem em `CompositeMarketDataClient.get_ohlc_bars` (`infrastructure/adapters/market_data_client.py`):
1. **DB local** (candle_repository — fallback chain interno acima)
2. **Yahoo Finance**
3. **BRAPI** — último recurso

Ordem em `get_quote` (live): profit_agent `:8002` → Yahoo → BRAPI.

**Regras vinculantes:**
1. **Não chamar `BrapiClient` direto** nos routes. Usar `request.app.state.market_client` (Composite).
2. **Exceção única**: fundamentalistas (P/L, ROE, DY) continuam via BRAPI — DLL não fornece.
3. `MIN_BARS_THRESHOLD = 30` — DB com < 30 bars cai pro Yahoo.
4. `YAHOO_PREFERRED_RANGES = {"10y", "max"}` — ranges longos vão direto pro Yahoo.
5. **Ingestor `ohlc_1m_ingestor` continua usando BRAPI** para alimentar DB. Não viola a Decisão.

## Decisão 21 — `populate_daily_bars` default `1m` (ticks tem bug de escala)

**Regras vinculantes:**
1. `populate_daily_bars.py` default `auto` tenta `ohlc_1m` primeiro, fallback para ticks.
2. **Não usar `--source ticks` em produção** para tickers com `ohlc_1m` disponível.
3. **Exceção**: futuros (`WDOFUT`, `WINFUT`) sem `ohlc_1m` continuam usando ticks.
4. Se voltar a aparecer escala mista, regenerar via `populate_daily_bars.py --ticker $T --source 1m` após `DELETE FROM profit_daily_bars WHERE ticker=$T`. Não tentar "patch in place".

Runbook detalhado: `runbook_profit_daily_bars_scale.md`.

## Decisão 22 — Docker runtime: Engine direto em WSL2 (não Docker Desktop)

**Regras vinculantes:**
1. **Runtime canônico**: Docker Engine 29.4.2 dentro de Ubuntu-22.04 WSL2 (`systemctl is-active docker` = active). Volumes Postgres+Timescale em **ext4 nativo** (`/home/abi/finanalytics/data/{postgres,timescale}/`, 10-50x perf vs NTFS+9P, Fase B.2 done 01/mai). Demais volumes (`prometheus`, `grafana`, `pgadmin`, etc.) ainda em `/mnt/e/finanalytics_data/` — não foram migrados pq não são caminho crítico de IO.
2. **PowerShell**: `docker context use wsl-engine` apontando pra `tcp://127.0.0.1:2375`. **Docker Desktop autostart desativado em 01/mai** — abrir manualmente quando precisar do `default` context.
3. **profit_agent bind 0.0.0.0:8002** desde 01/mai (era 127.0.0.1) — Engine WSL2 puro precisa pra alcançar via WSL gateway. Override via env `PROFIT_AGENT_BIND` se quiser restringir.
4. **`docker-compose.wsl.yml` é OBRIGATÓRIO** ao subir a stack — converte paths NTFS `E:/` pra `/mnt/e/`, mapeia `host.docker.internal:172.17.80.1` (não `:host-gateway` — esse resolve pra docker bridge interna em Engine WSL2 puro).
5. **Firewall Windows** tem regra `Profit Agent WSL Inbound` permitindo TCP 8002 da subnet `172.17.80.0/20`. Se WSL gateway IP mudar (após `wsl --shutdown` ou reboot), atualizar regra **e** o `docker-compose.wsl.yml`.
6. **Smoke test após qualquer mudança de stack**:
   ```powershell
   docker context show  # wsl-engine
   docker ps  # 17 containers
   curl http://localhost:8000/api/v1/agent/health  # {"ok":true}
   ```
7. **Imagens stale**: rebuilds via `docker compose build api worker` (~5min com cache). NÃO usar `--no-cache` casual — pode falhar transient em pip install torch+prophet (2GB re-download).

Runbook completo: `runbook_wsl2_engine_setup.md`. Histórico de fases I1 em `historico/sessoes_29abr_01mai.md`.

## Decisão 23 — Alembic + Timescale: ts_* são registry-only

**Regras vinculantes:**
1. Migrations `0xxx_*.py` (ramo Postgres principal) — DDL real contra Postgres `finanalytics` DB. Aplicar via `alembic upgrade <revision>`.
2. Migrations `ts_xxxx_*.py` (ramo Timescale) — **registry-only**: o `upgrade()` deveria ser `pass` ou comentado (DDL escrita ali roda contra Postgres por bug arquitetural). Schema real Timescale vai em `init_timescale/00X_*.sql` aplicado manualmente.
3. Após criar nova migration `0xxx_*`, **sempre validar tabela física existe pós-upgrade**: `docker exec finanalytics_postgres psql -c "\dt <table>"`. Detecta caso `alembic stamp` em vez de `upgrade` (precedente real em 02/mai com 0025).
4. Timescale **não tem `alembic_version`** — controle de versão é só no Postgres. Aceitável até ramo `ts_*` exigir 2-context env.py (defer enquanto < 5 migrations Timescale).

Runbook completo: `runbook_alembic_audit.md`.

## Decisão 24 — UNION cross-source p/ daily bars (Fintz freeze defense)

**Regras vinculantes:**
1. Pipelines que precisam de daily bars **recentes** (qualquer lookback que ultrapasse 2025-11-03) DEVEM fazer UNION ALL com prio:
   - `prio=1`: `profit_daily_bars` (DLL pre-aggregated, mais recentes)
   - `prio=2`: `ohlc_1m` daily aggregate (BRAPI ingestor + tick agg)
   - `prio=3`: `fintz_cotacoes_ts` (histórico longo 2010 → 2025-11-03)
   Dedup via `DISTINCT ON (date) ORDER BY date ASC, prio ASC` — primeira fonte ganha por data.
2. Endpoint canônico: `/api/v1/marketdata/candles_daily/{ticker}?n=N` (`marketdata.py:get_candles_daily`). Frontend e workers consomem este, NÃO `/candles` (que é 5m default p/ chart UI).
3. Strategies/services que precisam closes daily usam `HttpCandleFetcher.fetch_daily_closes(ticker, n)` (wrapper de `fetch_daily_bars`). NUNCA usar `fetch_closes` (5m intraday) p/ z-score history ou lookback longo — só pra "preço current" (n=1).
4. Scripts/jobs que leem fintz direto (cointegration_screen, features_daily_builder) DEVEM aplicar o mesmo SQL UNION (CTE pattern com prio + DISTINCT ON). Lookback 24-36mo na cláusula WHERE de cada source.
5. **Anti-trap**: bug original do `range_period` no `/candles` ignorava o param silenciosamente (sem 422 ou warning). Sempre validar empiricamente: `curl /endpoint?param=x | jq '.candles[0].ts'` confere se time é daily ou intraday. Test de integração que verifica `ts` do primeiro/último bar contra range esperado pega esse tipo de regressão.

Runbook: `runbook_survivorship_bias.md` (R5 também usou esse pattern via candle_repository fallback chain).
