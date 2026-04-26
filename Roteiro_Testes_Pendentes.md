# Roteiro de Testes Pendentes — FinAnalytics AI

> **Reorganizado**: 26/abr/2026 — passos numerados em ordem de execução
> **Login dev**: `marceloabisquarisi@gmail.com` / `admin123` (master)
> **DB seedado** (commit `7555662`) — 2 contas teste populadas: "Teste Ações XP" + "Teste Renda Fixa BTG"
> **Cache**: SW v43 — `Ctrl+Shift+R` na 1ª abertura de cada página

## Resumo executivo

| Bloco | Quando | Conteúdo |
|---|---|---|
| **Passos 0-9** (Bloco A — sem pregão) | agora, ~1h45 | Smoke + features novas (UI, dados seed) |
| **Passo 10** (pré-pregão) | hoje à tarde ou domingo | restart profit_agent, validar rotas /oco/* |
| **Passos 11-15** (Bloco B — pregão) | segunda 27/abr 10h-18h BRT | DLL viva, ordens reais, OCO end-to-end |
| **Bloqueados** (C-F) | quando contexto disponível | Pushover, sudo presencial, samples, Nelogica |

---

## 🟢 BLOCO A — Sem pregão (executar agora, ordem sequencial)

### PASSO 0 — Pré-flight (~5min)

> Garantir que tudo está respondendo antes de testar.

- [ ] **0.1** Containers up:
  ```bash
  docker ps --filter name=finanalytics --format "{{.Names}}: {{.Status}}"
  ```
  Esperado: `finanalytics_api`, `finanalytics_postgres`, `finanalytics_timescale`, `finanalytics_redis`, `finanalytics_kafka`, `finanalytics_grafana`, `finanalytics_prometheus`, scheduler/workers todos `Up`.
- [ ] **0.2** Health endpoints:
  ```bash
  curl -s http://localhost:8000/health
  curl -s http://localhost:8002/health
  ```
  Esperado: `{"ok":true,...}` em ambos.
- [ ] **0.3** Login devolve token:
  ```bash
  TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"marceloabisquarisi@gmail.com","password":"admin123"}' \
    | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
  echo $TOKEN | head -c 50
  ```
  Esperado: JWT base64 começando com `eyJ...`.

### PASSO 1 — Smoke das 14 páginas privadas (~5min)

> 200 em todas confirma que G4 (auth refactor) e i18n não quebraram nada.

- [ ] **1.1**
  ```bash
  for r in /dashboard /carteira /movimentacoes /alerts /import /screener /watchlist \
           /admin /hub /performance /diario /fundamental /forecast /macro; do
    code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000${r}")
    echo "${r}: ${code}"
  done
  ```
  Esperado: todas `200`.

### PASSO 2 — /carteira filtro de conta (~10min)

> Valida o filtro novo introduzido em commits `eaf77fb` + `9ac1bf8`.

- [ ] **2.1** Abrir http://localhost:8000/carteira (`Ctrl+Shift+R` se 1ª vez)
- [ ] **2.2** Selector "Conta" no topo mostra **3 opções**: Todas as contas / Teste Ações XP (XPI) / Teste Renda Fixa BTG (BTG Pactual)
- [ ] **2.3** DevTools (F12) console — deve aparecer `[carteira] acc-filter populado com 2 contas`
- [ ] **2.4** Selecionar **Teste Ações XP** → info inline aparece `caixa: R$ 50.000,00`
- [ ] **2.5** Selecionar **Teste Renda Fixa BTG** → `caixa: R$ 30.000,00`
- [ ] **2.6** F5 mantém seleção (localStorage `fa_carteira_account_id`)

### PASSO 3 — /carteira tabs com seed (~25min)

> 7 tabs com dados reais do seed.

**Tab Overview (1ª, default ativa)**:
- [ ] **3.1** Iframe carrega `/overview` dentro da tab — vê cards das posições (BBSE3, ITUB4, KNRI11, PETR4, VALE3, WEGE3, BOVA11, BBAS3 — 8 cards considerando ambas contas)
- [ ] **3.2** Sparklines, P/L, badge ML (após scheduler 18:30 BRT — pode ficar `—` agora)
- [ ] **3.3** Filtro "Apenas BUY" reduz cards
- [ ] **3.4** Seção "Últimas movimentações" no rodapé do iframe — 5 tx (das 20 do seed)

**Tab Contas**:
- [ ] **3.5** Lista as 2 contas com institution_name + apelido em **2 linhas** (BUG15 fix: apelido bold + small institution_name embaixo)

**Tab Posições** (com filtro = "Todas"):
- [ ] **3.6** 8 linhas (PETR4 70 net, VALE3, ITUB4, WEGE3, BBSE3, KNRI11, BBAS3, BOVA11)
- [ ] **3.7** Colunas: Ticker, Classe, Qtd, Preço Médio, **Atual**, **P/L**, **SL**, Total Investido, Trades
- [ ] **3.8** "Atual" carrega via `/marketdata/candles` (placeholder `—` substituído ao chegar)
- [ ] **3.9** P/L formatado verde/vermelho com pct embaixo
- [ ] **3.10** Mudar filtro pra "Teste Ações XP" → 6 linhas (sem BBAS3/BOVA11)

**Tab Trades**:
- [ ] **3.11** Filtro "Todas" → 9 trades; filtro "BTG" → 2 trades (BBAS3 + BOVA11); filtro "XP" → 7 trades
- [ ] **3.12** Coluna "Conta" mostra apelido bold + institution small (BUG15 fix)

**Tab Cripto**:
- [ ] **3.13** Filtro "Todas" ou "XP" → 1 linha BTC com qty 0.025, avg R$ 280.000,00
- [ ] **3.14** Colunas Atual + P/L (pode ficar `—` se símbolo BTC sem candles)
- [ ] **3.15** Botão 💰 (resgate) abre prompt

**Tab Renda Fixa**:
- [ ] **3.16** Filtro "Todas" ou "BTG" → 3 títulos (CDB BTG, LCI BTG, Tesouro IPCA+)
- [ ] **3.17** Colunas Nome, Tipo, Emissor, Taxa, Vencimento, Investido, IR, Conta

**Tab Outros**:
- [ ] **3.18** Filtro "Todas" ou "XP" → 1 linha "Apartamento SP" R$ 450.000

### PASSO 4 — /movimentacoes cross-account (~15min)

> Página nova com seed populado (20 tx).

- [ ] **4.1** Abrir http://localhost:8000/movimentacoes
- [ ] **4.2** Tabela mostra 20 tx (paginação 100 default — só 1 página)
- [ ] **4.3** Filtros funcionam:
  - Conta = Teste Ações XP → 13 tx (1 deposit, 6 trade_buy, 1 trade_sell, 1 crypto_buy, 4 dividend)
  - Conta = Teste Renda Fixa BTG → 7 tx (1 deposit, 3 rf_apply, 2 trade_buy, 1 dividend)
  - Direção = "Saídas (-)" → 12 tx
  - Direção = "Entradas (+)" → 8 tx
  - Tipo = "rf_apply" → 3 tx
  - Tipo = "dividend" → 5 tx (4 XP + 1 BTG)
- [ ] **4.4** Sort por coluna: clicar "Data" inverte ↑/↓; clicar "Valor" ordena por amount
- [ ] **4.5** Paginação 50/100/200/500 (só relevante com volume maior)
- [ ] **4.6** **Export CSV**: botão 📥 baixa `movimentacoes_2026-04-26.csv` com BOM UTF-8
- [ ] **4.7** Totais no rodapé refletem TODO o filtrado (não só a página)
- [ ] **4.8** **Reconciliação manual**: 5 linhas de tipo `dividend` têm botão 🔗 amarelo
- [ ] **4.9** Click 🔗 em "DIVIDENDOS PETR4" → modal pede ticker → digita PETR4 → toast OK + tx vinculada (DB: `related_id` setado)
- [ ] **4.10** Botão 🖨 Imprimir abre window.print

### PASSO 5 — /import C6 Dividendos (~15min)

- [ ] **5.1** Abrir http://localhost:8000/import
- [ ] **5.2** Card verde "💰 Importar Dividendos" presente
- [ ] **5.3** Click → modal abre, select Conta carrega 2 opções (XP + BTG)
- [ ] **5.4** **Sample CSV sintético**: criar `/tmp/div.csv`:
  ```bash
  cat > /tmp/div.csv << 'EOF'
  data,desc,valor
  20/04/2026,DIVIDENDOS RECEBIDOS PETR4,180.00
  21/04/2026,JCP ITUB4,420.50
  22/04/2026,RENDIMENTO KNRI11,95.30
  EOF
  ```
- [ ] **5.5** Selecionar conta XP + upload `/tmp/div.csv` → click "Analisar"
- [ ] **5.6** Tabela preview mostra 3 linhas, todas **matched** (verde) — porque PETR4/ITUB4/KNRI11 existem como positions
- [ ] **5.7** Tags summary: matched=3, ambiguous=0, unmatched=0
- [ ] **5.8** Click "Confirmar Importação" → toast OK → `/movimentacoes` mostra 3 dividendos novos
- [ ] **5.9** Sem pdfplumber → upload PDF deve dar 400 amigável (testar com qualquer PDF)

### PASSO 6 — /alerts BUG17 fix (~5min)

- [ ] **6.1** Abrir http://localhost:8000/alerts
- [ ] **6.2** Criar: ticker=PETR4, indicador=ROE, operador=`>`, threshold=15 → click Criar
- [ ] **6.3** Toast OK, alerta aparece na lista
- [ ] **6.4** DB:
  ```bash
  docker exec finanalytics_postgres psql -U finanalytics -d finanalytics \
    -c "SELECT user_id, ticker, condition FROM indicator_alerts ORDER BY created_at DESC LIMIT 1;"
  ```
  Esperado: `user_id` = `09d05145-bf74-481e-ab1d-efa3ea9775b5` (NÃO `user-demo`)
- [ ] **6.5** Listar `/api/v1/alerts/indicator` (com auth header) → retorna só os do user logado

### PASSO 7 — i18n PT/EN (~10min)

- [ ] **7.1** Botão `PT/EN` na topbar (esquerda do toggle 🌙/☀️)
- [ ] **7.2** Click → cycle pra EN; localStorage `fa_locale=en`
- [ ] **7.3** Pages que devem trocar:
  - `/dashboard` (tabs DT)
  - `/carteira` (title, subtitle, tabs Overview/Posições/Trades/etc, sec titles, botões)
  - `/movimentacoes` (filtros, colunas, totais)
  - `/alerts` (form labels, botões, colunas)
  - `/import` (title + 5 seções)
  - `/screener`, `/watchlist`, `/profile`, `/admin`, `/hub`, `/macro`, `/forecast`, etc
- [ ] **7.4** Sidebar mostra "Visão Geral" → "Overview" em EN; "Movimentações" → "Transactions"
- [ ] **7.5** F5 mantém locale
- [ ] **7.6** Texto NÃO marcado com `data-i18n` continua em PT (intencional)
- [ ] **7.7** Voltar pra PT — todas mensagens revertem

### PASSO 8 — G4 auth refactor end-to-end (~10min)

> 14/14 páginas migradas pra FAAuth.requireAuth.

- [ ] **8.1** Logout em `/dashboard` (FAModal "Deseja sair?") → redirect `/login`
- [ ] **8.2** Acessar `/carteira` sem token → redirect `/login`
- [ ] **8.3** Login com "Lembrar-me 7 dias" marcado
- [ ] **8.4** Após login, qualquer página privada respeita FAAuth
- [ ] **8.5** Manualmente apagar `localStorage.access_token` → próxima ação faz silent refresh (se refresh_token ainda válido) ou redireciona pro login

### PASSO 9 — /dashboard OCO modal (sem disparar ordens) (~20min)

> Validações UI das Phases A+B+C sem afetar mercado.

- [ ] **9.1** Abrir `/dashboard` aba "Ordens" (lista) — pode estar vazia se sem ordens hoje
- [ ] **9.2** Pra testar modal sem ordens reais: enviar uma ordem **simulação** distante do mercado (ex: BUY PETR4 100 @ R$10) — vai ficar PendingNew
- [ ] **9.3** Em "Ordens" aparece com botão 🛡 (azul) + ✕ (vermelho)
- [ ] **9.4** Click 🛡 → modal "Anexar OCO" abre
- [ ] **9.5** **Phase A** (1 nível): TP=15 SL=8 → counter "100/100 ✓ verde"
- [ ] **9.6** **Phase B** (split): click "+ nível" → 2º com qty=0; editar qty 60/40 → confirmar OK
- [ ] **9.7** Validação sum: 50/40 (=90) → bloqueia com mensagem
- [ ] **9.8** Validação proteção: nível com TP+SL ambos desmarcados → erro
- [ ] **9.9** **Phase C Trailing**: checkbox "🔄 TRAILING" → trail-box revela
- [ ] **9.10** Radio R$ ↔ % muda placeholder
- [ ] **9.11** Trailing sem SL marcado → erro "trailing requer SL marcado"
- [ ] **9.12** Submit envia POST `/api/v1/agent/order/attach_oco` — retorna `{ok:true,group_id:...}` se profit_agent restartado, ou `{"error":"not found"}` se ainda código antigo
- [ ] **9.13** Cancelar a ordem mãe (✕) — limpa o teste

---

## 🟡 PASSO 10 — Pré-pregão (executar antes de segunda 10h BRT) (~10min)

> **Necessário** pra Bloco B funcionar.

- [ ] **10.1** Restart `profit_agent` (Windows host) com código novo (Phase A+B+C+D + ML refactor):
  ```powershell
  $pid = (Get-NetTCPConnection -LocalPort 8002 -State Listen).OwningProcess
  Stop-Process -Id $pid -Force  # pode pedir admin
  Start-Process -FilePath ".venv\Scripts\python.exe" `
    -ArgumentList "src\finanalytics_ai\workers\profit_agent.py" `
    -WindowStyle Hidden -RedirectStandardOutput ".profit_agent.log"
  ```
- [ ] **10.2** Aguardar 10s → testar rotas novas:
  ```bash
  curl -s http://localhost:8000/api/v1/agent/oco/groups
  # esperado: {"groups":[],"count":0}
  curl -s http://localhost:8000/api/v1/agent/oco/state/reload
  # esperado: {"ok":true,"groups_loaded":0}
  ```
- [ ] **10.3** `tail -f .profit_agent.log` — deve aparecer `oco_groups_monitor.started`, `trail_monitor.started`, `oco.state_loaded groups=0`
- [ ] **10.4** Conta DLL re-conectada (verificar via `/profile` aba Contas — DLL ATIVA)

---

## 🔴 BLOCO B — Pregão aberto (segunda 27/abr 10h-18h BRT)

### PASSO 11 — DT básico (~30min)

- [ ] **11.1** **Cancel order** (BUG7 fix): BUY PETR4 R$30 longe → ✕ → CANCELED em ~5s; fallback `/positions/dll` em 10s
- [ ] **11.2** **Aba Ordem**: BUY PETR4 100 @ Market → toast ok + aparece em Ordens
- [ ] **11.3** **Aba OCO** (legacy): TP 35 + SL 28 stop_limit 27.50 → ordem em "Ordens" + polling
- [ ] **11.4** **Aba Pos.**: search PETR4 → GetPositionV2 traz preço médio + qty
- [ ] **11.5** **Cotação live PETR4**: profit_agent :8002/quotes (subscrito) → Yahoo → BRAPI (Decisão 20)
- [ ] **11.6** Aba Trades em /carteira: criar BUY/SELL → confirma trade chega no DLL + status reflete em /positions

### PASSO 12 — OCO Phase A end-to-end (~20min)

- [ ] **12.1** Limit BUY PETR4 100 @ R$30 longe → enviar (PendingNew)
- [ ] **12.2** Click 🛡 → modal → TP=52, SL=28, SL limit=27.50 → "Anexar OCO"
- [ ] **12.3** Toast: "OCO anexado · group XXXXXXXX · 1 nível(eis)"
- [ ] **12.4** DB: `SELECT status, parent_order_id FROM profit_oco_groups` → 1 row `awaiting`
- [ ] **12.5** `/api/v1/agent/oco/groups` → 1 group; `/oco/groups/{id}` → mostra parent + 1 level
- [ ] **12.6** Reduzir preço da mãe pra fillar → status vira `active` ou `partial`
- [ ] **12.7** TP/SL gerados aparecem em "Ordens"
- [ ] **12.8** Log profit_agent: `oco_group.dispatched group=... filled=N/M levels=K`

### PASSO 13 — OCO Phase B Splits (~15min)

- [ ] **13.1** Limit BUY VALE3 100 @ valor longe → pending
- [ ] **13.2** 🛡 OCO → +nível, qty 60/40, TP1=72 SL1=58, TP2=75 SL2=58 → confirma
- [ ] **13.3** DB: 2 rows em `profit_oco_levels` com `level_idx` 1 e 2
- [ ] **13.4** Validação sum: tentar com 50/40 → mensagem `Soma das qty (90) deve bater parent.qty (100)`

### PASSO 14 — OCO Phase C Trailing (~25min)

- [ ] **14.1** **Trail R$**: BUY PETR4 100 @ market → fill imediato; OCO 1 nível: TP=35 SL=28 + ☑ Trailing R$ 0,50
- [ ] **14.2** Mover preço pra +R$ 1 → log `trailing.adjusted group=... lv=1 hw=31.0000 new_sl=30.5000`
- [ ] **14.3** **Trail %**: OCO com Trailing 1.5% (radio %) em VALE3; mover +2% → SL atualiza proporcionalmente
- [ ] **14.4** **Immediate trigger** (Decisão 6): OCO com SL trigger 50 (acima do last 48), trailing R$ 0,50 → log `trailing.immediate_trigger`; market sell disparada imediato

### PASSO 15 — OCO Phase D persistence + cancel manual (~15min)

- [ ] **15.1** Com 1+ group active → restart profit_agent (admin)
- [ ] **15.2** Log inicial: `oco.state_loaded groups=N levels=M order_index=K`
- [ ] **15.3** `/api/v1/agent/oco/groups` retorna mesmos groups, status preservado
- [ ] **15.4** Cross-cancel continua funcionando após restart (TP fill → SL cancela)
- [ ] **15.5** **Cancel manual**: `POST /api/v1/agent/oco/groups/{group_id}/cancel` → resposta `{ok:true, cancelled_orders:N}`; DB `status='cancelled'` + `completed_at` setado

### PASSO 16 — Tick-dependent + reconcile (~20min)

- [ ] **16.1** Aviso saldo insuficiente antes de confirmar trade BUY (UI guard real-time)
- [ ] **16.2** Indicadores em `/marketdata?ticker=PETR4` — RSI/MACD/Bollinger reflete tick recente
- [ ] **16.3** `/dashboard` painel ML signals Live: tickers com BUY/SELL atualizados
- [ ] **16.4** DI1 realtime: `di1_tick_age_high` deve ficar resolved durante pregão (tick < 120s)
- [ ] **16.5** Scheduler `reconcile_loop` (a cada 5min em 10h-18h BRT) executa
- [ ] **16.6** Order enviada via dashboard → após 5min, status no DB confere com DLL

---

## 🟠 BLOCO C — Pushover (precisa celular ligado com app)

- [ ] **17.1** Grafana UI → Alerting → rule → "Test" → push chega no celular
- [ ] **17.2** `di1_tick_age_high` firing fora pregão → critical com siren (priority=1)
- [ ] **17.3** Alerta indicador em `/alerts` prestes a disparar → push normal (priority=0)
- [ ] **17.4** Escalation: parar profit_agent 25min → 5 reconcile errors → critical

---

## 🟠 BLOCO D — Sudo manual (você presente, fora pregão)

- [ ] **18.1** Endpoint `POST /api/v1/agent/restart` com `require_sudo` → 401 + `X-Sudo-Required: true` sem token
- [ ] **18.2** FASudo.confirm prompt → senha → POST com header → 200
- [ ] **18.3** Health `:8002/health` volta em <10s após restart
- [ ] **18.4** Conta DLL re-conectada automaticamente
- [ ] **18.5** Phase D log: `oco.state_loaded groups=N` mostra groups in-flight recarregados
- [ ] **18.6** Auto-reconnect TimescaleDB: down 20min → reconnect lazy
- [ ] **18.7** Log throttled: TICK_V1 callback error usa contador (count=21001, 22001 — Sprint Backend V1)

---

## 🔵 BLOCO E — Samples reais BTG/XP (você fornecer arquivos)

> C6 Fase 5 — validação real após Bloco A passar.

- [ ] **19.1** Sample CSV BTG → `/import` Dividendos → preview matched ≥80%
- [ ] **19.2** Sample OFX BTG → idem
- [ ] **19.3** Sample PDF BTG (se houver) → `parse_pdf` extrai e classifica
- [ ] **19.4** Sample CSV/OFX/PDF XP → idem
- [ ] **19.5** Edge cases reais: linhas com R$ + IRRF, datas exóticas, tickers com sufixo (PETR4F), valores negativos (devolução)
- [ ] **19.6** Após validação, **importar dados reais** dos investimentos (substitui dados teste do seed) — gatilho da migração final

---

## ⚫ BLOCO F — Externo (bloqueado por terceiros)

### PASSO 20 — Nelogica 1m bars (~48h após pedido)

- [ ] **20.1** Quando arquivo chegar: rodar `runbook_import_dados_historicos.md`
- [ ] **20.2** `scripts/import_historical_1m.py` → `ohlc_1m` (source='nelogica_1m')
- [ ] **20.3** `populate_daily_bars.py --source 1m` → `profit_daily_bars`
- [ ] **20.4** `resample_ohlc.py` 5m/15m/30m/60m → `ohlc_resampled`

### PASSO 21 — Z5 ML multi-horizon (após Nelogica)

- [ ] **21.1** Adaptar `retrain_top20_h21.py` pra h3, h5, h21
- [ ] **21.2** Treinar pickles para top tickers em cada horizon
- [ ] **21.3** `/api/v1/ml/predict_ensemble` ganha utilidade real (multi-horizon)
- [ ] **21.4** Validar via `/dashboard` Aba Signals e `/overview` ML badge

---

## Status atual (26/abr/2026 madrugada)

- ✅ **53 commits** acumulados no fds (super sessão)
- ✅ **DB seedado** com 2 contas teste (commit `7555662`)
- ✅ **Backend** deployado: API container restartado, rotas novas live (`/oco/*`, `/wallet/transactions`, `/wallet/transactions/{id}/reconcile`, `/import/dividends/*`)
- ✅ **Frontend** deployado: SW v43, 42 páginas com i18n, /carteira refatorada (filtro conta + Overview tab + sem portfolio selector)
- ⚠️ **profit_agent**: deployado em disco mas precisa restart pra Phase A+B+C+D ativarem (Passo 10)
- ⚠️ **dashboard.html G4**: migrado, validar Passo 8

---

## Comandos úteis (referência)

### Estado dos OCO groups
```bash
docker exec finanalytics_timescale psql -U finanalytics -d market_data -c \
  "SELECT status, count(*) FROM profit_oco_groups GROUP BY status;"
```

### Estado do seed
```bash
docker exec finanalytics_postgres psql -U finanalytics -d finanalytics -c \
  "SELECT 'accounts' AS tbl, count(*) FROM investment_accounts
   UNION ALL SELECT 'portfolios', count(*) FROM portfolios
   UNION ALL SELECT 'trades', count(*) FROM trades
   UNION ALL SELECT 'crypto', count(*) FROM crypto_holdings
   UNION ALL SELECT 'rf', count(*) FROM rf_holdings
   UNION ALL SELECT 'tx', count(*) FROM account_transactions;"
```

### Re-seed (caso precise resetar)
```bash
docker exec finanalytics_postgres psql -U finanalytics -d finanalytics -c \
  "DELETE FROM account_transactions;
   DELETE FROM trades; DELETE FROM positions; DELETE FROM crypto_holdings;
   DELETE FROM rf_holdings; DELETE FROM other_assets;
   DELETE FROM portfolio_name_history; DELETE FROM portfolios;
   DELETE FROM investment_accounts;"
docker exec -i finanalytics_postgres psql -U finanalytics -d finanalytics \
  < scripts/seed_test_accounts.sql
```

---

**Próximo gatilho**: você executa Passos 0-9 agora (~1h45 total). Reporta achados (qualquer FAIL) inline. Passo 10 (restart agent) hoje à tarde ou domingo. Bloco B (passos 11-16) segunda 27/abr no pregão.
