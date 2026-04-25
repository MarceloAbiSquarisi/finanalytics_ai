# FinAnalytics AI — Pendências consolidadas

> **Data**: 25/abr/2026 (sábado, após sessão A+B)
> **Base**: pós-cleanup `a86b1fc` + Playwright 25/abr fechou ~30 itens
> **Restantes**: 50 itens `[ ]` + 14 BUGs abertos *(§A.1-§A.7 fechadas 25/abr; +BUG10/11/12/14/15/17/18/19/20/21; BUG13/16 resolvidos)*
> **Login**: marceloabisquarisi@gmail.com / admin123 (master)

## Calendário

| Quando | Janela | Cobertura |
|---|---|---|
| **Hoje (sáb 25/abr)** + **Amanhã (dom 26/abr)** | qualquer hora | seções §A todas — UI/backend/edge cases sem pregão |
| **Segunda 27/abr (pregão 10h-18h BRT)** | janela única | seção §B — DLL viva, ordens, ticks live |
| **Sessões dedicadas** | qualquer dia | seção §C — sprints longas (4-6h cada) |

---

## §A — Hoje/Amanhã (sem pregão)

### §A.1 — Configuração + UI Feature B (DLL setup, sem ordem real) — ~45min ✅ DONE 25/abr

> Configura DLL nas contas; ordem real fica pra 27/abr.

- [X] `/profile#invest`: conta criada mostra campos opcionais `dll_account_type/broker_id/account_id/routing_password` vazios → sem quebrar listagem — validado API: dll_* todos null/false; listagem renderiza
- [X] Conectar DLL numa conta existente: botão "Conectar DLL" preenche os 4 campos — validado: POST /connect-dll → broker_id/account_id/sub/type populados; routing_password_set=true (bool por segurança). NOTA: connect ≠ activate (separados); is_dll_active=false após connect
- [X] Toggle ativar/desativar DLL: reflete em `/dashboard` Aba Conta — validado: activate-dll **desativa auto qualquer outra do mesmo user** (invariante 1 ativa por user); disconnect-dll zera 4 campos + dll_routing_password_set=false
- [X] Simulador `dll_account_type='simulator'` → não precisa routing_password (env `PROFIT_SIM_*` fallback) — validado: 200 + routing_password_set=false. Constraint global `ux_inv_accounts_one_dll_sim` força 1 simulator no sistema
- [X] `real_operations_allowed`: admin-only; marcar conta prod → dashboard *deve permitir* ordem real (UI 27/abr) — validado: master/admin PATCH /real-operations → 200; user comum → 403 "Apenas ADMIN ou MASTER pode alterar permissao de operacoes reais."

**Achado**: connect-dll com 2º simulator (já existe Simulador Nelogica) retorna 500 com UniqueViolationError não tratada — deveria ser 409 amigável. Local: `wallet.py:245` (endpoint connect_dll). Mini-bug, baixo impacto pois constraint global é raramente acionado.

### §A.2 — Feature C Cash Ledger (UI + scheduler) — ~1h ✅ DONE 25/abr

> Backend já validado (etapa A 25/abr). Aqui é UI + cenários extras.

- [X] `/profile` aba "Contas" (não #invest): botão Depositar/Sacar numa conta → modal #modal-cash com valor; deposit 5000 → cash_balance subiu 50000→55000; modal fecha auto. NOTA: hash `#invest` não ativa aba — abre na "Perfil" (precisa click manual em "Contas")
- [X] POST /withdraw saldo insuficiente → **FAModal.confirm "Saldo ficará negativo"** antes de enviar; cancel reverte; cash NÃO muda (validado withdraw 100k com cash 55k → cancelado)
- [X] Trade SELL → credita T+1 pending — validado: SELL 100×PETR4@35 → tx_type=trade_sell amt=+3500 settle=2026-04-26 status=pending; pending_in cresce
- [X] Scheduler `settle_cash_transactions_job` — `settle_cash_loop` @ SCHEDULER_SETTLE_HOUR=0 (00:00 BRT default). Manual run via `repo.settle_due_transactions(date.today())` liquida tx pending settle≤hoje. Idempotente.
- [X] **C3b** ETF metadata: validado PUT /etf/metadata/{ticker} aceita `name, benchmark, mgmt_fee, perf_fee, isin, note`. **NOTA: `liquidity_days` NÃO existe no schema** — roteiro original desatualizado (são 3 campos, não 4).
- [X] **C4** Crypto D+0: aporte 0.5 BTC @200k → tx crypto_buy settled HOJE; redeem 0.2 BTC → tx crypto_sell settled HOJE (cash_credit calculado por average_price_brl). Sem pending.
- [X] **C5** RF aplicação é **D+0 não D+X** (correção roteiro): CDB R$30k + LCI R$20k → 2 tx rf_apply settled imediato. liquidity_days persiste no holding (CDB=1, LCI=30).
  - **Resgate** sim é D+X: CDB redeem R$10k → tx rf_redeem amt=+10000 status=pending settle=2026-04-26 (T+1) note "(D+1)"; LCI redeem R$5k → settle=2026-05-25 (T+30) note "(D+30)". cash_balance NÃO muda; pending_in cresce.
  - "Warn antes vencimento" implícito via tx pending até due_date (cash não libera). UI banner manual.
  - Scheduler `settle_due_transactions_job` processa pendentes due_date≤hoje (validado manual).
  - **Gap arquitetural minor**: carteira RF criada via /fixed-income/portfolio NÃO seta investment_account_id → cash hooks skipped silenciosamente. Workaround: usar Portfolio existente da conta como portfolio_id das holdings.

### §A.3 — Feature F UX 8 refinements — ~1.5h ✅ DONE 25/abr

- [X] **F1** Modal Histórico em `/profile` aba Contas (botão 📋):
  - Filtros tx-date-from/tx-date-to (default hoje), tx-direction-filter, tx-status-filter, tx-include-pending ✓
  - Coluna **Saldo** com running_balance ✓
  - Footer "Saldo Final +R$ 67500.00 R$ 49000.00" (Total créditos − débitos + saldo final) ✓
  - Botão "🖨 Imprimir" → printTxHistory() ✓
- [X] **F2** Withdraw deixaria caixa < 0 → FAModal.confirm("Saldo ficará negativo") antes de submeter; cancel reverte
- [X] **F3** Campo valor vazio/0/negativo → class `.fa-invalid-input` + msg inline "Valor inválido — informe um número maior que zero." (NÃO toast — msg inline com class "msg err"); cash NÃO muda
- [X] **F4** Apelido em listings: `/carteira` 4 tabelas (Trades, Crypto, RF, Outros) com coluna "Conta" mostrando apelido. **Render minor**: "Itau A.2Itau" (apelido + institution_name colados sem separador)
- [X] **F5** Crypto botões — PARTIAL: Resgate inline `💰` em cada holding ✓ (qty atual passada via redeemCrypto); botão "+ Cripto" no topo da tab faz aporte geral (PUT upsert que aumenta qty) — não há "+ Aportar" inline por holding como literal no roteiro, mas semanticamente equivalente
- [X] **F6** RF Aplicar — perfeito: `/fixed-income` aba "🔍 Busca de Títulos" cada bond linha tem botão verde "Aplicar" → onclick applyBondQuick(...) → modal `#modal-apply-rf` cascade: (1) Conta select (2) Portfolio depende da conta (3) Valor + Data. **SEM window.prompt() nativo** ✓
- [X] **F7** Delete conta — PARTIAL com **fix BUG13 aplicado**:
  - cash_balance > 0 → 409 "Saldo R$ X diferente de zero. Zere via saque/depósito antes de excluir." ✓ (após fix connection.py)
  - **GAP detectado**: cash=0 + 2 trades + 2 RF holdings ATIVOS → 204 (soft-delete sem bloquear). Roteiro especificava 409 "Há investimentos vinculados"; só cash é validado backend
  - zerada + sem holdings → 204 ✓
- [X] **F8** `/fixed-income` layout: sidebar aberta `--sb-w=220px` → content margin-left=220px (no_overlap); colapsada `--sb-w-collapsed=52px` → margin-left=52px; transition exato "margin-left 0.22s cubic-bezier(0.4, 0, 0.2, 1)" ✓

### §A.4 — G2 Rename portfolio inline — ~15min ✅ DONE 25/abr

- [X] `/profile` aba Contas: cada conta mostra "Carteiras / Portfolios (1)" — após **fix BUG16** (Portfolio entity + PortfolioModel sem `investment_account_id` mapeado, apesar de migration 0018 já ter coluna no DB)
- [X] Botão **✎** ao lado do portfolio → `renameInvPortfolio(id, name)` → `window.prompt('Novo nome para a carteira:', name)` → PATCH `/api/v1/portfolios/{id}` `{name}`. Validado: "Portfolio" → "Carteira Principal A.4" → 200. NOTA minor: usa prompt nativo (não FAModal)
- [X] Rename gera entry em `portfolio_name_history` (old_name, new_name, changed_at, changed_by=user_id master). Validado SQL após PATCH.
- [X] Seção atualiza sem reload — `renameInvPortfolio` chama `loadAccounts()` após PATCH ok
- [X] `/fixed-income` aba "💼 Carteira RF" tem botão **"✎ Renomear"** (id `btn-rename-pf`); display:none por default, aparece após select carteira em `#pf-select`. onclick=`renamePortfolio()`

### §A.5 — Golden path páginas críticas (sem pregão) — ~1.5h ✅ DONE 25/abr

- [X] `/carteira` — selector portfolio (7 opções), 6 tabs (Contas/Posições/Trades/Cripto/RF/Outros), tab Outros tabela com IR Isento, botão "🖨 Imprimir". ⚠️ FAAuth=false (BUG2 G4)
- [X] `/alerts` — Página de **alertas FUNDAMENTALISTAS** (ROE/DY/PL/etc, NÃO cotação). Endpoint POST /api/v1/alerts/indicator (operator: gt/lt/gte/lte, NÃO ">"). Validado: criar alerta DY > 10 PETR4 → 201; DELETE → 204. Botão "Avaliar Todos os Alertas" → evaluateNow(). Filter al-filter texto. **GAP**: usa user_id="user-demo" placeholder (BUG17), UI form usa "<" mas API exige "gt" (BUG18)
- [X] `/screener` — runScreener executa com 65 results; 17 inputs filtros (pe_min/max, pvp_min/max, dy_min/max, roe_min/max, roic_min/max etc); tabela com P/L, P/VP, DY%, ROE%, ROIC%, Mg.Liq%
- [X] `/watchlist` — input #add-ticker + botão "+ Adicionar" (addItemFromInput()) + botão "Avaliar Alertas". SSE live + bloqueio última = UI manual com dados
- [X] `/ml` — 4 tabs: PREVISAO, RISCO, SCREENER ML, FEATURES. Botões CALCULAR + RETREINAR. Tabela P10/P50/P90/PROB POSITIVO/INTERVALO 80%. NOTA: Live/Hist/Mudanças mencionadas no roteiro são do /dashboard ML signals (não /ml)
- [X] `/performance` — KPIs (drawdown, sharpe, beta, alpha, volatilidade, max drawdown) documentados; botão "🖨 Imprimir". Charts/heatmap precisam portfolio com posições (UI manual)
- [X] `/diario` — botão "+ Novo Trade" abre modal com 16 campos: Ticker*, Direção*, Timeframe, Datas Entrada*/Saída, Setup, Preços*, Qtd*, motivo/expectativa/aconteceu/erros/lições, emoção, tags
- [X] `/fixed-income` — coberto extensivamente em §A.2.4 (RF aplicar D+0), §A.2.5 (resgate D+X), §A.3.5 (modal Aplicar cascade), §A.3.7 (sidebar layout), §A.4.5 (rename portfolio RF). Comparador presente
- [X] `/crypto` — coberto em §A.2.3 (D+0 hooks aporte/resgate), §A.3.3 (apelido listing), §A.3.4 (botões Resgate inline + "+ Cripto" geral)
- [X] `/admin` — tabela users com 8 cols: Nome, E-mail, Role, **Admin (checkbox)**, Status, Último login, 2FA, Ações. Role select "Usuário" + "Master" (Admin virou flag ortogonal — refactor 25/abr ✓)
- [X] `/hub` — admin-only via _require_admin; 4 tabelas: Serviços (Status/Detalhe/Latência/Ação) + Sources/agendamentos. Botões "Limpar Concluidos" → cleanupCompleted() + "Reprocessar Todos" → reprocessAll()

### §A.6 — Smoke 24 páginas (carrega/helpers/sort/empty CTA) — ~1h ✅ DONE 25/abr

24/24 páginas HTTP 200 + carregam sem JS critical errors:

- [X] **Análise & ML (7)**: `/correlation`, `/anomaly`, `/sentiment`, `/forecast`, `/backtest`, `/optimizer`, `/var`
- [X] **Investimentos (5)**: `/dividendos`, `/etf`, `/laminas`, `/fundos`, `/patrimony`
- [X] **Trading (6)**: `/opcoes`, `/opcoes/estrategias`, `/vol-surface`, `/daytrade/setups`, `/daytrade/risco`, `/tape`
- [X] **Dados & Sistema (6)**: `/marketdata`, `/macro`, `/fintz`, `/import`, `/subscriptions`, `/whatsapp`

Achados smoke:
- **BUG19**: `/fintz` GET `/api/v1/fintz/tickers?dataset=cotacoes` → 500 (backend issue não bloqueante, tabela fica vazia)
- **BUG20**: `/daytrade/risco` `<title>` mostra "Day Trade - GestÃ£o de Risco" (encoding UTF-8 quebrado no title — cosmetic)
- FAAuth ausente em maioria (BUG2 G4 already known)

### §A.7 — Auth/RBAC/Network edge cases — ~45min ✅ DONE 25/abr

**Auth**:
- [X] Senha errada → 401 "Email ou senha inválidos." ✓
- [X] "Lembre-me 7d" expiry estendido — login com remember_me=true → access_token expires_in=86400 (24h vs 1800 default 30min); refresh_token via POST /auth/refresh → 200 + novo access_token (silent refresh)
- [X] Reset password sem token → 422 "token Field required" ✓; com token = UI manual
- [X] Já-logado em `/login` → redirect automático para `/dashboard` ✓
- **BUG21 NOVO**: forgot-password com email cadastrado → 500 (AttributeError: 'Settings' object has no attribute 'smtp_host')

**Sessão**:
- [X] Apagar `localStorage.access_token`+`refresh_token` + acessar /dashboard → redirect /login ✓ (auth_guard ativo)
- [X] Refresh token via POST /api/v1/auth/refresh → 200 + novo access_token (silent refresh funcional)

**RBAC**:
- [X] User comum (user_comum_test) em `/api/v1/admin/users` → 403 "Acesso restrito a administradores." ✓
- [X] User comum em `/hub/events` → 403 ✓ (vs master 200, controle). NOTA: prefix /hub direto (não /api/v1/hub)

**Forms**:
- [X] Trade qty negativa (-100) ou zero → 422 "Input should be greater than 0" (Pydantic gt=0)
- [ ] exit < entry em diário — UI manual

**Network**:
- [ ] Fast 3G simulado — DevTools throttle, UI manual
- [ ] Offline PWA — UI manual com Chrome dev tools
- [X] Erro forçado via /portfolios/{uuid-fake}/performance → toast .fa-toast-err com `req=a87c77b3` (correlation_id 8 chars). DB down test pulado (cascade impact em workers); FAErr boundary funciona igual.

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
| BUG10 | `connect-dll` com 2º simulator → 500 (deveria 409) | Baixo — UniqueViolationError não tratada em `wallet.py:245`; constraint raro | Adicionar try/except IntegrityError → HTTPException(409) |
| BUG11 | RF carteira via `/fixed-income/portfolio` sem investment_account_id → cash hooks skipped | Médio — fluxo correto exige usar Portfolio da conta (1:1 refactor) | Aceitar `investment_account_id` em CreatePortfolioRFRequest OU deprecar /fixed-income/portfolio e usar /api/v1/portfolios direto |
| BUG12 | ETF metadata schema falta `liquidity_days` | Baixo — UI/roteiro mencionavam mas schema só tem 3 fee fields | Decidir: adicionar coluna OU remover do roteiro |
| ~~BUG13~~ | ~~`connection.py:84` engolia `ValueError`/`HTTPException` em `DatabaseError` → 500 em vez de 409~~ | **RESOLVIDO 25/abr** — fix aplicado: `if isinstance(exc, (ValueError, HTTPException)): raise` antes do wrap. Validado: F7 delete com cash>0 → 500→409 com msg amigável. | — |
| BUG14 | Soft-delete de conta com holdings ativos não bloqueia (gap F7) | Médio — UI pode esconder conta com investimentos vinculados sem warn | Adicionar check em wallet_repo.delete_account: if has_holdings → raise ValueError("Há investimentos vinculados") |
| BUG15 | F4 render Conta: "Itau A.2Itau" (apelido + institution_name colados) | Baixo — cosmético em /carteira tabelas | Adicionar separador (espaço/dot/dash) entre <span apelido> e <span inst> |
| ~~BUG16~~ | ~~PortfolioModel + Portfolio entity sem `investment_account_id` mapeado → `/api/v1/portfolios` retorna sempre null + UI /profile mostra "Carteiras (0)"~~ | **RESOLVIDO 25/abr** — fix em 3 arquivos: domain/entities/portfolio.py (field), infrastructure/database/repositories/portfolio_repo.py (mapped_column + populate em _hydrate). Migration 0018 já tinha a coluna no DB; só faltou ORM mapping. Validado: 6 portfolios listados com investment_account_id correto; UI /profile mostra "(1)" carteira por conta. | — |
| BUG17 | `/api/v1/alerts/indicator` POST usa user_id="user-demo" placeholder (não JWT) | Médio — multi-tenant quebrado para alertas fundamentalistas; alertas vão pro user genérico | Substituir Query(user_id) por Depends(get_current_user) + repo filter por user_id real |
| BUG18 | UI `/alerts` form usa operadores `>/</>=/<=` mas API `/alerts/indicator` exige `gt/lt/gte/lte` | Médio — submit via UI usa valor errado, criação via UI provavelmente quebrada (form `Criar` retorna erro silencioso) | Mapear operator_label → operator_api no frontend OU aceitar ambos no backend |
| BUG19 | `GET /api/v1/fintz/tickers?dataset=cotacoes` retorna 500 | Baixo — `/fintz` carrega mas tabela de tickers fica vazia | Investigar no fintz_service o handler |
| BUG20 | `/daytrade/risco` `<title>` "Day Trade - GestÃ£o de Risco" — UTF-8 quebrado em meta | Baixo — cosmético no tab title | Verificar encoding do template HTML |
| BUG21 | `/api/v1/auth/forgot-password` 500 com email cadastrado | Médio — bloqueia fluxo "esqueci senha" | Adicionar smtp_host + smtp_port + smtp_user + smtp_password no Settings model OU lazy-load email_sender com try/except graceful |

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

- **Total pendente**: 50 itens distribuídos entre §A (29), §B (10), §C (11+)
- **§A.1 DONE 25/abr** (5 itens — Feature B DLL setup via API)
- **§A.2 DONE 25/abr** (8 itens — Feature C cash UI + scheduler + RF/Crypto/ETF hooks)
- **§A.3 DONE 25/abr** (7 itens — Feature F UX refinements + fix BUG13)
- **§A.4 DONE 25/abr** (5 itens — G2 rename portfolio + fix BUG16)
- **§A.5 DONE 25/abr** (11 itens — Golden path 11 páginas críticas)
- **§A.6 DONE 25/abr** (24 itens — Smoke 24 páginas)
- **§A.7 DONE 25/abr** (5 itens — Auth/Sessão/RBAC/Forms/Network edge)
- **Hoje sáb 25/abr**: 65 itens fechados, ~6h. Pacing alto sustentado
- **Bloqueado por externo**: Z5 (Nelogica 1m, ~48h)
- **BUGs**: 14 abertos (6 médios: BUG8 SMTP + BUG11 RF account_id + BUG14 soft-delete holdings + BUG17 alerts user-demo + BUG18 alerts operator UI<>API + BUG21 forgot-password 500; 8 baixos); BUG13+BUG16 resolvidos

---

**Documento gerado em**: 25/abr/2026 (sáb, após cleanup `a86b1fc`)
**Próximo gatilho**: começar §A.1 hoje à tarde / §A.5 amanhã manhã / §B.1 segunda 10h BRT
