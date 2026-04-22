# FinAnalytics AI — Roteiro de Testes UI

> **Data**: 22/abr/2026 (planejamento)
> **Versão do app testada**: pós-commit `459852c` (Sprint UI 21/abr completa, 24 helpers, light/dark, i18n PT/EN)
> **URL base**: `http://localhost:8000`
> **Login**: usar conta master (marceloabisquarisi@gmail.com)

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
- [ ] **Botão `PT/EN`** (FALocale) — click alterna sidebar entre português e inglês
- [ ] **Botão sol/lua** (FATheme) — click alterna tema light/dark; persiste em refresh; **atalho `Cmd/Ctrl + Shift + L`**
- [ ] **Botão Sair** (logout) — confirma via FAModal e redireciona `/login`

### Sidebar (38 links em 6 seções)
- [X] Click no botão hamburger expande/colapsa sidebarDiscove
- [X] Estado open/collapsed persiste em refresh (`localStorage.fa_sidebar_open`)
- [X] Link da página atual aparece com marca `active` (cor accent + borda direita)
- [ ] **Mobile (<768px)**: sidebar vira overlay; clique fora fecha; backdrop escuro

### Discovery
- [ ] **`Cmd/Ctrl + K`** ou tecla `/` → abre Command Palette (FAPalette) com 40+ páginas pesquisáveis
- [ ] **`g` + letra** (gd=dashboard, gp=portfolios, ga=alertas) → goto rápido
- [ ] **`?`** → overlay com lista de atalhos
- [ ] **Esc** → fecha qualquer dialog/modal/palette

### Notificações realtime
- [ ] **Sino topbar** (FANotif) — counter de unread; click abre dropdown últimas 30
- [ ] Notificação SSE em `/api/v1/alerts/stream` aparece em tempo real
- [ ] Notificações também disparam **toast** automático

### Toast (FAToast)
- [ ] Após qualquer save/delete bem-sucedido → toast verde 3.5s
- [ ] Após erro → toast vermelho 5s
- [ ] **Cap 4 visíveis** simultâneos (5º vai pra fila, aparece quando 1º expira)
- [ ] **Click no toast** → fecha imediatamente
- [ ] **Hover no toast** → pausa countdown; sair retoma
- [ ] Barra de progresso CSS (linha embaixo) shrinking

### Modals (FAModal)
- [ ] Qualquer ação destrutiva (excluir trade, desativar conta, etc) abre modal **Promise-based**
- [ ] **Esc** → cancel
- [ ] **Enter** → confirma OK
- [ ] **Click fora** (backdrop) → cancel
- [ ] **Focus trap** — Tab cicla dentro do modal; foco volta ao gatilho ao fechar

### Tabelas (FATable)
- [ ] Click no header da coluna → ordena asc/desc (seta visível)
- [ ] Detecta colunas numéricas vs texto automaticamente
- [ ] Filtro de busca (input acima) — esconde linhas que não casam
- [ ] **Auto-init** em qualquer `<table data-fa-table>`

### Empty states (FAEmpty)
- [ ] Tabela vazia mostra ícone + título + texto + CTA (botão clicável)
- [ ] CTA dispara ação correta (ex: "+ Novo Trade" abre modal trade)

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

Anotar APENAS bugs novos. Já documentados:

- 6 contact points/datasource Grafana ghosts da migração (3 deletados, 1 default email auto-criado pelo Grafana persiste)
- `/predict_ensemble` retorna 404 estruturado — pickles 3d/5d ainda não treinados (Z5 depende Nelogica)
- 3 alert rules (`brapi_errors_high`, `portfolio_ops_burst`, `scheduler_reconcile_errors_high`) só disparam após primeiro increment do counter (esperado)
- profit_agent.py duplo-spawn já resolvido (PID 121656 atual)
- `:root` per-page intencional — light mode pode ficar dark em algumas seções de páginas individuais (Decisão 19)

---

## 7. Critérios de Aceite

Sessão de testes pronta para encerrar quando:

- [ ] Todas páginas críticas (1-3 do golden path) → 0 erros bloqueantes
- [ ] Helpers globais (seção 2) → 0 quebras em páginas testadas
- [ ] Pushover end-to-end validado em 4 cenários
- [ ] Bugs novos catalogados em "Issues" (criar arquivo `Bugs_22abr.md`)

---

## 8. Próximas frentes (pós-testes)

Se tudo verde, avaliar:
- **Z5**: aguardar Nelogica (~24-48h) → treinar pickles h3/h5/h21 em batch
- **Migração in-page i18n**: gradual, página por página
- **SMTP backup**: hoje só Pushover; adicionar email como redundância em alertas critical

---

**Documento gerado em**: 21/abr/2026 23:30 UTC
**Repositório**: https://github.com/MarceloAbiSquarisi/finanalytics_ai
**Última versão**: commit `459852c`
