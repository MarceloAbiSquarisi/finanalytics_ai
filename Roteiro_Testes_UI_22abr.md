# FinAnalytics AI — Roteiro de Testes UI

> **Data**: 22/abr/2026 (planejamento)
> **Versão do app testada**: pós-commit `459852c` (Sprint UI 21/abr completa, 24 helpers, light/dark, i18n PT/EN)
> **URL base**: `http://localhost:8000`
> **Login**: usar conta master (marceloabisquarisi@gmail.com)

---

## 📊 Status de execução (22/abr/2026 — sessão Claude)

**Fase automatizada — CONCLUÍDA**. Cobre tudo que é testável sem navegador real.

| Seção | Status | Nota |
|---|---|---|
| 0. Pré-requisitos | ✅ | 17/17 containers up, health OK |
| 1. Inventário 42 rotas | ✅ | 42/42 HTTP 200 |
| 2. Helpers globais (estático) | ✅ | Syntax OK, i18n paridade, gaps corrigidos |
| 3. Golden path (backend) | ✅ | Endpoints testados; UI real → manual |
| 3. Golden path (UI CRUD flows) | ⏸ manual | Requer navegador logado |
| 4. Edge cases backend | ✅ | Auth, validation, 4xx/5xx |
| 4. Edge cases UI (mobile, theme) | ⏸ manual | Requer navegador |
| 5. Pushover alerts | ⏸ manual | Requer Grafana UI + celular |

**Bugs corrigidos nesta sessão (9)** — commits `3784f24` → `d1f60f1`:

| # | Bug | Status | Commit |
|---|---|---|---|
| B1 | `/api/v1/ml/metrics` 404 (image stale) | ✅ rebuild | `89ae947` |
| B2 | `/openapi.json` 500 (pydantic forward-ref) | ✅ fixed | `89ae947` |
| B3 | `/api/v1/live/sse/tickers` 404 (router não incluído) | ✅ fixed | `89ae947` |
| B4 | `/api/v1/patrimony/consolidated` 500 (3 imports quebrados) | ✅ fixed | `a3d6439` |
| B5 | `POST /auth/login` 500 com creds inválidas (bcrypt hash malformado) | ✅ fixed | `a3d6439` |
| B6 | `/api/v1/accounts/*` sem auth (vulnerabilidade) | ✅ fixed | `e731ddd` |
| G1 | `subscriptions.html` sem `sidebar.js` helper | ✅ fixed | `d1f60f1` |
| G2 | `admin/fintz/profit-tickers` sem `toast.js` | ✅ fixed | `d1f60f1` |
| G3 | `fundamental.html` sem `i18n.js` | ✅ fixed | `d1f60f1` |
| G5 | SW precache `fa-v3` só 17 assets (6 helpers fora) | ✅ `fa-v4` com 25 | `d1f60f1` |

**Bugs secundários fixados**:
- Dashboard chart axis: UTC → local BRT (`89ae947`)
- Toasts ruidosos no boot: `FAErr.fetchJson({silent})` (`3784f24`)
- Pickles models/ não montados como volume: `./models:/app/models:ro` (`e731ddd`)

**Gaps flagados sem fix** (tech debt, não bloqueantes):

- **G4**: 22 páginas usam auth inline (`if (!getToken()) location.href='/login'`) em vez de `auth_guard.js` helper. Funciona, mas perdem refresh automático (Lembre-me 7d), allowedRoles enforcement, onDenied customizável. Refator ~4-6h.
- **G6**: 0 referências `data-i18n=...` no HTML. 87 chaves definidas em PT/EN mas só sidebar usa `FAI18n.applyDOM()`. Coerente com "migração gradual" (Decisão 18).
- **/api/v1/var/portfolio/{id}**: 503 "PortfolioService nao disponivel" — experimental, UI usa `/var/calculate`. Não é bug.
- **/api/v1/portfolios/{id}/performance**: retorna 422 para portfolio inexistente (deveria ser 404). Minor.
- **/api/v1/anomaly/scan**: 422 sem params — ok, só precisa query string.
- **/api/v1/tape/metrics/PETR4**: 404 sem tape data. Esperado fora de pregão.

---

## 0. Pré-requisitos

Antes de começar, validar que tudo está no ar:

```powershell
# Containers
docker ps --filter name=finanalytics --format "{{.Names}}: {{.Status}}"
# Esperado: api, scheduler, grafana, prometheus, timescale, postgres,
#           kafka, redis, di1_realtime, worker, worker_v2, ohlc_ingestor — todos Up

# API health
Invoke-RestMethod "http://localhost:8000/health"
# Esperado: {status: ok, env: production}

# profit_agent (Windows host)
Invoke-RestMethod "http://localhost:8002/health"
# Esperado: {ok: true}
```

Se algum estiver Down, reiniciar antes:
```powershell
docker compose up -d
.venv\Scripts\python.exe src\finanalytics_ai\workers\profit_agent.py  # se profit_agent off
```

---

## 1. Inventário de Páginas (43 rotas)

Agrupado pela **sidebar** (6 seções + auth):

### 🔐 Auth (não-logado)
| URL | Página | Função |
|---|---|---|
| `/login` | Login | Login email/senha + "Lembre-me 7d" + link reset |
| `/reset-password` | Reset Password | Solicitar link reset + setar nova senha (token URL) |

### 📊 Visão Geral
| URL | Página | Função |
|---|---|---|
| `/dashboard` | Dashboard | SPA com painel DayTrade (Ordem/OCO/Pos/Ordens/Conta), watchlists, ML signals tabs (Live/Hist/Mudanças) |
| `/carteira` | Carteira | 6 tabs: Contas, Posições, Trades, Cripto, RF (read-only redirect), Outros ativos |
| `/portfolios` | Portfolios | CRUD portfolios; soft-delete (is_active); rename + history; set-default |
| `/alerts` | Alertas Fundamentalistas | CRUD alertas indicador (P/L, ROE, dividend yield, etc) |

### 🔍 Pesquisa
| URL | Página | Função |
|---|---|---|
| `/watchlist` | Watchlist | Múltiplas watchlists com cores (default 4); add/remove ticker |
| `/screener` | Screener | ~75 ações Ibovespa filtradas por critérios (P/L, P/VP, ROE, DY, etc) |
| `/fundamental` | Análise Fundamentalista | Indicadores fundamentalistas por ticker |
| `/correlation` | Correlação | Matriz heatmap + rolling correlation |
| `/anomaly` | Anomalias | Detecção de outliers em retornos/volume |
| `/sentiment` | Sentimento de Notícias | BERTimbau scores em notícias COPOM/macro |

### 🧠 Análise & ML
| URL | Página | Função |
|---|---|---|
| `/forecast` | Forecast | QuantileForecaster com bandas de confiança |
| `/ml` | ML Probabilístico | Predicts MVP por ticker + signals batch |
| `/backtest` | Backtest | Backtest de estratégias (BUY/SELL/HOLD signals) |
| `/optimizer` | Otimizador | Markowitz / Black-Litterman / Risk Parity |
| `/performance` | Performance | Drawdown, Sharpe, Beta, Alpha, vs IBOV |
| `/var` | VaR | Value at Risk (paramétrico, histórico, monte carlo) |

### 💰 Investimentos
| URL | Página | Função |
|---|---|---|
| `/fixed-income` | Renda Fixa | Carteiras RF; aplicar/resgatar/comparar; soft-delete |
| `/dividendos` | Painel de Dividendos | DY histórico + ranking Fintz |
| `/etf` | ETFs | Cadastro + rebalanceador de ETFs |
| `/crypto` | Criptoativos | Holdings BRL/USD; aporte/resgate parcial |
| `/laminas` | Lâminas | Lâminas PDF de fundos |
| `/fundos` | Fundos CVM | Sync cadastro CVM + informe diário |
| `/patrimony` | Patrimônio Consolidado | Net worth agregado (multi-classe) |

### 📈 Trading
| URL | Página | Função |
|---|---|---|
| `/opcoes` | Opções (Greeks, IV) | Cadeia de opções com greeks + IV |
| `/opcoes/estrategias` | Estratégias de Opções | Spreads (call spread, iron condor, etc) |
| `/vol-surface` | Superfície de Volatilidade | Smile + term structure |
| `/daytrade/setups` | DT Setups | Setups intraday (pin bar, IFR2, etc) |
| `/daytrade/risco` | DT Gestão de Risco | Stop, position sizing, max drawdown diário |
| `/tape` | Tape Reading | Times & Trades realtime |

### 🛠️ Dados & Sistema
| URL | Página | Função |
|---|---|---|
| `/marketdata` | Market Data | Cotações + bars OHLC com fallback chain |
| `/macro` | Macro | SELIC, IPCA, FX, IBOV, VIX, S&P, IGP-M |
| `/fintz` | Fintz Histórico | Cotações Fintz (200+ tickers, 2010→2025) |
| `/diario` | Diário de Trade | Journal de trades com P&L + setup + emoção + lições |
| `/import` | Importar Arquivos | Upload extratos BTG/XP/etc (CNPJ auto-detect) |
| `/subscriptions` | Subscrições Profit | Tickers subscritos no DLL |
| `/whatsapp` | WhatsApp Alertas | Alertas via Evolution API |
| `/hub` | Monitoramento | Event records + dead_letter (admin only) |
| `/profile` | Perfil | Dados pessoais + 2FA + alterar senha |
| `/admin` | Admin | RBAC users + agentes (admin/master only) |
| `/profit-tickers` | Profit Tickers | CRUD tickers DLL (active/inactive) |
| `/pnl` | P&L Intraday | P&L em tempo real |

---

## 2. Helpers Globais a Validar (24 assets)

Confirmar que cada **interação UI** abaixo funciona em **toda página privada** (não só uma):

### Topbar
- [X] **Logo** "FinAnalytics AI" → click leva ao `/dashboard`
- [X] **Avatar + email** visíveis (se logado)
- [X] **Botão `PT/EN`** (FALocale) — click alterna sidebar entre português e inglês
- [X] ⚠️ **Botão sol/lua** (FATheme) — click alterna tema light/dark; persiste em refresh; **atalho `Cmd/Ctrl + Shift + L`**
- [X] **Botão Sair** (logout) — confirma via FAModal e redireciona `/login`

### Sidebar (38 links em 6 seções)
- [X] Click no botão hamburger expande/colapsa sidebarDiscove
- [X] Estado open/collapsed persiste em refresh (`localStorage.fa_sidebar_open`)
- [X] Link da página atual aparece com marca `active` (cor accent + borda direita)
- [ ] **Mobile (<768px)**: sidebar vira overlay; clique fora fecha; backdrop escuro

### Discovery
- [X] **`Cmd/Ctrl + K`** ou tecla `/` → abre Command Palette (FAPalette) com 40+ páginas pesquisáveis
- [X] **`g` + letra** (gd=dashboard, gp=portfolios, ga=alertas) → goto rápido
- [X] **`?`** → overlay com lista de atalhos
- [X] **Esc** → fecha qualquer dialog/modal/palette

### Notificações realtime
- [ ] **Sino topbar** (FANotif) — counter de unread; click abre dropdown últimas 30
- [ ] Notificação SSE em `/api/v1/alerts/stream` aparece em tempo real
- [ ] Notificações também disparam **toast** automático

### Toast (FAToast)
- [X] ❌ Após qualquer save/delete bem-sucedido → toast verde 3.5s
- [X] ⚠️ Após erro → toast vermelho 5s
- [ ] **Cap 4 visíveis** simultâneos (5º vai pra fila, aparece quando 1º expira)
- [X] **Click no toast** → fecha imediatamente
- [X] **Hover no toast** → pausa countdown; sair retoma
- [X] Barra de progresso CSS (linha embaixo) shrinking

### Modals (FAModal)
- [X] Qualquer ação destrutiva (excluir trade, desativar conta, etc) abre modal **Promise-based**
- [X] **Esc** → cancel
- [X] **Enter** → confirma OK
- [X] **Click fora** (backdrop) → cancel
- [X] **Focus trap** — Tab cicla dentro do modal; foco volta ao gatilho ao fechar

### Tabelas (FATable)
- [X] Click no header da coluna → ordena asc/desc (seta visível)
- [ ] Detecta colunas numéricas vs texto automaticamente
- [X] Filtro de busca (input acima) — esconde linhas que não casam
- [X] **Auto-init** em qualquer `<table data-fa-table>`

### Empty states (FAEmpty)
- [X] Tabela vazia mostra ícone + título + texto + CTA (botão clicável)
- [X] CTA dispara ação correta (ex: "+ Novo Trade" abre modal trade)

### Loading skeletons (FALoading)
- [ ] Antes de fetch terminar, tabela mostra **shimmer rows** em vez de "Carregando..."
- [ ] Em `prefers-reduced-motion` → animação substituída por opacity

### Forms (FAForm)
- [ ] Save com campo obrigatório vazio → input fica vermelho + tooltip + toast warn
- [ ] CPF inválido → marca + mensagem
- [ ] Email inválido → marca + mensagem
- [ ] Após corrigir → estado de erro limpa

### Acessibilidade (FAA11y)
- [ ] **Tab** logo após carregar → "Pular para conteúdo" aparece (skip link azul)
- [ ] **Foco visível** — outline 2px cyan em qualquer elemento focado
- [ ] Modal abre → foco vai pro botão OK (e fica preso dentro)
- [ ] Modal fecha → foco volta ao gatilho

### PWA
- [ ] Browser oferece "Instalar app" (Chrome/Edge: ícone na barra)
- [ ] Após instalado → ícone na taskbar/dock; abre fullscreen sem URL
- [ ] **Offline**: assets/css continuam carregando (cache); chamadas /api/* falham (esperado)
- [ ] Refresh em página visitada → carrega instantaneamente do cache

### Print (FAPrint)
- [ ] Em `/carteira`, `/performance`, `/portfolios`, `/dividendos`: botão "🖨 Imprimir" visível
- [ ] Click → preview esconde sidebar/topbar/botões; expande tabelas; força fundo branco/preto
- [ ] Rodapé "FinAnalytics AI — impresso em DD/MM/YYYY HH:MM"

### Light/Dark (FATheme)
- [ ] Toggle muda fundo + texto + borders + accent
- [ ] **Sem flash** dark→light no refresh (FOUC prevention via inline `<head>`)
- [ ] Páginas com `:root` próprio (ex: performance, dividendos) podem manter dark — esperado

### i18n PT/EN (FAI18n)
- [ ] `PT/EN` button → sidebar inteira (38 links + 6 sections + Menu) muda
- [ ] `<html lang>` atualiza ("pt-BR" ou "en")
- [ ] Persiste em refresh (`localStorage.fa_locale`)
- [ ] Texto in-page (forms, headers) **continua PT** — esperado, migração gradual

### Error boundary (FAErr)
- [ ] Forçar erro: matar `timescale` container e tentar `/marketdata` ou outra página DB
- [ ] Toast vermelho aparece com mensagem + correlation_id (8 chars)
- [ ] Browser console não trava — apenas log

### Charts (FACharts)
- [ ] Páginas com Chart.js (`/performance`, `/backtest`, `/correlation`, `/dividendos`, `/etf`, `/fixed-income`, `/marketdata`, `/fintz`, `/diario`, `/dashboard`) — gráficos renderam
- [ ] Tooltip mostra valores; legenda no bottom; cores consistentes

---

## 3. Roteiro de Testes — Golden Path por Página

Para cada página, fazer **smoke test** (1-2 min) e marcar OK ou anotar bug.

### 🟢 Crítico — fazer primeiro

#### `/dashboard`
- [ ] Carrega sem erro JS (F12 console limpo)
- [ ] Aba **Ordem**: enviar ordem `BUY PETR4 100 @ Market` em conta SIMULAÇÃO → toast ok
- [ ] Aba **OCO**: TP 52 + SL 47 stop_limit 46.50 → ordem aparece em "Ordens"
- [ ] Aba **Pos.**: search "PETR4" → mostra GetPositionV2 (mesmo se zerado)
- [ ] Aba **Ordens**: lista atualiza a cada 5s; cancelar individual funciona
- [ ] Aba **Conta**: seletor mostra contas; switch ativa; create new conta → modal abre
- [ ] Painel ML signals (lado direito): tabs Live/Hist/Mudanças carregam dados

#### `/carteira`
- [ ] **Selector de portfolio** carrega lista; switch atualiza tabelas
- [ ] **Tab Contas**: criar nova conta com CPF inválido → bloqueio; CPF válido → salva
- [ ] **Tab Trades**: novo trade BUY → calcula P&L; aparece em Posições agregado
- [ ] **Tab Cripto**: aporte BTC → vira holding; resgate parcial reduz qty
- [ ] **Tab Outros**: cadastro imóvel → aparece com IR isento check
- [ ] Botão **🖨 Imprimir** funciona

#### `/portfolios`
- [ ] Criar novo portfolio "Teste"
- [ ] Renomear "Teste" → "Teste2" — modal de history mostra entry com (old, new, when)
- [ ] Set-default em outro portfolio
- [ ] Tentar desativar portfolio com saldo > 0 → erro 422 estruturado
- [ ] Desativar portfolio vazio → soft-delete (`is_active=false`); reativar funciona

#### `/alerts`
- [ ] Criar alerta: PETR4, indicator=preco, operator=>, threshold=50, note="teste"
- [ ] Listar; filtrar por status
- [ ] Cancelar alerta → confirma via FAModal → desaparece da lista
- [ ] Botão "Avaliar agora" dispara `/alerts/indicator/evaluate` → mostra trigger count

### 🟡 Alta prioridade

#### `/screener`
- [ ] Click "EXECUTAR" → FAEmpty inicial some, FALoading aparece, depois resultados
- [ ] Filtros: PL_max=15, ROE_min=10 → atualiza lista
- [ ] Click em ticker → leva pra `/fundamental?ticker=X` (se implementado)

#### `/watchlist`
- [ ] Selecionar watchlist (4 default cores)
- [ ] Add ticker `MGLU3` → aparece com cotação live (SSE)
- [ ] Remove → confirma + some
- [ ] Excluir watchlist completa → bloqueio se for última

#### `/ml`
- [ ] Tab Signals batch — 118 tickers carregam com BUY/SELL/HOLD
- [ ] Filtro `min_sharpe=1.0` → reduz lista
- [ ] Tab Histórico — `/api/v1/ml/signal_history` ordenado DESC
- [ ] Tab Mudanças — `/api/v1/ml/signal_history/changes` mostra diff vs ontem

#### `/performance`
- [ ] Selecionar portfolio + período "1y"
- [ ] Cards KPI carregam: drawdown, sharpe, beta, alpha
- [ ] Charts (drawdown, equity, contributions) renderizam
- [ ] Heatmap meses × anos colorido
- [ ] Botão **🖨 Imprimir** funciona

#### `/diario`
- [ ] Adicionar trade BUY PETR4 entry=30 exit=33 qty=100 → P&L 300, is_winner=true
- [ ] Editar trade → recalcula P&L
- [ ] Excluir → FAModal confirm → some
- [ ] Stats: equity_curve + win_rate por setup/emoção

#### `/fixed-income`
- [ ] Selecionar carteira RF
- [ ] Aplicar título: bondId=X, principal=10000, date=hoje → some erro/sucesso
- [ ] Comparar 2 títulos com mesmo principal+prazo
- [ ] Resgatar parcial título → reduz invested
- [ ] Tentar deletar carteira com saldo → erro estruturado

#### `/crypto`
- [ ] Aporte BTC: qty=0.5, preco_medio_brl=300000 → holding criado
- [ ] Resgate parcial: qty=0.2 → holding atualiza
- [ ] Selector de conta filtra holdings

### 🟢 Média prioridade — explorar mas não exaustivo

- [ ] `/correlation`, `/anomaly`, `/sentiment`, `/forecast`, `/backtest`, `/optimizer`, `/var`
- [ ] `/dividendos`, `/etf`, `/laminas`, `/fundos`, `/patrimony`
- [ ] `/opcoes`, `/opcoes/estrategias`, `/vol-surface`, `/daytrade/setups`, `/daytrade/risco`, `/tape`
- [ ] `/marketdata`, `/macro`, `/fintz`, `/import`, `/subscriptions`, `/whatsapp`

Para cada uma:
- [ ] Carrega sem erro JS
- [ ] Helpers globais (toast/modal/i18n/light theme) funcionam
- [ ] Tabelas com dados mostram FATable sort
- [ ] Tabelas vazias mostram FAEmpty com CTA

### 🔧 Páginas Admin

#### `/admin` (admin/master only)
- [ ] Lista users com role
- [ ] CRUD agentes
- [ ] Promover/rebaixar role

#### `/hub` (admin/master only)
- [ ] Lista event_records com filtros (status, event_type)
- [ ] Reprocessar evento `failed` ou `dead_letter` → toast ok + status muda
- [ ] Cleanup eventos `completed` > 30d → toast com count deletado

#### `/profit-tickers`
- [ ] Lista tickers DLL ativos
- [ ] Add ticker `RAIZ4` → subscreve via DLL
- [ ] Inativar `MGLU3` → marca inactive

---

## 4. Roteiro de Testes — Edge Cases

### Auth
- [ ] Login com senha errada → toast vermelho específico
- [ ] Login com "Lembre-me 7d" marcado → expiry 7d em vez de 30min
- [ ] Reset password sem token → fase "esqueci"; com token URL → fase "nova senha"
- [ ] Logado abrindo `/login` ou `/reset-password` → redirect `/dashboard` (W: skip-if-logged-in)

### Sessão expirada
- [ ] Esperar token expirar (ou apagar `localStorage.access_token`) e tentar action → redirect `/login`
- [ ] Com refresh token válido (Lembre-me) → silent refresh (auth_guard)

### RBAC
- [ ] User comum tentar `/admin` ou `/hub` → 401 backend → access denied frontend (UX C)

### Forms
- [ ] Carteira: criar conta com CPF "12345678901" (DV inválido) → toast warn
- [ ] Carteira: criar conta sem nome → toast warn "Campo obrigatório"
- [ ] Trade: qty negativa ou exit antes de entry → toast warn

### Mobile
- [ ] DevTools > toggle device > iPhone 12
- [ ] Sidebar overlay funciona (<768px)
- [ ] Modais fullscreen
- [ ] Topbar compacto (esconde email)
- [ ] Tabelas com scroll-x
- [ ] Botões clicáveis (touch target ≥44px)

### Network resilience
- [ ] Throttle Fast 3G → loading skeletons aparecem mais tempo
- [ ] Offline → cache PWA serve último HTML; nova navegação falha graciosamente
- [ ] DB down (parar timescale) → toast vermelho com correlation_id em cada page que tenta query

### Internacionalização
- [ ] Mudar para EN → sidebar muda; settings persiste em refresh
- [ ] Voltar para PT → sidebar volta sem refresh

### Theme
- [ ] Light mode em todas páginas — algumas terão visual quebrado (`:root` próprio); anotar quais
- [ ] Atalho `Cmd+Shift+L` em qualquer página

---

## 5. Roteiro — Pushover Alerts

Aproveitar a sessão para validar os 12 alert rules:

- [ ] Disparar manualmente em Grafana UI: Alerting > Alert rules > qualquer rule > "Test" → push notification chega no celular
- [ ] Force `di1_tick_age_high` (já firing fora do pregão): você já recebeu — confirmar push **critical com siren**
- [ ] Criar alerta indicador em `/alerts` com threshold quase certo de disparar → quando disparar, push **normal** chega via Pushover
- [ ] Testar escalation scheduler: parar `profit_agent` por 25min → após 5 reconcile errors → push **critical** chega

---

## 6. Bugs conhecidos / esperados

Já documentados (não são bugs novos):

- 6 contact points/datasource Grafana ghosts da migração (3 deletados, 1 default email auto-criado pelo Grafana persiste)
- `/predict_ensemble` retorna 404 **quando não há pickle para o ticker** (pickles h21 existem para top-116 tickers; pickles h3/h5 ainda não treinados — Z5 depende Nelogica)
- 3 alert rules (`brapi_errors_high`, `portfolio_ops_burst`, `scheduler_reconcile_errors_high`) só disparam após primeiro increment do counter (esperado)
- `:root` per-page intencional — light mode pode ficar dark em algumas seções de páginas individuais (Decisão 19)

---

## 7. Critérios de Aceite

- [x] **Fase automatizada** — 0 bugs backend bloqueantes (9 corrigidos; 6 secundários flagados)
- [x] **Smoke HTTP** — 42/42 rotas HTTP 200; 70+ endpoints da API validados
- [x] **Observabilidade** — 5/5 Prometheus targets up, 12/12 alert rules carregadas
- [x] **Validação estática dos helpers** — 20/20 syntax OK, i18n PT=EN=87 chaves
- [ ] **Golden path manual (browser)** — enviar ordem DayTrade, OCO, cancelar, CRUD portfolios/alertas/trades
- [ ] **Helpers interativos (browser)** — toast queue/pause, FAModal focus trap, FATable sort, Ctrl+K palette, Ctrl+Shift+L theme
- [ ] **Mobile responsive (browser)** — sidebar overlay, topbar compacto, touch targets
- [ ] **Theme light/dark (browser)** — toggle em todas páginas
- [ ] **i18n PT/EN switch (browser)** — muda sidebar (in-page continua PT — esperado)
- [ ] **PWA (browser)** — instalar, offline, cache
- [ ] **Pushover (Grafana UI + celular)** — 4 cenários (manual test, di1_tick_age, alerta indicador, escalation)

---

## 8. O que ainda falta para fechar o roteiro

### Manual no navegador (sessão 2)

Requer **usuário logado** rodando os fluxos — não é automatizável por HTTP.

1. **Dashboard DayTrade** (`/dashboard`):
   - Aba Ordem: BUY PETR4 100 @ Market em conta SIMULAÇÃO → toast ok
   - Aba OCO: TP 52 + SL 47 stop_limit 46.50 → ordem em "Ordens"
   - Aba Pos./Ordens/Conta: validar polling, cancel, switch conta
   - Aba ML Signals: sub-tabs Live/Hist/Mudanças

2. **Carteira** (`/carteira`):
   - Criar conta com CPF inválido → bloqueio via FAForm
   - Trade BUY → P&L calculado, aparece em Posições
   - Cripto aporte + resgate parcial
   - Outros: cadastro imóvel
   - Botão 🖨 Imprimir

3. **Portfolios** (`/portfolios`):
   - Criar "Teste", renomear (auditoria aparece), set-default, desativar vazio, tentar desativar com saldo (422 estruturado)

4. **Alerts** (`/alerts`):
   - CRUD alerta indicador, avaliar agora, cancelar via FAModal

5. **Helpers transversais** (testar em qualquer página):
   - `Cmd/Ctrl+K` → Command Palette
   - `Cmd/Ctrl+Shift+L` → toggle theme
   - Botão PT/EN → sidebar muda, persiste
   - Tab no carregamento → skip-link
   - FAModal.confirm() em delete → Esc cancela, Enter OK, focus trap
   - Toasts: abrir 5 → 4 visíveis + 1 fila; hover pausa; click fecha
   - Tabelas com `data-fa-table` → sort/filter
   - FAEmpty em tabela vazia → CTA clicável

6. **Mobile** (DevTools > iPhone 12):
   - Sidebar overlay + backdrop
   - Modais fullscreen
   - Touch targets ≥44px

7. **Network**:
   - Throttle Fast 3G → skeletons mais visíveis
   - Offline → PWA cache serve páginas visitadas; `/api/*` falha graciosa
   - DB down (parar timescale) → toast vermelho + correlation_id

### Tech debt maior (sessão dedicada futura)

- **G4**: migrar 22 páginas de auth inline → `auth_guard.js` helper (~4-6h, validar refresh 7d)
- **G6**: aplicar `data-i18n="..."` nos HTMLs gradualmente (migração Sprint UI 22/abr+)

### Dependências externas

- **Z5**: aguardar arquivo Nelogica 1m (~24-48h após pedido) → `runbook_import_dados_historicos.md` + treinar pickles h3/h5/h21
- **SMTP backup para alertas critical**: hoje só Pushover; redundância recomendada

---

## 9. Registros operacionais

### Comandos úteis para re-rodar smokes

```bash
# 1. Pré-requisitos
docker ps --filter name=finanalytics --format "{{.Names}}: {{.Status}}"
curl -s "http://localhost:8000/health"
curl -s "http://localhost:8002/health"

# 2. Smoke das 42 rotas HTML
for r in /dashboard /carteira /portfolios /alerts /watchlist /screener /fundamental /correlation /anomaly /sentiment /forecast /ml /backtest /optimizer /performance /var /fixed-income /dividendos /etf /crypto /laminas /fundos /patrimony /opcoes /vol-surface /daytrade/setups /daytrade/risco /tape /marketdata /macro /fintz /diario /import /subscriptions /whatsapp /hub /profile /admin /profit-tickers /pnl /login /reset-password; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000${r}")
  echo "${r}: ${code}"
done

# 3. Endpoints críticos da API
curl -s "http://localhost:8000/api/v1/ml/metrics" | python -m json.tool
curl -s "http://localhost:8000/api/v1/patrimony/consolidated/user-demo"
curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000/openapi.json"
curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000/api/v1/live/sse/tickers"
curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000/api/v1/accounts/"   # deve ser 401 sem Bearer

# 4. Observabilidade
curl -s "http://localhost:9090/api/v1/targets" | python -c "import json, sys; [print(t['labels']['job'], t['health']) for t in json.load(sys.stdin)['data']['activeTargets']]"
curl -s -u admin:admin "http://localhost:3000/api/v1/provisioning/alert-rules" | python -c "import json, sys; d=json.load(sys.stdin); print(f'{len(d)} rules:'); [print(' ', r['title']) for r in d]"
```

### Após qualquer edit em `routes/` ou `static/`

```bash
# Hot-fix (sem rebuild)
docker cp src/finanalytics_ai/... finanalytics_api:/app/src/finanalytics_ai/...
docker restart finanalytics_api

# Persistir na image (cold-start seguro)
docker compose build api
docker compose up -d --force-recreate --no-deps api
```

---

**Documento gerado em**: 21/abr/2026 23:30 UTC
**Atualizado em**: 22/abr/2026 (sessão Claude — fase automatizada)
**Repositório**: https://github.com/MarceloAbiSquarisi/finanalytics_ai
**Última versão testada**: commit `d1f60f1`
