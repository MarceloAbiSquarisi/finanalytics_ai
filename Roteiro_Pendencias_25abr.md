# FinAnalytics AI — Pendências consolidadas

> **Data**: 26/abr/2026 (dom madrugada, após super sessão de 9h)
> **Base**: pós-cleanup `a86b1fc` + **48 commits** acumulados no fds (último: `4e3cdee` i18n final)
> **Restantes**: ~22 itens `[ ]` + **5 BUGs abertos** (12 resolvidos) + OCO Phase C agendada terça
> **Login**: marceloabisquarisi@gmail.com / admin123 (master)

---

## 🛑 Ponto de parada — sáb 25/abr 23h+

**Última sessão fechou** (~12h trabalho — manhã+tarde+noite):
- ✅ Etapas A backend automated + B Playwright helpers (24 itens)
- ✅ §A.1-§A.7 + §A.9 + §A.10 estrutural + §A.11 (49 itens)
- ✅ 7 BUGs fixados: BUG7, BUG13, BUG16, BUG10, BUG14, BUG18, BUG21
- ✅ Cleanup raiz (50 files, -12834 linhas) commit `a86b1fc`
- ✅ §C.1 C6 Dividendos **Fase 1/5 done**: backend `DividendImportService` + endpoints preview/commit (commit `7cb27c6`)
- ✅ Cleanup state DB final: 1 user ativo, 2 contas legítimas, 0 alerts teste

**Sessão noite 25/abr** (~4.5h):
- ✅ **Dashboard chart fixes**: outlier filter (|close*100 - ref| < |close - ref| per-bar), price line dashed cyan, vertical bar removal — 3 layers (frontend + backend SQL CTE last_valid + migration)
- ✅ **OHLC scale migration**: `scripts/fix_ohlc_scale.py` corrigiu 3.4M bars × 100 em ohlc_1m (135 tickers afetados, penny stocks legítimos preservados via per-bar comparison)
- ✅ **Mojibake fix**: `scripts/fix_mojibake.py` corrigiu 81 substituições em 21 HTML files (commit `4c13cd0`)
- ✅ **/fixed-income** legacy `<nav>` removido (overlapping sidebar) commit `194c787`
- ✅ **Clocks widget** no dashboard: hora atual + countdown candle (depende do interval) + countdown pregão (font 13px)
- ✅ **Candle counter** abaixo do mínimo: intercalado (1, _, 3, _, 5, _ ...), reset diário
- ✅ **/overview novo dashboard**: 4 fontes (positions/watchlist/crypto/RF) progressive render + tabs/filtros + sparklines SVG inline + ML signal badge — commits `c6c0f02`, `34729a3`, `3167f68`
- ✅ **/overview ML via signal_history**: substituiu batch /signals (5min) por SELECT em signal_history (<100ms), auto-load + cache 5min — commit `3167f68`
- ✅ **/overview P/L + 🛡 SL badge** por card — commit `9508f49`
- ✅ **/carteira tabela Posições** ganhou colunas Atual + P/L + SL (mesma lógica) — commit `936d540`
- ✅ **Design_OCO_Trailing_Splits.md**: spec 382 linhas, 4 fases A/B/C/D, 6 decisões pendentes — commit `4ea9dcb`

**Onde retomar** (próximas sessões):

### A. Curto prazo — finalizar §A
- [ ] **§A.8 Pushover** (~30min) — precisa **celular ligado com app Pushover** + você presente
- [ ] **§A.10 restart real** (~30min) — restart end-to-end (FASudo prompt → senha → POST → os._exit → NSSM auto-restart) — precisa você presente

### B. Segunda 27/abr (pregão 10h-18h BRT)
- [ ] **§B.1-B.3** (~2h15min) — DLL viva: cancel order individual, cotação live profit_agent, OCO, indicadores tick-dependent, reconcile real-time

### C. Sessões dedicadas
- [ ] **§C.1 C6 Dividendos Fases 2-5** (~3h) — UI /import dividendos + UI /movimentacoes + reconciliação manual + tests BTG/XP samples
  - Fase 1 (backend) ✅ done — endpoints `POST /api/v1/import/dividends/{preview, commit}` funcionando
  - Fase 2: UI `/import` botão "Importar Dividendos" → upload + preview modal + confirm (~45min)
  - Fase 3: UI `/movimentacoes` nova rota com filtros ticker/portfolio/direção/período (~60min)
  - Fase 4: Reconciliação manual de unmatched (~45min)
  - Fase 5: Tests com samples reais BTG/XP (~30min)
- [ ] **§C.2 Tech debt** (variado): G4 auth refactor 21 páginas, G6 i18n spread, BUG8 SMTP backup, light mode cleanup, Etapa 2 portfolio refactor
- [ ] **§C.3 BUGs restantes** (10): 3 médios (BUG8/11/17) + 7 baixos (BUG2/3/4/5/6/12/15/19/20)
- [ ] **Z5** Nelogica 1m bars (bloqueado externo, ~48h após pedido)

### D. Outras funcionalidades — backlog

**OCO + Trailing + Splits parciais** (spec em `Design_OCO_Trailing_Splits.md`):
- [X] **OCO Phase A** — attach OCO em ordem pendente (commit `90adb01` 26/abr) — backend + UI deployed; teste end-to-end em §B.4
- [X] **OCO Phase B** — UI splits parciais N níveis (commit `443acb6` 26/abr) — modal dinâmico add/remove level; teste em §B.4 letra B
- [ ] **OCO Phase C** — Trailing stop (codar + testar segunda 27/abr — agendado em §B.5)
- [X] **OCO Phase D** — Persistence + restart safety (commit `f2c60a7` 26/abr) — `_load_oco_state_from_db` + endpoint `/oco/state/reload`; validado live `groups_loaded:0`

**G6 i18n spread** (DONE 26/abr — 42 páginas, 159 chaves):
- [X] Páginas core (5): /carteira, /movimentacoes, /alerts, /dashboard, /overview
- [X] Pesquisa (3): /watchlist, /screener, /import
- [X] Análise & ML (5): /macro, /forecast, /performance, /fundamental, /diario
- [X] Análise técnica (5): /backtest, /correlation, /anomaly, /etf, /laminas
- [X] Sistema (5): /admin, /hub, /patrimony, /vol-surface, /var
- [X] Trading (5): /sentiment, /optimizer, /tape, /opcoes, /opcoes-estrategias
- [X] Dados (5): /daytrade-setups, /daytrade-risco, /marketdata, /fintz, /subscriptions
- [X] Investimentos (5): /dividendos, /fundos, /whatsapp, /pnl, /ml
- [X] Auth/Profile (4): /profile, /tickers, /portfolios, /login
- [X] Skip dynamic JS render (intencional — out of scope)

**G4 auth refactor** (DONE 26/abr — 13/14 páginas):
- [X] Piloto: /watchlist (`168f977`)
- [X] Batch via `scripts/refactor_inline_auth.py` (12 páginas, `bba4fbc`): anomaly, backtest, correlation, diario, etf, fixed_income (3 IIFEs), forecast, laminas, macro, patrimony, performance, screener
- [ ] **dashboard.html restante** — manual (lógica auth própria, exige cuidado)

**ML / Multi-horizon** (depende de Z5 — Nelogica 1m):
- [ ] Treinar pickles h3, h5 + h21 por ticker (multi-horizon real)
- [ ] `/api/v1/ml/predict_ensemble` ganha utilidade real (hoje só agrega h21 sozinho)
- [ ] Avaliar features extras por classe de ativo (futuros: book imbalance/tape; ações: fundamentus)

**UX expansões**:
- [X] /overview: botão per-card "↻ live recalc" via /predict_mvp/{ticker} (`9f55967` 26/abr)
- [ ] /overview: badge SL também para crypto (precisa endpoint de orders crypto — não existe hoje)
- [ ] Tabs /carteira: replicar P/L+SL nas tabs Trades, Outros (Crypto já feito)

**ML / Multi-horizon** (depende de Z5 — Nelogica 1m):
- [ ] Treinar pickles h3, h5 + h21 por ticker (multi-horizon real)
- [ ] `/api/v1/ml/predict_ensemble` ganha utilidade real (hoje só agrega h21 sozinho)
- [ ] Avaliar features extras por classe de ativo (futuros: book imbalance/tape; ações: fundamentus)

**UX expansões**:
- [ ] /overview: botão per-card "↻ live recalc" via /predict_mvp/{ticker} (~4s, helper `recalcLiveSignal` já existe)
- [ ] /overview: badge SL também para crypto (precisa endpoint de orders crypto — não existe hoje)
- [ ] Tabs /carteira: replicar P/L+SL nas tabs Trades, Cripto, Outros

---

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

### §A.9 — Profit Tickers UI — ~30min ✅ DONE 25/abr

- [X] `/profit-tickers` filtros persistem em `localStorage[fa_profit_tickers_filter]` — validado: "PETR" persiste, rows reduzem para PETR3+PETR4
- [X] Bulk activate: botão "+ Ativar próximos 10 inativos" → activateBulk(10). Cada row tem botão "Desativar" individual (toggleTicker)
- [X] Badge estados implementados (validado os 2 visíveis sábado):
  - ⌛ **Aguardando feed** ✓ (371 rows hoje)
  - ○ **Inativo** ✓ (2 rows hoje)
  - 🟢 **Coleta Ativa** + 🔴 **Falha DLL** — só com pregão (segunda 27/abr)
- [ ] Tooltip em cada badge — UI manual hover
- [ ] Colunas renomeadas — Headers atuais: Ticker, Exchange, Status Coleta, Notas, Acao (sem comparativo histórico)
- [X] Bulk top500: 374 rows cadastradas (próximo de 500); summary "0 coletando · 371 aguardando feed · 2 inativos"

### §A.10 — Sudo + Profit Agent restart — ~45min ✅ DONE 25/abr (estrutural)

> Sensível host Windows — restart NÃO disparado (validação estrutural sem efeito real).

**FASudo**:
- [X] `FASudo.{confirm, fetch, fetchJson, reset}` existe em static/sudo.js — validado em /hub. **GAP: sudo.js só carrega em /hub.html** (outras páginas com ações destrutivas como /admin perdem este wrapper)
- [X] 401 + header `X-Sudo-Required: true` retornado pelo backend quando `require_sudo` dependency não autoriza ✓
- [X] `FASudo.fetchJson` parseado com FAErr (combina FASudo.fetch + parse JSON)

**Restart Profit Agent**:
- [X] Endpoint `POST /api/v1/agent/restart` existe (agent.py:375) com `require_sudo` dependency. SEM sudo token → 401 + `X-Sudo-Required: true` + body `{"detail": "Sudo confirmation required."}`. profit_agent.py:4715 implementa /restart via HTTP. **NÃO disparado** (requer você presente).
- [ ] Health `:8002/health` volta em <10s após restart — UI manual com você presente
- [ ] Conta DLL re-conectada automaticamente — UI manual

**Auto-reconnect**:
- [ ] `finanalytics_timescale` down 20min — UI manual (cleanup risk se cascade em workers)
- [X] Log throttled: TICK_V1 callback error usa contador (count=21001, 22001, 23001 — 1 log a cada 1000 events). Sprint Backend V1 implementado.

### §A.11 — Etapa B residuais — ~30min ✅ DONE 25/abr

- [X] PWA install criteria: 8/8 atendidos — manifest válido (name/icons/start_url/display=standalone), SW registered+active, localhost (https-equivalent). Oferta "Instalar app" do Chrome/Edge é UI nativa (não testável headless mas pre-condições 100%)
- [X] FAPrint UI: botão "🖨 Imprimir" presente em /carteira, /performance, /dividendos, /profile (substitui /portfolios via redirect). FAPrint.print() infra já validada B14 (window.print + body[data-print-date] + @media print)
- [X] FACharts: Chart.js 4.4.1 lazy-loaded; FACharts.{apply,opts,palette,load} disponíveis. Canvases:
  - /backtest: 5 canvases (charts carregados)
  - /correlation: 1 canvas (heatmap)
  - /performance: 0 canvas (sem portfolio com dados)
  Tooltip + legenda + cores consistentes precisam UI manual com dados reais

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

### §B.4 — OCO Phase A+B+D end-to-end (NOVO 26/abr) — ~1h

> Backend deployado + profit_agent reiniciado às 12h33 BRT 26/abr; rotas `/oco/*` respondem 200; DB vazio. Falta **disparar com ordem real**.

**A) Attach OCO 1 nível (smoke)**:
- [ ] Limit BUY PETR4 100 @ R$30 (longe pra ficar pending) → enviar
- [ ] Na lista "Abertas" da aba Ordens, clicar **🛡** (botão azul) na ordem pending
- [ ] Modal abre: parent info, 1 level com qty=100, TP+SL marcados
- [ ] Preencher TP=52, SL trigger=28, SL limit=27.50 → "Anexar OCO"
- [ ] Toast "OCO anexado · group XXXXXX · 1 nível(eis) · disparará ao fill"
- [ ] DB: `SELECT status, parent_order_id FROM profit_oco_groups` → 1 row status=`awaiting`
- [ ] `/api/v1/agent/oco/groups` → 1 group; `/oco/groups/{group_id}` → mostra parent + 1 level

**B) Splits parciais (NEW UI)**:
- [ ] Cancelar a ordem do passo A
- [ ] Limit BUY VALE3 100 @ valor longe → pending
- [ ] 🛡 OCO → modal abre com 1 nível pré-preenchido qty=100
- [ ] Click "+ nível" → 2º nível aparece com qty=0 (sugestão)
- [ ] Editar nível 1 qty=60, nível 2 qty=40
- [ ] TP1=72, SL1=58 ; TP2=75, SL2=58 → "Anexar OCO"
- [ ] Toast "2 níveis"; DB: `SELECT level_idx, qty, tp_price, sl_trigger FROM profit_oco_levels WHERE group_id='...' ORDER BY level_idx` → 2 rows {idx=1,qty=60,tp=72,sl=58},{idx=2,qty=40,tp=75,sl=58}
- [ ] Validação: tentar enviar com sum(qty) ≠ 100 → modal mostra mensagem `Soma das qty (X) deve bater parent.qty (100).`
- [ ] Validação: nível sem TP nem SL marcado → `Nível N: marque ao menos TP ou SL.`

**C) Parent fill → dispatch automático**:
- [ ] Reduzir preço da ordem mãe pra perto do mercado (ou cancelar e enviar nova @ preço de fill)
- [ ] Aguardar fill (callback assíncrono)
- [ ] `/api/v1/agent/oco/groups/{group_id}` → status=`active` ou `partial`; cada level com `tp_order_id` e/ou `sl_order_id` populados
- [ ] Aba Ordens mostra TP (LMT sell) e SL (STP sell) novas geradas pelo dispatch
- [ ] Log do profit_agent: `oco_group.dispatched group=... filled=N/M levels=K`

**D) Cross-cancel (uma perna fillou → cancela outra)**:
- [ ] Mover preço de mercado pra cima do TP1 do nível 1 (ou ajustar TP pra perto do mercado)
- [ ] Quando TP1 executa: log `oco.tp_filled→sl_cancel group=... lv=1`; level 1 SL fica status=`cancelled`
- [ ] Group continua status=`partial` enquanto níveis restantes ativos
- [ ] Repetir até último nível → group=`completed`, completed_at populado

**E) Persistence (Phase D)**:
- [ ] Com 1+ group active no DB, parar profit_agent (Get-Process | Stop-Process — admin necessário)
- [ ] Subir novo: `Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "src\finanalytics_ai\workers\profit_agent.py" -WindowStyle Hidden -RedirectStandardOutput ".profit_agent.log"`
- [ ] Log inicial deve conter: `oco.state_loaded groups=N levels=M order_index=K`
- [ ] `/api/v1/agent/oco/groups` retorna mesmos groups com mesmo status (in-memory restaurado)
- [ ] Sem regressão: cross-cancel continua funcionando após restart

**F) Cancel manual de group**:
- [ ] Group active → `POST /api/v1/agent/oco/groups/{group_id}/cancel`
- [ ] Resposta: `{ok:true, cancelled_orders:N}` (N = TP+SL pendentes)
- [ ] DB: status=`cancelled`, `completed_at` setado
- [ ] Aba Ordens: TP e SL daquele group ficam status CANCELED

### §B.5 — OCO Phase C (Trailing) — ~2-3h (CODA + TESTA)

> Backend ainda **não codado** — implementar e testar no mesmo dia, requer pregão pra validar trailing real.

- [ ] Codar `_trail_monitor_loop` que roda a cada N segundos: pra cada level com `is_trailing=true` e SL aberto, busca last_price do `_book` (in-memory) e atualiza `trail_high_water` se favorável
- [ ] Quando `trail_high_water - trail_distance > sl_trigger atual` (sell long) → chamar `change_order` (SendChangeOrderV2) com novo stop
- [ ] Decisão 1: aceitar `trail_distance` (R$) OU `trail_pct` (% do high_water) — payload tem ambos campos opcionais
- [ ] Decisão 6: se ao criar trailing já estiver além do trigger inicial → enviar market do lado oposto imediato + log `trailing.immediate_trigger`
- [ ] UI dashboard.html: checkbox "Trailing" no level + radio R$/% + input distance — só ativa quando checkbox marcado
- [ ] Validação UI: trailing só faz sentido se SL marcado; se SL desmarcado, oculta opções de trailing
- [ ] Smoke: criar level com trailing R$ 0,50, mover preço de mercado +R$ 1 → SL deve ter sido `change_order`-ado pra (last - 0.50)
- [ ] Smoke %: trailing 1.5%, mover +2% → SL move proporcionalmente
- [ ] Imediato: criar trailing com SL trigger acima do last (sell long) → ordem market disparada na hora; log gravado

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

### §C.1 — C6 Dividendos (Fase 1/5 done 25/abr) — ~3h restantes

**Fase 1 ✅ DONE 25/abr** (commit `7cb27c6`):
- [X] `DividendImportService` em `application/services/dividend_import_service.py`
- [X] Parser CSV (auto-detect delimiter + header) + OFX (regex em `<STMTTRN>`)
- [X] Detecção keywords: DIVIDENDOS RECEBIDOS, DIVIDENDO, JCP, JUROS SOBRE CAPITAL, RENDIMENTO
- [X] Extração ticker B3 (regex `[A-Z]{4,5}\d{1,2}`) + classificação tipo (dividendo/jcp/rendimento)
- [X] Match positions por ticker exato (matched/unmatched/ambiguous)
- [X] Endpoints `POST /api/v1/import/dividends/preview` + `/commit`
- [X] Idempotência via duplicate detection (data+amount+ticker)
- [X] Suporte BR (R$ 1.234,56) + US (R$ 234.50)
- [X] Validado com sample sintético: 4 linhas detectadas, 2 matched commit OK, cash_balance atualizou

**Fases 2-5 pendentes** (~3h):
- [ ] **Fase 2** UI /import (~45min): botão "Importar Dividendos" → upload + preview modal + confirm; mostra matched/unmatched/ambiguous + count
- [ ] **Fase 3** UI /movimentacoes (~60min): nova rota; tabela agregada todas account_transactions com filtros ticker/portfolio/direção (in/out)/período/tipo (dividend/trade/etc)
- [ ] **Fase 4** Reconciliação manual (~45min): linha unmatched → modal "Selecione ticker" → POST `/api/v1/wallet/transactions/{id}/reconcile` (precisa criar endpoint)
- [ ] **Fase 5** Tests (~30min): import sample CSV BTG + XP reais (precisa user fornecer samples)
- [ ] **Bonus** PDF support (deferred, ~1h): pdfplumber + heurísticas BTG/XP layouts

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
| ~~BUG10~~ | ~~`connect-dll` com 2º simulator → 500 (deveria 409)~~ | **RESOLVIDO 25/abr** — `wallet.py:254` adicionou `except Exception` detectando `ux_inv_accounts_one_dll_sim`/`duplicate key` → 409 com mensagem amigável "Já existe uma conta 'simulator' ativa no sistema. Desconecte-a primeiro via /disconnect-dll." | — |
| ~~BUG11~~ | ~~RF carteira via `/fixed-income/portfolio` sem investment_account_id → cash hooks skipped~~ | **RESOLVIDO 26/abr** — `CreatePortfolioRFRequest.investment_account_id` virou obrigatório (Field min_length=1). Service propaga ao `RFPortfolioRepository.create_portfolio` que seta no `PortfolioModel` mirror. Cash hooks rf_apply/rf_redeem agora encontram a conta dona. UI já usava `/api/v1/portfolios` desde Sprint UX C (21/abr) — backward-incompat só afeta scripts/curl que chamem o endpoint legado. Validado: 422 com mensagem clara sem o campo. | — |
| ~~BUG12~~ | ~~ETF metadata schema falta `liquidity_days`~~ | **RESOLVIDO 26/abr** — decisão: ETFs B3 liquidam D+2 padrão sem customização per-ETF (diferente de RF onde varia: CDB D+1, LCI D+30). Schema atual com 6 campos (name/benchmark/mgmt_fee/perf_fee/isin/note) está correto. Roteiro original assumia incorretamente analogia com RF. | — |
| ~~BUG13~~ | ~~`connection.py:84` engolia `ValueError`/`HTTPException` em `DatabaseError` → 500 em vez de 409~~ | **RESOLVIDO 25/abr** — fix aplicado: `if isinstance(exc, (ValueError, HTTPException)): raise` antes do wrap. Validado: F7 delete com cash>0 → 500→409 com msg amigável. | — |
| ~~BUG14~~ | ~~Soft-delete de conta com holdings ativos não bloqueia (gap F7)~~ | **RESOLVIDO 25/abr** — `wallet_repo.delete_account` adicionou query consolidada (trades + crypto_holdings + rf_holdings via JOIN portfolios + other_assets) → ValueError("Há investimentos vinculados (N: {detalhe})") → 409. Validado: trade 1×PETR4 → 409 com counts corretos. | — |
| BUG15 | F4 render Conta: "Itau A.2Itau" (apelido + institution_name colados) | Baixo — cosmético em /carteira tabelas | Adicionar separador (espaço/dot/dash) entre <span apelido> e <span inst> |
| ~~BUG16~~ | ~~PortfolioModel + Portfolio entity sem `investment_account_id` mapeado → `/api/v1/portfolios` retorna sempre null + UI /profile mostra "Carteiras (0)"~~ | **RESOLVIDO 25/abr** — fix em 3 arquivos: domain/entities/portfolio.py (field), infrastructure/database/repositories/portfolio_repo.py (mapped_column + populate em _hydrate). Migration 0018 já tinha a coluna no DB; só faltou ORM mapping. Validado: 6 portfolios listados com investment_account_id correto; UI /profile mostra "(1)" carteira por conta. | — |
| BUG17 | `/api/v1/alerts/indicator` POST usa user_id="user-demo" placeholder (não JWT) | Médio — multi-tenant quebrado para alertas fundamentalistas; alertas vão pro user genérico | Substituir Query(user_id) por Depends(get_current_user) + repo filter por user_id real |
| ~~BUG18~~ | ~~UI `/alerts` form usa operadores `>/</>=/<=` mas API `/alerts/indicator` exige `gt/lt/gte/lte`~~ | **RESOLVIDO 25/abr** — `indicator_alert_service.py:152` adicionou `_SYMBOL_TO_OP = {">": "gt", "<": "lt", ">=": "gte", "<=": "lte"}` antes da validação. Backend agora aceita ambos. Validado: POST com operator=">" → 201 com operator="gt" gravado. | — |
| ~~BUG19~~ | ~~`GET /api/v1/fintz/tickers?dataset=cotacoes` retorna 500~~ | **RESOLVIDO 26/abr** — `FintzRepo.list_tickers(dataset)` adicionado (faltava implementação; route chamava método inexistente). Suporta cotacoes/indicadores/itens via mapping table. Validado: 884 tickers retornados em cotacoes. | — |
| BUG20 | `/daytrade/risco` `<title>` "Day Trade - GestÃ£o de Risco" — UTF-8 quebrado em meta | Baixo — cosmético no tab title | Verificar encoding do template HTML |
| ~~BUG21~~ | ~~`/api/v1/auth/forgot-password` 500 com email cadastrado~~ | **RESOLVIDO 25/abr** — `auth.py:251` envolveu `get_email_sender()` em try/except graceful. Quando Settings sem smtp_host, log warning + retorna 200 com `dev_reset_url` (modo dev fallback). Validado: forgot-password com master email → 200 + dev_reset_url + dev_token. | — |

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

- **Total pendente**: ~22 itens em §A (2 — A.8+A.10 real), §B (16 incluindo §B.4 OCO + §B.5 Phase C trail), §C (4 — C6 Fase 5 com samples + bugs G4 dashboard + Z5)
- **§A.1-A.7 + A.9 + A.10 estrutural + A.11 DONE 25/abr** (49 itens)
- **§C.1 C6 Dividendos 4/5 fases DONE** (Fase 1 backend + Fase 2 UI /import + Fase 3 /movimentacoes + Fase 4 reconciliação manual; só Fase 5 testes com samples reais BTG/XP pendente)
- **Sessão noite 25/abr (4.5h add)**: chart fixes + OHLC migration 3.4M bars + mojibake 21 files + clocks/candle counter + /overview novo dashboard + /overview ML via signal_history + /overview P/L+SL + /carteira P/L+SL + OCO design spec
- **Sessão 26/abr (super sessão ~9h em 4 batches)**:
  - **Batch 1 OCO (3h)**: Phase A backend+UI (`90adb01`) + Phase B UI splits (`443acb6`) + Phase D persistence (`f2c60a7`) + profit_agent restartado live
  - **Batch 2 BUGs+features (3.5h, 9 commits)**: BUG15 separador, /overview live recalc per-card, /carteira P/L Crypto, BUG17 JWT, C6 Fase 2+3 UI, BUG12+BUG19 (`9f55967`→`ce1ce5a`); BUG11 RF account_id (`4f0a70d`)
  - **Batch 3 i18n+G4 (2h, 3 commits)**: G6 i18n /carteira+/movimentacoes+/alerts+/dashboard (`06abdc3`+`60cb600`); +/watchlist/screener/import + G4 piloto (`168f977`); G4 batch 12 páginas via script (`bba4fbc`)
  - **Batch 4 i18n spread massivo (1.5h, 6 commits)**: 25 páginas adicionais com data-i18n: macro/forecast/performance/fundamental/diario (`a4570a5`), backtest/correlation/anomaly/etf/laminas (`e0259b4`), admin/hub/patrimony/vol_surface/var (`0e77e0b`), sentiment/optimizer/tape/opcoes/opcoes_estrategias (`33c13a6`), dt_setups/dt_risco/marketdata/fintz/subscriptions (`6f7727b`), dividendos/fundos/whatsapp/pnl/ml (`953657a`), profile/tickers/portfolios/login (`a1dc965`+`4e3cdee`)
- **Total acumulado 25-26/abr**: ~28h, **48 commits**, **12 BUGs resolvidos**, **20+ features novas**, **~159 chaves i18n em 42 páginas** (cobertura ampla), **G4 13/14 páginas migradas** (só dashboard.html restante, complexo)
- **Bloqueado por externo**: Z5 (Nelogica 1m, ~48h)
- **Próximo gatilho**: segunda 27/abr 10h BRT — §B.1 (DLL viva) + §B.4 (OCO end-to-end) + §B.5 (Phase C Trailing — codar + testar)
- **BUGs**: 5 abertos (BUG8 SMTP backup; BUG2/3/4/5/6 baixos/intencionais); **12 resolvidos** (BUG7, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 21)

### Cleanup state (final do dia 25/abr 23h+):
- **Users**: 1 ativo (master `marceloabisquarisi@gmail.com`); user_comum_test desativado via PATCH /admin/users/{id}/active
- **Contas**: 2 ativas — Simulador Nelogica (DLL ativa, cash 0) + XP Teste 2 (cash 1000, dado real preservado). 9 contas teste já soft-deletadas
- **Portfolios**: 2 ativos (1 por conta, ambos "Portfolio")
- **Alerts**: 0 ativos user-demo (3 cancelled)
- **TX órfãs**: 34 (14 cancelled + 20 settled) vinculadas a contas inativas — histórico preservado
- **Containers**: 18 healthy
- **API**: ok
- **Pos-fix env**: connection.py + auth.py + wallet.py + indicator_alert_service.py + portfolio_repo.py todos com fixes deployed

---

**Documento gerado em**: 25/abr/2026 (sáb, após cleanup `a86b1fc`)
**Última atualização**: 26/abr/2026 madrugada (dom — após super sessão de 9h: OCO + 9 commits BUGs/features + 25 páginas i18n + G4 batch 13 páginas)
**Próximo gatilho**: segunda 27/abr 10h BRT pregão — §B.1 DLL viva + §B.4 OCO end-to-end (A+B+D) + §B.5 Phase C Trailing (codar+testar). Único pendente G4: dashboard.html (manual)
