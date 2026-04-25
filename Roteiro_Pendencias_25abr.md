# FinAnalytics AI — Pendências consolidadas

> **Data**: 25/abr/2026 (sábado, após sessão A+B)
> **Base**: pós-cleanup `a86b1fc` + Playwright 25/abr fechou ~30 itens
> **Restantes**: 117 itens `[ ]` + 6 BUGs abertos
> **Login**: marceloabisquarisi@gmail.com / admin123 (master)

## Calendário

| Quando | Janela | Cobertura |
|---|---|---|
| **Hoje (sáb 25/abr)** + **Amanhã (dom 26/abr)** | qualquer hora | seções §A todas — UI/backend/edge cases sem pregão |
| **Segunda 27/abr (pregão 10h-18h BRT)** | janela única | seção §B — DLL viva, ordens, ticks live |
| **Sessões dedicadas** | qualquer dia | seção §C — sprints longas (4-6h cada) |

---

## §A — Hoje/Amanhã (sem pregão)

### §A.1 — Configuração + UI Feature B (DLL setup, sem ordem real) — ~45min

> Configura DLL nas contas; ordem real fica pra 27/abr.

- [ ] `/profile#invest`: conta criada mostra campos opcionais `dll_account_type/broker_id/account_id/routing_password` vazios → sem quebrar listagem
- [ ] Conectar DLL numa conta existente: botão "Conectar DLL" preenche os 4 campos + marca `dll_active=true`
- [ ] Toggle ativar/desativar DLL: reflete em `/dashboard` Aba Conta
- [ ] Simulador `dll_account_type='simulator'` → não precisa routing_password (env `PROFIT_SIM_*` fallback)
- [ ] `real_operations_allowed`: admin-only; marcar conta prod → dashboard *deve permitir* ordem real (validar UI flag, ordem real só 27/abr)

### §A.2 — Feature C Cash Ledger (UI + scheduler) — ~1h

> Backend já validado (etapa A 25/abr). Aqui é UI + cenários extras.

- [ ] `/profile#invest`: botão Depositar/Sacar numa conta → modal com valor
- [ ] POST `/api/v1/wallet/withdraw` saldo insuficiente → FAModal "Confirma saldo negativo?" antes de enviar (UI guard)
- [ ] Trade SELL → credita em T+1 pending (já testado BUY; SELL via /carteira)
- [ ] Scheduler `settle_cash_transactions_job` 00:00 BRT → criar trade BUY antes de meia-noite, conferir amanhã se pending virou settled
- [ ] **C3b** ETF metadata: campos `benchmark`, `management_fee`, `performance_fee`, `liquidity_days` em `/etf` — salvar 4 + verificar card
- [ ] **C4** Crypto D+0: `/crypto` aporte BTC → debita caixa no dia (sem pending); resgate parcial → credita no dia
- [ ] **C5** RF D+X (prazo do título):
  - Aplicar CDB liquidez D+1 → tx pending due_date=T+1
  - Aplicar LCI liquidez D+30 → pending due_date=T+30
  - Resgate antes vencimento LCI/LCA → warn + não libera caixa até due_date
  - Scheduler `settle_due_transactions_job`: criar título com vencimento amanhã, ver se settle ocorre

### §A.3 — Feature F UX 8 refinements — ~1.5h

- [ ] **F1** Modal Histórico em `/profile#invest`:
  - Filtros período (início/fim), direção (crédito/débito/todos), include_pending toggle
  - Linha **Total** no footer (soma créditos − débitos)
  - Botão Imprimir → layout clean com FAPrint
- [ ] **F2** Withdraw/trade/crypto deixaria caixa < 0 → FAModal.confirm antes de submeter
- [ ] **F3** Campo valor vazio/0/negativo → input highlighted + toast warn + não submete
- [ ] **F4** Apelido em listings: `/carteira` Trades, `/crypto` cada linha mostra apelido conta ("XP Principal"); filtrar por conta funciona
- [ ] **F5** Crypto aporte/resgate: `/crypto` botões **Aportar** + **Resgate parcial** (qty validada contra holding.qty)
- [ ] **F6** RF Aplicar: `/fixed-income` aba "Buscar Títulos" cada linha botão verde **Aplicar** → modal cascade Conta → Portfolio → campos principal/data; SEM `window.prompt()` nativo
- [ ] **F7** Delete conta com saldo:
  - cash_balance > 0 → 409 "Há saldo em caixa" *(backend OK; UI guard)*
  - holdings → 409 "Há investimentos vinculados"
  - zerada + sem holdings → soft-delete `is_active=false`
- [ ] **F8** `/fixed-income` layout: sidebar aberta NÃO sobrepõe content; colapsada content até `--sb-w-collapsed` margin; transição cubic-bezier .22s

### §A.4 — G2 Rename portfolio inline — ~15min

- [ ] `/profile#invest` card de conta mostra **1 carteira "Portfolio"** (não mais lista N)
- [ ] Botão **✎** ao lado do portfolio → modal/prompt rename → PATCH `/api/v1/portfolios/{id}`
- [ ] Rename gera entry em `portfolio_name_history` (old, new, when)
- [ ] Seção atualiza sem reload após rename
- [ ] `/fixed-income` aba Carteira RF: botão rename também disponível

### §A.5 — Golden path páginas críticas (sem pregão) — ~1.5h

- [ ] `/carteira` — selector portfolio atualiza tabelas; tab Outros: cadastro imóvel → IR isento check; botão 🖨
- [ ] `/alerts` — criar alerta PETR4 preço > 50 nota "teste"; filtrar por status; cancelar via FAModal; "Avaliar agora" → trigger count
- [ ] `/screener` — EXECUTAR, filtros PL_max/ROE_min, click ticker → fundamental
- [ ] `/watchlist` — Add/remove ticker SSE live; excluir watchlist bloqueia se última
- [ ] `/ml` — batch 118 tickers; filtro min_sharpe; Histórico; Mudanças
- [ ] `/performance` — KPIs (drawdown/sharpe/beta/alpha); charts; heatmap; 🖨
- [ ] `/diario` — add BUY PETR4 entry=30 exit=33 qty=100; editar; excluir; stats
- [ ] `/fixed-income` — aplicar título; comparar 2; resgatar parcial; delete com saldo bloqueia
- [ ] `/crypto` — aporte BTC; resgate parcial; selector de conta
- [ ] `/admin` — lista users; CRUD agentes; role User/Master + Admin checkbox; promover/rebaixar
- [ ] `/hub` — event_records filtros; reprocessar dead_letter; cleanup > 30d

### §A.6 — Smoke 24 páginas (carrega/helpers/sort/empty CTA) — ~1h

- [ ] `/correlation`, `/anomaly`, `/sentiment`, `/forecast`, `/backtest`, `/optimizer`, `/var`
- [ ] `/dividendos`, `/etf`, `/laminas`, `/fundos`, `/patrimony`
- [ ] `/opcoes`, `/opcoes/estrategias`, `/vol-surface`, `/daytrade/setups`, `/daytrade/risco`, `/tape`
- [ ] `/marketdata`, `/macro`, `/fintz`, `/import`, `/subscriptions`, `/whatsapp`

### §A.7 — Auth/RBAC/Network edge cases — ~45min

**Auth**:
- [ ] Senha errada → toast vermelho
- [ ] "Lembre-me 7d" expiry estendido (silent refresh)
- [ ] Reset password com/sem token (`/reset-password`)
- [ ] Já-logado em `/login` → redirect `/dashboard`

**Sessão**:
- [ ] Apagar `localStorage.access_token` + tentar ação → redirect `/login`
- [ ] Com refresh token (Lembre-me) → silent refresh sem prompt

**RBAC**:
- [ ] User comum em `/admin` ou `/hub` → 401 backend + "access denied" frontend

**Forms**:
- [ ] qty negativa em trades → toast warn + não submete
- [ ] exit < entry em diário → toast warn + não submete

**Network**:
- [ ] Fast 3G simulado (DevTools throttle) → skeletons aparecem antes do dado
- [ ] Offline → PWA cache serve assets/CSS; `/api/*` falha graciosamente
- [ ] DB down (`docker stop finanalytics_timescale`) → toast vermelho com correlation_id

### §A.8 — Pushover ao vivo — ~30min

> Precisa do celular com app Pushover.

- [ ] Grafana UI > Alerting > rule > "Test" → push chega no celular
- [ ] `di1_tick_age_high` firing (já fora pregão hoje) → critical com siren (priority=1)
- [ ] Alerta indicador em `/alerts` prestes a disparar → push normal (priority=0)
- [ ] Escalation: parar profit_agent 25min → 5 reconcile errors → critical (precisa tolerar agent down ~30min)

### §A.9 — Profit Tickers UI — ~30min

- [ ] `/profit-tickers` filtros persistem em `localStorage` ao recarregar
- [ ] Bulk activate: selecionar N tickers + botão "Ativar selecionados"
- [ ] Badge 4 estados:
  - 🟢 **Coleta Ativa** — subscribed=true + has_recent_data=true
  - 🟡 **Aguardando feed** — subscribed=true + has_recent_data=false (<30min)
  - 🔴 **Falha DLL** — subscribed=false + active=true
  - ⚪ **Inativo** — active=false
- [ ] Tooltip em cada badge explicando significado
- [ ] Colunas renomeadas (conferir vs nome anterior)
- [ ] Bulk top500: confirmar `scripts/bulk_cadastrar_top500_tickers.py` cadastrou 500 mais líquidos

### §A.10 — Sudo + Profit Agent restart — ~45min

> Sensível host Windows — fazer com cuidado, restart leva ~10s.

**FASudo**:
- [ ] `FASudo.fetch` em ação destrutiva (deletar usuário admin) → modal com senha; cache 5min
- [ ] 401 + header `X-Sudo-Required` → re-prompt senha
- [ ] `FASudo.fetchJson` retorna parseado

**Restart Profit Agent**:
- [ ] Restart via `/admin` ou `/profile` → confirm password → `os._exit(0)` no agent → NSSM restart automático
- [ ] Health `:8002/health` volta em <10s após restart
- [ ] Conta DLL anterior re-conectada automaticamente (via `dll_active` persistido em DB)

**Auto-reconnect**:
- [ ] `finanalytics_timescale` down 20min + subir → profit_agent reconecta em <5s sem restart manual
- [ ] Log throttled: 3 silent excepts → 1 log/min máx (não spammy)

### §A.11 — Etapa B residuais — ~30min

- [ ] PWA install Chrome/Edge: oferece "Instalar app" (ícone barra); after install ícone na taskbar; offline assets/css cache; refresh em página visitada → instant cache
- [ ] FAPrint UI manual em `/carteira`, `/performance`, `/portfolios` (302→profile), `/dividendos`: botão "🖨 Imprimir" + preview oculta sidebar/topbar/botões; expande tabelas; rodapé "FinAnalytics AI — impresso em DD/MM/YYYY HH:MM"
- [ ] FACharts com dados reais: tooltip + legenda bottom + cores consistentes; testar em /performance, /backtest, /correlation

---

## §B — 27/abr (segunda, pregão 10h-18h BRT)

> **Janela única — só validável com DLL aceitando ordens em pregão.**

### §B.1 — Dashboard DayTrade — ~1.5h

- [ ] **Aba Ordens — cancel order individual** (BUG7 secundário, fix aplicado 25/abr):
  - Limit BUY PETR4 R$30 (longe do mercado) → enviar
  - Em "Ordens" lista → click ✕ → status CANCELED em ~5s (polling 600/2000/5000ms)
  - Fallback `/positions/dll` em 10s consolida estado
- [ ] **Aba Ordem**: BUY PETR4 100 @ Market em SIMULAÇÃO → toast ok + aparece em Ordens (já validado em paper, validar live)
- [ ] **Aba OCO**: TP 35 + SL 28 stop_limit 27.50 → ordem em "Ordens" + polling automático
- [ ] **Aba Pos.**: search PETR4 → GetPositionV2 traz preço médio + qty real-time
- [ ] **Cotação PETR4 live**: primeiro tenta `profit_agent :8002/quotes` (subscrito) → Yahoo → BRAPI (ordem Decisão 20)
- [ ] Aba Trades em `/carteira`: criar BUY/SELL → confirma trade chega no DLL + status reflete em `/positions`

### §B.2 — Validações dependentes de tick live — ~30min

- [ ] Aviso saldo insuficiente antes de confirmar trade BUY (UI guard real-time, depende de cotação atual)
- [ ] Indicadores em `/marketdata?ticker=PETR4` — RSI/MACD/Bollinger reflete tick recente
- [ ] `/dashboard` painel ML signals Live: tickers com BUY/SELL atualizados pós-pregão
- [ ] DI1 realtime: `di1_tick_age_high` deve ficar resolved durante pregão (tick < 120s)

### §B.3 — Reconcile real-time — ~15min

- [ ] Scheduler `reconcile_loop` (a cada 5min em 10h-18h BRT) executa: trigger update em `profit_orders` via DLL EnumerateAllOrders
- [ ] Order enviada via dashboard → após 5min, status no DB confere com DLL
- [ ] Se DLL retorna order com status diff, log `reconcile.discrepancy.fixed`

---

## §C — Sessões dedicadas (qualquer dia)

### §C.1 — C6 Dividendos (não iniciado) — ~5h

- [ ] Parser de extrato (PDF/CSV/OFX) detecta "DIVIDENDOS RECEBIDOS" / "JCP" / "RENDIMENTOS"
- [ ] Auto-reconciliação: casa CNPJ+data+valor com holding em `positions` → cria `account_transactions` tipo=dividend, direction=credit, settled_at=data
- [ ] UI em `/import`: botão "Importar Dividendos" separado
- [ ] UI Movimentações global: página `/movimentacoes` (ou aba em `/carteira`) listando todas transactions agregadas (depósito/saque/trade/cripto/RF/dividendos) com filtros ticker/portfolio/direção/período
- [ ] Reconciliação manual: linha não-casada → operador anexa ao ticker correto
- [ ] Tests: import de extrato exemplo BTG e XP

### §C.2 — Tech debt — variado

- [ ] **Z5**: aguardar arquivo Nelogica 1m (~48h) → `runbook_import_dados_historicos.md` + treinar pickles h3/h5/h21
- [ ] **G4 auth refactor**: 22 páginas inline → `auth_guard.js` (`dashboard.html` migrado 25/abr; 21 restantes) — ~4-6h
- [ ] **G6 i18n spread**: aplicar `data-i18n` em forms/headers de `/dashboard`, `/carteira`, `/alerts`, `/fixed-income` — ~2h
- [ ] **BUG8 SMTP backup**: configurar SMTP além Pushover para alerts critical — ~1h
- [ ] **Light mode cleanup** (Decisão 19): páginas com `:root` próprio que decidir unificar — variável
- [ ] **Etapa 2 refactor portfolio**: revisar `/carteira`, `/fixed-income`, `/dashboard` selectors — confirmar listam só "Portfolio" por conta; atualizar copy/labels que mencionam "carteira default" ou "RF Padrão" — ~1h

### §C.3 — Bugs abertos

| # | Bug | Impacto | Próximo passo |
|---|---|---|---|
| BUG2 | G4: 22 páginas com auth inline | Médio — perdem refresh 7d | §C.2 G4 |
| BUG3 | G6: `data-i18n` não aplicado nos HTMLs in-page | Baixo — sidebar+topbar OK | §C.2 G6 |
| BUG4 | `/predict_ensemble` 404 para tickers sem pickle | Baixo — h21 OK top-116 | §C.2 Z5 |
| BUG5 | Light mode em páginas com `:root` próprio | Baixo — intencional (Decisão 19) | §C.2 light mode |
| BUG6 | 3 alert rules só firing após 1º increment | Baixo — esperado | — |
| BUG8 | SMTP backup ausente para Pushover | Médio — se Pushover cair, sem redundância | §C.2 SMTP |

---

## Comandos úteis

### Pré-flight (rodar antes de cada sessão)
```bash
docker ps --filter name=finanalytics --format "{{.Names}}: {{.Status}}"
curl -s http://localhost:8000/health
curl -s http://localhost:8002/health
```

### Smoke pós-deploy
```bash
for r in /dashboard /carteira /portfolios /alerts /profile /fixed-income /crypto /profit-tickers /admin /hub; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000${r}")
  echo "${r}: ${code}"
done
# Esperado: tudo 200, exceto /portfolios = 302
```

### Login + token (dev)
```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login -H "Content-Type: application/json" -d '{"email":"marceloabisquarisi@gmail.com","password":"admin123"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
```

### Stop profit_agent (Windows host) — para testar §A.10 escalation
```powershell
Get-Process python | Where-Object { $_.MainWindowTitle -like "*profit*" -or $_.CommandLine -like "*profit_agent*" } | Stop-Process -Force
```

### DB throttle test (§A.7 Network DB down)
```bash
docker stop finanalytics_timescale
# tentar /marketdata → toast com correlation_id esperado
docker start finanalytics_timescale
```

---

## Estimativas por janela

| Janela | Seções | Tempo estimado |
|---|---|---|
| **Hoje (sáb 25/abr)** | §A.1 + §A.2 + §A.3 + §A.4 = features B/C/F/G2 | ~3h45min |
| **Amanhã (dom 26/abr)** | §A.5 + §A.6 + §A.7 + §A.8 = golden path + smoke + edge + Pushover | ~3h45min |
| **Hoje OU amanhã** | §A.9 + §A.10 + §A.11 = profit-tickers + sudo + B residuais | ~1h45min |
| **Segunda 27/abr (pregão)** | §B.1 + §B.2 + §B.3 = dashboard DT + tick-dependent + reconcile | ~2h15min |
| **Sessões dedicadas** | §C.1 (Dividendos) + §C.2 (tech debt 6 itens) | 5h + 9-12h |

---

## Status

- **Total pendente**: 117 itens distribuídos entre §A (96), §B (10), §C (11+)
- **Bloqueado por externo**: Z5 (Nelogica 1m, ~48h)
- **BUGs**: 6 abertos (1 médio BUG8 SMTP, 5 baixos)

---

**Documento gerado em**: 25/abr/2026 (sáb, após cleanup `a86b1fc`)
**Próximo gatilho**: começar §A.1 hoje à tarde / §A.5 amanhã manhã / §B.1 segunda 10h BRT
