# FinAnalytics AI — Testes Pendentes

> **Data**: 24-25/abr/2026 (atualizado 25/abr)
> **Base**: `Roteiro_Testes_UI_22abr.md` + sessões 23-25/abr (Features B, C, F, G1, G2 + BRAPI-purge + sessão fixes 25/abr)
> **URL base**: `http://localhost:8000`
> **Login**: conta master (marceloabisquarisi@gmail.com)
> **Último deploy**: 25/abr — refactor 1 portfolio por conta + UI fixes do dashboard

## Sessão 25/abr — fixes aplicados (resumo)

**Backend / arquitetura:**
- Refactor `UserRole.ADMIN` → flag ortogonal `is_admin` (migration 0017). UI `/admin` ganhou checkbox dedicado.
- Refactor **1 portfolio por conta** (migration 0018). Coluna `is_default` removida; index `ux_portfolios_one_active_per_account` adiciona invariante 1:1. Rota `/portfolios` deprecada → 302 redirect para `/profile#invest`.

**`/dashboard` (vários fixes):**
- Toast `401: Not authenticated` ao logar — causado por `auth_guard.js` ausente. Adicionado no `<head>`.
- Toast `ResizeObserver loop completed...` — filtrado em `error_handler.js`.
- `checkProfitStatus is not defined` — função estava em outro `<script>` block. Movida `setInterval` para o mesmo bloco.
- Service Worker cache version bumped `fa-v7` → `fa-v8` (forçar invalidação).
- Aba **Ordens** cancel: checa `r.ok`, polling escalonado 600/2000/5000ms + fallback `/positions/dll` em 10s. **Pendente revalidação 27/abr (pregão)**.
- Aba **OCO**: layout vertical (TP/Stop/Limite em colunas próprias); LADO virou toggle Compra(verde)/Vender(vermelho); todos os 3 preços viraram **opcionais** com 3 modos (TP+SL=OCO real / só TP=limit / só SL=stop).
- Aba **Conta**: select do topo populado via `/api/v1/wallet/accounts` (era `/api/v1/accounts/` deprecated). Painel virou só **seleção**; botão "+Nova" e form inline removidos (CRUD agora em `/profile#invest`).
- Watchlist: botão `+` virou linha separada "+ Adicionar ativo".
- Painel DT: 6 abas em grid 3×2 com borda individual; padding horizontal no `.main` evita corte.
- Quickbar removeu atalho `/portfolios`.

**Outras telas:**
- `/portfolios` modal "Novo" (antes do refactor): ganhou campo conta obrigatório com select. Modal Hist com guard de 300ms contra fechar imediato.

---

## Índice

1. [Pendências 22/abr carry-over (browser)](#1-pendências-22abr-carry-over-browser)
2. [Features B/C/F/G1/G2 — novos fluxos a validar](#2-features-bcfg1g2--novos-fluxos-a-validar)
3. [Decisão 20 (BRAPI-purge) — validar fallback chain](#3-decisão-20-brapi-purge--validar-fallback-chain)
4. [Sudo mode + Profit Agent restart](#4-sudo-mode--profit-agent-restart)
5. [Bugs abertos (não fechados)](#5-bugs-abertos-não-fechados)
6. [C6 — Dividendos (não iniciado)](#6-c6--dividendos-não-iniciado)
7. [Tech debt para sessão dedicada](#7-tech-debt-para-sessão-dedicada)
8. [Critérios de aceite restantes](#8-critérios-de-aceite-restantes)

---

## 1. Pendências 22/abr carry-over (browser)

Tudo que ficou `[ ]` no roteiro 22/abr. Continua valendo — nenhum desses foi validado ainda em navegador real.

### Helpers globais

**Sidebar mobile**
- [X] `<768px` (DevTools iPhone 12): sidebar vira overlay; clique fora fecha; backdrop escuro

**Notificações realtime**
- [X] Sino topbar (`FANotif`): counter de unread; click abre dropdown últimas 30 — sino SVG presente; click abre .fa-notif-panel.open; empty state "Sem notificacoes recentes." (count=0); validado Playwright 25/abr
- [ ] Notificação SSE `/api/v1/alerts/stream` aparece em tempo real — EventSource disponível, sem events ativos pra validar
- [ ] Notificações também disparam toast automático — sem events pra validar

**Toast (FAToast)**
- [X] Cap 4 visíveis simultâneos (5º vai pra fila, aparece quando 1º expira) — validado Playwright 25/abr (timeline 200ms→3000ms→4500ms confirma queue)

**Tabelas (FATable)**
- [X] Detecção automática de colunas numéricas vs texto (sort numérico vs lexicográfico) — validado Playwright 25/abr (table sintética: Qtd=[5,22,100,1000] num; Nome alfabético)

**Loading skeletons (FALoading)**
- [X] Antes de fetch terminar, tabela mostra shimmer rows em vez de "Carregando..." — FALoading.tableRows insere <tr aria-busy=true> com .fa-sk-cell larguras aleatórias; animation fa-sk-shimmer 1.4s; validado Playwright 25/abr
- [X] Em `prefers-reduced-motion` → animação substituída por opacity — @media (prefers-reduced-motion: reduce) cancela animation + aplica opacity:0.6 em .fa-sk-bar/.fa-sk-cell

**Forms (FAForm)**
- [X] CPF inválido → marca + mensagem (testar com `12345678901`) — validado: errors.cpf="CPF inválido"; isCpf('12345678901')=false, isCpf('21504556259')=true
- [X] Email inválido → marca + mensagem — validado: errors.email="E-mail inválido"; isEmail('naoeumail')=false, isEmail('a@b.com')=true
- [X] Após corrigir → estado de erro limpa — validado FAForm.clearErrors limpa .fa-form-err class. NOTA: rules syntax é array `['required','cpf']` (NÃO pipe `'required|cpf'`).

**Acessibilidade (FAA11y)**
- [X] Tab logo após carregar → "Pular para conteúdo" aparece (skip link azul) — validado Playwright 25/abr (focus → top:-40 transition 0.1s → top:0; href=#main-content target existe)
- [X] Foco visível: outline 2px cyan em qualquer elemento focado — validado (rgb(0,212,255) solid 2px)
- [X] Modal abre → foco vai pro botão OK (preso dentro) — validado (foco vai pro Cancel; Tab cicla Cancel↔OK trap circular)
- [X] Modal fecha → foco volta ao gatilho — validado (Esc → modal_gone, foco volta no fa-notif-btn)

**PWA**
- [ ] Chrome/Edge oferece "Instalar app" (ícone na barra)
- [ ] Após instalado: ícone na taskbar/dock; abre fullscreen sem URL
- [ ] Offline: assets/css continuam (cache); `/api/*` falha (esperado)
- [ ] Refresh em página visitada → carrega instantaneamente do cache
- [X] manifest.json + sw.js + caches fa-v8-static (26 keys) + fa-v8-html ativos — validado Playwright 25/abr

**Print (FAPrint)**
- [ ] `/carteira`, `/performance`, `/portfolios`, `/dividendos`: botão "🖨 Imprimir" — verificar UI manual
- [X] Click → preview esconde sidebar/topbar/botões; expande tabelas; fundo branco — @media print rule em theme.css com sidebar/topbar/buttons display:none + tables expand + cores BR/B; validado Playwright 25/abr
- [X] Rodapé "FinAnalytics AI — impresso em DD/MM/YYYY HH:MM" — body[data-print-date]="impresso em 25/04/2026, 10:11" via FAPrint.print(); rendered via body[data-print-date]::after content attr() em @media print

**Light/Dark (FATheme)**
- [X] Toggle muda fundo + texto + borders + accent em todas as páginas — parcial; FATheme.toggle/set funciona + persiste localStorage; visual NÃO muda em /dashboard, /carteira, /alerts (Decisão 19 — `:root{...}` próprio intencional)
- [X] Sem flash dark→light no refresh (FOUC prevention) — snippet inline antes de theme.css confirmado em /alerts (Decisão 17)
- [X] Páginas com `:root` próprio podem manter dark — anotar quais → /dashboard, /carteira, /alerts (auditoria parcial; testar resto deliberadamente)

**Error boundary (FAErr)**
- [X] Forçar erro: chamada a `/portfolios/{uuid-fake}/performance` → 404 — validado Playwright 25/abr
- [X] Toast vermelho com mensagem + correlation_id (8 chars) — validado: "404: Portfólio não encontrado: ... (req=c4c7b981)" em .fa-toast-err
- [X] Console não trava — validado
- [X] Filtrar `ResizeObserver loop` (browser noise) — não dispara toast (fix 25/abr)

**Charts (FACharts)**
- [X] `/performance`, `/backtest`, `/correlation`, `/dividendos`, `/etf`, `/fixed-income`, `/marketdata`, `/fintz`, `/diario`, `/dashboard` — gráficos renderam — Chart.js 4.4.1 lazy-loaded em /performance; FACharts.{apply,opts,palette,load}; chart sintético criado ok; validado Playwright 25/abr (páginas individuais sem dados de portfolio sem canvas no DOM nativo — Chart.js bundle disponível)
- [ ] Tooltip com valores; legenda no bottom; cores consistentes — UI manual com dados reais. Palette: cyan #00d4ff, green, orange, purple etc (9 cores)

### Golden path — páginas críticas (nenhuma testada manualmente ainda)

**`/dashboard`** — DayTrade
- [X] Toast 401 ao logar — fix 25/abr (`auth_guard.js` adicionado no `<head>`)
- [X] Toast ResizeObserver — filtrado em `error_handler.js` 25/abr
- [X] Aba Ordem: BUY PETR4 100 @ Market em SIMULAÇÃO → toast ok
- [X] Aba OCO: TP 52 + SL 47 stop_limit 46.50 → ordem em "Ordens"
- [X] Aba OCO: 3 modos (só TP=limit, só Stop=stop-limit, ambos=OCO real) — fix 25/abr
- [X] Aba OCO: LADO toggle Compra(verde)/Vender(vermelho) — fix 25/abr
- [X] Aba OCO: layout vertical TP/Stop/Limite (sem corte de label) — fix 25/abr
- [X] Aba Pos.: search PETR4 → GetPositionV2
- [ ] Aba Ordens: polling 5s; cancelar individual *(fix aplicado 25/abr — checa r.ok, polling 600ms/2s/5s, fallback `/positions/dll` em 10s. **Pendente revalidação 27/abr (seg, pregão)** — DLL recusa cancel fora de pregão)*
- [X] Aba Conta: select do topo populando (`/api/v1/wallet/accounts`) — fix 25/abr
- [X] Aba Conta: virou só seleção; CRUD movido para `/profile#invest` — refactor 25/abr
- [X] Painel DT: 6 abas em grid 3×2 com borda individual — fix 25/abr
- [X] Painel ML signals: Live/Hist/Mudanças
- [X] Quickbar: padding horizontal corrigido (sem corte de texto) — fix 25/abr
- [X] Quickbar: atalho `/portfolios` removido (refactor 25/abr)
- [X] `setInterval(checkProfitStatus)` — fix 25/abr (estava em outro script block)

**`/carteira`**
- [ ] Selector de portfolio atualiza tabelas
- [ ] Tab Trades: novo trade BUY → P&L + agrega em Posições
- [ ] Tab Outros: cadastro imóvel → IR isento check
- [ ] Botão 🖨 Imprimir

**`/portfolios`** ✅ **DEPRECADA 25/abr** — refactor 1 portfolio por conta. Rota faz redirect 302 → `/profile#invest`. CRUD agora 100% em Perfil → Contas (cada conta tem 1 carteira "Portfolio"). Tests legados de criar/renomear/set-default não se aplicam mais.

**`/alerts`**
- [ ] Criar alerta PETR4 preco > 50 nota "teste"
- [ ] Filtrar por status
- [ ] Cancelar via FAModal → some
- [ ] Botão "Avaliar agora" → trigger count

### Golden path — alta prioridade

- [ ] `/screener` — EXECUTAR, filtros PL_max/ROE_min, click ticker leva a fundamental
- [ ] `/watchlist` — Add/remove ticker com SSE live, excluir watchlist bloqueia se última
- [ ] `/ml` — batch 118 tickers, filtro min_sharpe, Histórico, Mudanças
- [ ] `/performance` — KPIs (drawdown/sharpe/beta/alpha), charts, heatmap, 🖨
- [ ] `/diario` — add BUY PETR4 entry=30 exit=33 qty=100, editar, excluir, stats
- [ ] `/fixed-income` — aplicar título, comparar 2, resgatar parcial, delete com saldo bloqueia
- [ ] `/crypto` — aporte BTC, resgate parcial, selector de conta

### Golden path — média (smoke + helpers)

Para cada, testar `carrega sem erro JS / helpers OK / FATable sort / FAEmpty CTA`:
- [ ] `/correlation`, `/anomaly`, `/sentiment`, `/forecast`, `/backtest`, `/optimizer`, `/var`
- [ ] `/dividendos`, `/etf`, `/laminas`, `/fundos`, `/patrimony`
- [ ] `/opcoes`, `/opcoes/estrategias`, `/vol-surface`, `/daytrade/setups`, `/daytrade/risco`, `/tape`
- [ ] `/marketdata`, `/macro`, `/fintz`, `/import`, `/subscriptions`, `/whatsapp`

### Admin

- [ ] `/admin` — lista users; CRUD agentes; **role dropdown agora User/Master** (Admin virou checkbox separado — refactor 25/abr); promover/rebaixar
- [X] `/admin` — coluna **Admin** com checkbox por linha (PATCH `/users/{id}/admin-flag`) — fix 25/abr
- [ ] `/hub` — event_records filtros; reprocessar dead_letter; cleanup > 30d

### Edge cases

- [ ] Auth: senha errada → toast; "Lembre-me 7d" expiry estendido; reset password com/sem token; já-logado em `/login` → redirect `/dashboard`
- [ ] Sessão: apagar `localStorage.access_token`, tentar ação → redirect `/login`; com refresh token (Lembre-me) → silent refresh
- [ ] RBAC: user comum em `/admin` ou `/hub` → 401 backend + access denied frontend
- [ ] Forms: CPF DV inválido, qty negativa, exit < entry → toast warn
- [X] Mobile: DevTools iPhone 12 (390×844) → sidebar collapsed default 1px; toggle abre 240px + body.sb-open + .fa-sb-backdrop full viewport; click fora fecha; topbar compact 52px — validado Playwright 25/abr
- [ ] Network: Fast 3G → skeletons; offline → PWA cache; DB down → toast com correlation_id
- [X] i18n: PT→EN sidebar muda + persiste; voltar sem refresh — validado Playwright 25/abr (FALocale.toggle, fa_locale persist; Alertas/Alerts, Perfil/Profile, Sair/Sign out)
- [X] Theme: light em todas páginas (anotar quebradas); `Cmd+Shift+L` — parcial: dataset+localStorage OK; visual mantém dark em /dashboard, /carteira, /alerts (Decisão 19); FAA11y FOUC prevention OK

### Pushover (12 alert rules)

- [ ] Grafana UI > Alerting > rule > "Test" → push chega no celular
- [ ] `di1_tick_age_high` firing fora pregão → critical com siren
- [ ] Alerta indicador em `/alerts` prestes a disparar → push normal
- [ ] Escalation: parar profit_agent 25min → 5 reconcile errors → critical

---

## 2. Features B/C/F/G1/G2 — novos fluxos a validar

Implementado em 23-24/abr mas ainda sem validação end-to-end em navegador.

### Feature B — Contas unificadas (`trading_accounts` → `investment_accounts`)

- [ ] `/profile#invest`: conta criada mostra campos opcionais `dll_account_type`, `dll_broker_id`, `dll_account_id`, `dll_routing_password` vazios → sem quebrar listagem
- [ ] Conectar DLL numa conta existente: botão "Conectar DLL" preenche os 4 campos e marca `dll_active=true`
- [ ] Ativar/desativar DLL (toggle): reflete em `/dashboard` Aba Conta
- [ ] Simulador: conta com `dll_account_type='simulator'` → não precisa routing_password (env `PROFIT_SIM_*` usa como fallback)
- [ ] `real_operations_allowed`: admin-only; marcar numa conta prod → dashboard permite ordem real
- [X] Constraint loose: criar 2 contas mesma CPF mas institution_code/agency/account_number diferentes → salva sem 500 (validado via SQL 24/abr)
- [X] Constraint tight simulator: 1 simulator GLOBAL no schema (não por user — divergência da doc original; índice partial `ux_inv_accounts_one_dll_sim` valida no DB)
- [X] `/dashboard` Aba Conta seletor mostra as contas com DLL ativa + apelido — fix 25/abr

### Feature C — Cash ledger

**C1/C2 — Depósito e saque**
- [ ] `/profile#invest`: botão Depositar/Sacar numa conta → modal com valor
- [X] POST `/api/v1/wallet/deposit` aumenta `cash_balance` imediatamente (crédito liquidado) — validado API 25/abr
- [ ] POST `/api/v1/wallet/withdraw` com saldo insuficiente → FAModal confirm "Confirma saldo negativo?" antes de enviar
- [X] Saque normal reduz `cash_balance`; aparece em /cash-summary — validado API 25/abr (warning quando ficaria negativo)

**C3 — Hooks trades**
- [X] Aba Trades/Carteira: criar trade BUY 100×PETR4 @ 35 → debita caixa em T+1 (pending até próximo settle) — validado API 25/abr (auto-cria tx_type=trade_buy status=pending settlement_date=T+1, pending_out=-3500, available_to_invest decresce)
- [X] `POST /api/v1/wallet/transactions/{id}/cancel` num pending → estorna — validado API 25/abr
- [ ] Trade SELL → credita em T+1 pending
- [ ] Scheduler `settle_cash_transactions_job` às 00:00 BRT → pendentes com due_date ≤ hoje viram settled
- [ ] Aviso de saldo insuficiente antes de confirmar trade BUY se caixa não cobre

**C3b — ETF metadata**
- [ ] ETF tem campos extras: `benchmark`, `management_fee`, `performance_fee`, `liquidity_days`
- [ ] Cadastro em `/etf` salva os 4 campos; aparecem no card

**C4 — Crypto hooks D+0**
- [ ] Aporte crypto → debita caixa no dia (sem pending)
- [ ] Resgate parcial → credita caixa no dia

**C5 — RF D+X (prazo do título)**
- [ ] Aplicar CDB liquidez D+1 → transação pending com due_date=T+1
- [ ] Aplicar LCI liquidez D+30 → pending com due_date=T+30
- [ ] Resgate antes do vencimento de LCI/LCA → warn + não libera caixa até due_date
- [ ] Scheduler settle_due_transactions_job processa pendentes com due_date ≤ hoje

**F1 — Histórico enriquecido**
- [ ] Modal Histórico: filtros período (início/fim), direção (crédito/débito/todos), include_pending (toggle)
- [X] Coluna **Saldo acumulado** (running_balance) calculada corretamente — campo presente em /transactions (API validada 25/abr); UI testar em browser
- [ ] Linha **Total** no footer (soma créditos − débitos)
- [ ] Botão Imprimir → layout clean com FAPrint

**F2 — Confirmação de saldo negativo**
- [ ] Withdraw/trade/crypto que deixaria caixa < 0 → FAModal.confirm antes de submeter

**F3 — Validação frontend valor**
- [ ] Campo valor vazio/0/negativo → input highlighted + toast warn + não submete

**F4 — Apelido em listings**
- [ ] `/carteira` Trades, `/crypto`: cada linha mostra apelido da conta (ex: "XP Principal")
- [ ] Filtrar por conta funciona

**F5 — Crypto aporte/resgate**
- [ ] `/crypto`: botão **Aportar** visível em cada holding
- [ ] Botão **Resgate parcial** — qty validada contra holding.qty

**F6 — RF Aplicar visível**
- [ ] `/fixed-income` Aba Buscar Títulos: cada linha tem botão verde **Aplicar**
- [ ] Click abre modal cascade: Conta → Portfolio → (só depois) campos principal/data
- [ ] Sem `window.prompt()` nativo — modal custom

**F7 — Delete conta com saldo ≠ 0**
- [ ] Tentar deletar conta com `cash_balance > 0` → 409 "Há saldo em caixa"
- [ ] Tentar deletar conta com holdings → 409 "Há investimentos vinculados"
- [ ] Deletar conta zerada + sem holdings → soft-delete `is_active=false`

**F8 — Corte à esquerda /fixed-income**
- [ ] Sidebar aberta + `/fixed-income` → conteúdo NÃO é sobreposto
- [ ] Sidebar colapsada → content ocupa até `--sb-w-collapsed` margin
- [ ] Transição suave (cubic-bezier .22s)

### G1 — Auto-create portfolio (refactor 25/abr: 1 por conta)

- [X] Criar conta nova → **1 portfolio chamado "Portfolio"** criado automaticamente (antes eram 2: Principal + RF) — validado API 25/abr (acc1 nova → SQL mostrou 1 row "Portfolio" is_active=true)
- [X] `scripts/backfill_default_portfolios.py` rodou em todas contas antigas → idempotente (não duplica) — validado 25/abr (rodou 2x, 4→4 portfolios; precisa `PYTHONIOENCODING=utf-8 DATABASE_URL=...@localhost:5432...` p/ rodar do host)
- [X] Migration 0018 mesclou portfolios duplicados existentes em 1 por conta — validado no DB
- [X] Tentar criar 2º portfolio na mesma conta → 422 "Conta ja possui portfolio ativo" (unique partial index)
- [X] Coluna `is_default` removida — conceito não faz mais sentido

### G2 — Rename portfolio inline (refactor 25/abr: sem +Nova)

- [ ] `/profile#invest` card de conta: agora mostra **1 carteira "Portfolio"** (não mais lista N)
- [ ] Botão **✎** ao lado do portfolio → prompt/modal rename → PATCH `/api/v1/portfolios/{id}`
- [ ] Rename gera entry em `portfolio_name_history` (old, new, when)
- [X] **Botão "+Nova"** removido — cardinalidade é 1:1 (refactor 25/abr)
- [ ] Seção atualiza sem reload após rename
- [ ] `/fixed-income` aba Carteira RF: botão rename na carteira RF também disponível

---

## 3. Decisão 20 (BRAPI-purge) — validar fallback chain

BRAPI deixou de ser primária em 23/abr. Ordem canônica: **DB local → Yahoo → BRAPI**.
**Validado via API + log em 24/abr** (`/api/v1/quotes/{ticker}/history`).

- [X] `/marketdata?ticker=PETR4`: bars via DLL `profit_daily_bars` — log `market_data.source.db` (64 bars)
- [X] `/marketdata?ticker=WDOFUT`: bars via DLL — log `market_data.source.db` (69 bars, era bug 404 BRAPI)
- [X] `/marketdata?ticker=EMBR3`: cai pra `market_data.source.brapi` (DB <30 + Yahoo vazio = 3º recurso correto)
- [X] WMAR11 (sem DLL/Yahoo/BRAPI): retorna empty gracefully
- [X] `/screener` continua via BRAPI (exceção documentada) — validado API 25/abr (httpx logs mostram 4 batches a `brapi.dev/api/quote/...?fundamental=true`). NOTA: `/api/v1/fundamental/{ticker}` é endpoint separado (Fintz indicators), não BRAPI.
- [ ] `/dashboard` cotação PETR4: primeiro tenta `profit_agent :8002/quotes` (subscrito) → Yahoo → BRAPI — manual browser
- [X] Range `max`: vai direto pro Yahoo (`YAHOO_PREFERRED_RANGES`) — validado API 25/abr (PETR4 range=max → 6604 bars + log `market_data.source.yahoo`). NOTA: `10y` não está na Literal aceita pela route (only 1d/5d/1mo/3mo/6mo/1y/2y/5y/max → 422); só `max` é Yahoo-preferred no fluxo de leitura.
- [X] Routes não importam `BrapiClient` direto (exceto `producer.py`/`system_status.py` que são write-path do ingestor — uso legítimo)
- [X] Ingestor `ohlc_1m_ingestor` continua rodando e alimenta DB via BRAPI (único caminho de write) — validado 25/abr (container up, último cycle.done com 11 tickers, sleeping até 21:30Z)

---

## 4. Sudo mode + Profit Agent restart

- [ ] FASudo.fetch em action destrutiva (ex: deletar usuário admin) → modal com senha; cache 5min
- [ ] 401 + header `X-Sudo-Required` → re-prompt senha
- [ ] FASudo.fetchJson retorna parseado
- [ ] Restart Profit Agent via `/admin` ou `/profile` → confirmação password (sudo) → `os._exit(0)` no agent → NSSM restart automático
- [ ] Health `:8002/health` volta em <10s após restart
- [ ] Conta DLL anterior é re-conectada automaticamente (via `dll_active` persistido em DB)
- [ ] Auto-reconnect DB: `finanalytics_timescale` down por 20min + subir → profit_agent reconecta em <5s sem restart manual
- [ ] Log throttled: 3 silent excepts agora geram 1 log/min máx (não spammy)

### Profit Tickers UI (`/profit-tickers`)

- [ ] Filtros persistem em `localStorage` ao recarregar
- [ ] Bulk activate: selecionar N tickers + botão "Ativar selecionados"
- [ ] Badge 4 estados:
  - 🟢 **Coleta Ativa** — subscribed=true + has_recent_data=true
  - 🟡 **Aguardando feed** — subscribed=true + has_recent_data=false (<30min)
  - 🔴 **Falha DLL** — subscribed=false + active=true (tentou subscrever e falhou)
  - ⚪ **Inativo** — active=false
- [ ] Tooltip em cada badge explicando significado
- [ ] Colunas renomeadas (conferir vs nome anterior)
- [ ] Bulk cadastro top500 tickers: `scripts/bulk_cadastrar_top500_tickers.py` cadastrou 500 mais líquidos por mediana

---

## 5. Bugs abertos (não fechados)

| # | Bug | Impacto | Próximo passo |
|---|---|---|---|
| ~~BUG1~~ | ~~`/portfolios` trava navegador~~ | **RESOLVIDO 25/abr** — `/portfolios` deprecada (refactor 1 portfolio/conta) + Playwright não reproduziu trava em ambiente atual | — |
| BUG2 | G4: 22 páginas com auth inline | Médio — perdem refresh 7d | Migrar para `auth_guard.js` (~4-6h) |
| BUG3 | G6: `data-i18n` não aplicado nos HTMLs in-page | Baixo — sidebar + topbar OK | Migração Sprint UI gradual (Decisão 18) |
| BUG4 | `/predict_ensemble` 404 para tickers sem pickle | Baixo — h21 existe para top-116; h3/h5 aguardam Nelogica 1m | Z5 após arquivo Nelogica |
| BUG5 | Light mode em páginas com `:root` próprio | Baixo — intencional (Decisão 19) | Redesign deliberado por página |
| BUG6 | 3 alert rules só firing após 1º increment | Baixo — esperado | — |
| ~~BUG7~~ | ~~`/api/v1/portfolios/{id}/performance` retorna 422 (deveria 404) p/ portfolio inexistente~~ | **RESOLVIDO 25/abr** — `performance.py` mapeia `PerformanceError` com mensagem "não encontrado" para 404, demais (sem posições, período inválido) mantém 422. Validado API. | — |
| BUG8 | SMTP backup ausente para Pushover | Médio — se Pushover cair, sem redundância | Configurar SMTP p/ critical |
| BUG9 | Modal Hist em `/portfolios` fechava na hora ao abrir | **RESOLVIDO 25/abr** — guard de 300ms em `_maybeCloseOnOverlay()` (defesa contra mouseup deslocado). Não reproduzia no Playwright; fix preventivo | — |

---

## 6. C6 — Dividendos (não iniciado)

Única task da Feature C não implementada.

**Escopo:**
- [ ] Parser de extrato (PDF/CSV/OFX) detecta linhas "DIVIDENDOS RECEBIDOS" / "JCP" / "RENDIMENTOS"
- [ ] Auto-reconciliação: casa CNPJ+data+valor com holding em `positions` → cria `account_transactions` tipo=dividend, direction=credit, settled_at=data do crédito
- [ ] UI em `/import`: botão "Importar Dividendos" separado
- [ ] UI Movimentações global: página `/movimentacoes` (ou aba em `/carteira`) listando todas transactions agregadas (depósito/saque/trade/cripto/RF/dividendos) com filtros ticker/portfolio/direção/período
- [ ] Reconciliação manual: linha não-casada → operador anexa ao ticker correto
- [ ] Tests: import de extrato exemplo BTG e XP

**Estimativa**: 1 sessão dedicada (~4-6h com UI).

---

## 7. Tech debt para sessão dedicada

Além dos BUGs acima, estes itens requerem sessão focada:

- [ ] **Z5**: aguardar arquivo Nelogica 1m (~48h após pedido) → `runbook_import_dados_historicos.md` + treinar pickles h3/h5/h21
- [ ] **G4 auth refactor**: 22 páginas inline → `auth_guard.js` (dashboard.html já migrado 25/abr — 21 restantes)
- [ ] **G6 i18n spread**: aplicar `data-i18n` em forms/headers das páginas principais (`/dashboard`, `/carteira`, `/alerts`, `/fixed-income`)
- [ ] **Redundância de alertas**: SMTP backup além Pushover
- [ ] **Light mode cleanup**: páginas com `:root` próprio que o usuário decidir unificar
- [ ] **Etapa 2 do refactor portfolio:** revisar `/carteira`, `/fixed-income`, `/dashboard` selectors — confirmar que listam só "Portfolio" por conta (não esperam mais "Principal — X" + "RF — X"). Atualizar copy/labels que mencionam "carteira default" ou "RF Padrão".
- [ ] **Cancel order pregão (27/abr seg)**: revalidar fluxo após DLL voltar a aceitar cancels (limit fora de mercado → ✕ → status CANCELED em ~5s, fallback `/positions/dll` em 10s)

---

## 8. Critérios de aceite restantes

Do roteiro 22/abr, estes ainda não foram batidos:

- [ ] **Golden path manual (browser)** — ordem DayTrade, OCO, cancelar, CRUD portfolios/alertas/trades
- [ ] **Helpers interativos (browser)** — toast queue/pause, FAModal focus trap, FATable sort, Ctrl+K palette, Ctrl+Shift+L theme
- [ ] **Mobile responsive (browser)** — sidebar overlay, topbar compacto, touch targets
- [ ] **Theme light/dark (browser)** — toggle em todas páginas
- [ ] **i18n PT/EN switch (browser)** — muda sidebar
- [ ] **PWA (browser)** — instalar, offline, cache
- [ ] **Pushover (Grafana UI + celular)** — 4 cenários

**Novos critérios (23-25/abr):**

- [X] **Decisão 20** — fallback chain DB→Yahoo→BRAPI verificada via API + log (24/abr)
- [X] **Feature B** — schema completo + constraints DB + proxy resolve conta ativa (validado código + DB)
- [X] **Feature C** — schema + hooks D+0/D+1/D+X + scheduler 00:00 BRT (validado por leitura de código + scheduler logs)
- [ ] **Features F1-F8** — 8 UX refinements (browser manual)
- [X] **G1 (refactor 25/abr)** — auto-create **1 portfolio "Portfolio"** por conta (não mais Principal+RF); migration 0018 mesclou existentes
- [X] **G2 (refactor 25/abr)** — rename do portfolio único existe; criação de novo via UI desabilitada (1:1)
- [X] **UserRole refactor (25/abr)** — Admin virou flag `is_admin` ortogonal; UI `/admin` com checkbox; migration 0017
- [ ] **Sudo + restart** — password confirm + auto-reconnect DB + NSSM restart (browser + windows host)

---

## 9. Comandos úteis

### Pré-flight
```bash
docker ps --filter name=finanalytics --format "{{.Names}}: {{.Status}}"
curl -s http://localhost:8000/health
curl -s http://localhost:8002/health
```

### Smoke pós-deploy (antes da sessão manual)
```bash
# /portfolios devolve 302 redirect para /profile#invest (não mais 200 com HTML).
# Usar -L para seguir redirect; sem -L, 302 é o esperado.
for r in /dashboard /carteira /portfolios /alerts /profile /fixed-income /crypto /profit-tickers /admin /hub; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000${r}")
  echo "${r}: ${code}"
done
# Esperado: tudo 200, exceto /portfolios = 302
```

### Validar features 23-24/abr
```bash
# Feature C — cash summary (precisa Bearer)
curl -sH "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/wallet/cash-summary

# G1 — backfill default portfolios (idempotente)
.venv\Scripts\python.exe scripts\backfill_default_portfolios.py

# Decisão 20 — checar que routes não importam BrapiClient direto
grep -rn "from.*brapi.*import" src/finanalytics_ai/interfaces/api/routes/ | grep -v "market_data_client"
# deve retornar vazio
```

### Reset sessão manual (hot-fix)
```bash
docker cp src/finanalytics_ai/... finanalytics_api:/app/src/finanalytics_ai/...
docker restart finanalytics_api
```

---

**Documento gerado em**: 24/abr/2026 — atualizado 25/abr (sessão de fixes + refactor 1 portfolio/conta)
**Referência primária**: `Roteiro_Testes_UI_22abr.md`
**Status**: ~70 itens pendentes (após sessão 25/abr fechar ~30)
**Próxima sessão**:
1. Revalidação **27/abr (segunda, pregão)**: cancel order individual em `/dashboard` Aba Ordens
2. Browser manual: §1 carry-over (helpers globais, mobile, PWA, theme, i18n)
3. Etapa 2 do refactor portfolio: copy/labels em `/carteira`, `/fixed-income` que ainda mencionam "carteira default" ou "RF Padrão"
