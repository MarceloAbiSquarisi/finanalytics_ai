# Roteiro de Testes Pendentes — FinAnalytics AI

> **Reorganizado**: 26/abr/2026 — classificação por dependência (pregão aberto/fechado/outras)
> **Login dev**: `marceloabisquarisi@gmail.com` / `admin123` (master)
> **DB seedado**: 1 conta consolidada **"Teste"** (id `eeee5555`) — migration `migrate_test_to_single_carteira.sql` (27/abr); contas XP+BTG soft-deleted
> **Invariante 27/abr**: todo ativo DEVE ter `investment_account_id` (NOT NULL em DB + `Field(...)` Pydantic em trades/crypto/other)
> **Cache**: SW v86 — `Ctrl+Shift+R` na 1ª abertura de cada página

---

## ✅ Status atual (validações automáticas concluídas)

| Camada | Status | Detalhe |
|---|---|---|
| **Backend pré-flight** | ✅ | 18 containers UP, /health 200 nos 2 (api+agent), login OK |
| **Smoke 14 páginas** | ✅ | Todas 200 |
| **Backend filtro conta** | ✅ | XP=6 positions / BTG=2 positions; trades XP=7 / BTG=2 |
| **Backend tabs** | ✅ | trades=9, crypto=1, rf=3, other=1, tx=20 |
| **Backend dividendos** | ✅ | preview 3/3 matched, commit OK (após fix `7fe44ff`) |
| **Alerts BUG17** | ✅ | user_id JWT correto |
| **G4 auth flow** | ✅ | sem token=401, remember_me=86400s, refresh OK |
| **OCO Phase A+B+C+D** | ✅ profit_agent live | rotas /oco/* respondendo, profit_agent já restartado |

**Falta apenas**: testes UI/visual (browser) + testes que dependem de **pregão aberto**.

---

## 🟢 BLOCO A — Pregão FECHADO (pode fazer agora ~1h50)

> Tudo é render UI ou usa dados do seed. Não precisa tick/ordem viva.

### A.1 — /carteira filtro de conta (~5min)

- [X] **A.1.1** Abrir http://localhost:8000/carteira (`Ctrl+Shift+R` se 1ª vez)
- [X] **A.1.2** Selector "Conta" no topo mostra **3 opções**: Todas as contas / Teste Ações XP (XPI) / Teste Renda Fixa BTG (BTG Pactual)
- [X] **A.1.3** DevTools (F12) console — `[carteira] acc-filter populado com 2 contas`
- [X] **A.1.4** Selecionar **XP** → info inline `caixa: R$ 50.000,00`
- [X] **A.1.5** Selecionar **BTG** → `caixa: R$ 30.000,00`
- [X] **A.1.6** F5 mantém seleção (localStorage `fa_carteira_account_id`)

### A.2 — /carteira tabs render (~15min)

**Overview (1ª, default)**:
- [X] **A.2.1** Iframe carrega `/overview` — 8 cards (PETR4/VALE3/ITUB4/WEGE3/BBSE3/KNRI11/BBAS3/BOVA11)
- [X] **A.2.2** Sparklines SVG inline aparecem (carregam via /candles)
- [X] **A.2.3** Filtro "Apenas BUY" reduz cards (depende ML signals — pode estar `—`)
- [X] **A.2.4** Seção "Últimas movimentações" no rodapé do iframe — 5 tx (das 20 do seed)

**Contas**:
- [X] **A.2.5** Lista 2 contas com institution_name + apelido em **2 linhas** (BUG15 fix: bold/small)

**Posições** (filtro = "Todas"):
- [X] **A.2.6** 8 linhas (PETR4 70 net, VALE3, ITUB4, WEGE3, BBSE3, KNRI11, BBAS3, BOVA11)
- [X] **A.2.7** Colunas: Ticker · Classe · Qtd · Preço Médio · **Atual** · **P/L** · **SL** · Total · Trades
- [X] **A.2.8** "Atual" preenche progressivamente (placeholder `—`)
- [X] **A.2.9** P/L verde/vermelho com pct embaixo
- [X] **A.2.10** Trocar pra "XP" → 6 linhas; "BTG" → 2 linhas

**Trades**:
- [X] **A.2.11** Filtro "Todas" = 9 / "BTG" = 2 (BBAS3/BOVA11) / "XP" = 7
- [X] **A.2.12** Coluna "Conta" mostra apelido bold + institution small (BUG15)

**Cripto**:
- [X] **A.2.13** Filtro "XP" → 1 linha BTC qty 0.025 avg R$ 280.000
- [X] **A.2.14** Botão 💰 (resgate parcial) abre prompt

**Renda Fixa**:
- [X] **A.2.15** Filtro "BTG" → 3 títulos (CDB BTG 110%, LCI BTG 95%, Tesouro IPCA+ 2030)

**Outros**:
- [X] **A.2.16** Filtro "XP" → 1 linha "Apartamento SP" R$ 450.000

### A.3 — /movimentacoes UI (~15min)

- [X] **A.3.1** Abrir http://localhost:8000/movimentacoes
- [X] **A.3.2** Tabela mostra 20 tx
- [X] **A.3.3** Filtros: Conta=XP → 13 tx; Conta=BTG → 7 tx; Direção=saídas → 12 tx; Tipo=dividend → 5 tx
- [X] **A.3.4** Sort por coluna: clicar "Data" inverte ↑/↓; "Valor" ordena por amount
- [X] **A.3.5** Paginação 50/100/200/500 funciona (relevante com mais volume)
- [X] **A.3.6** **Export CSV**: botão 📥 baixa `movimentacoes_2026-04-26.csv` com BOM UTF-8
- [X] **A.3.7** Totais no rodapé refletem TODO o filtrado (não só a página)
- [X] **A.3.8** **5 dividendos** têm botão 🔗 amarelo (related_id=null)
- [X] **A.3.9** Click 🔗 em "DIVIDENDOS PETR4" → modal pede ticker → digita PETR4 → toast OK + tx vinculada
- [X] **A.3.10** Botão 🖨 Imprimir abre window.print

### A.4 — /import C6 Dividendos (~15min)

- [ ] **A.4.1** Abrir http://localhost:8000/import
- [ ] **A.4.2** Card verde "💰 Importar Dividendos" presente na seção "Dividendos / Rendimentos"
- [ ] **A.4.3** Click → modal abre, select Conta carrega 2 opções (XP + BTG)
- [ ] **A.4.4** Sample CSV sintético:
  ```bash
  cat > /tmp/div.csv << 'EOF'
  data,desc,valor
  20/04/2026,DIVIDENDOS RECEBIDOS PETR4,180.00
  21/04/2026,JCP ITUB4,420.50
  22/04/2026,RENDIMENTO KNRI11,95.30
  EOF
  ```
- [ ] **A.4.5** Selecionar XP + upload `/tmp/div.csv` → "Analisar"
- [ ] **A.4.6** Tabela preview mostra **3 linhas matched** (verde) — PETR4/ITUB4/KNRI11
- [ ] **A.4.7** Tags: matched=3, ambiguous=0, unmatched=0
- [ ] **A.4.8** "Confirmar Importação" → toast OK → /movimentacoes mostra 3 dividendos novos
- [ ] **A.4.9** PDF sintético: erro 400 amigável se pdfplumber faltar

### A.5 — /alerts criar/listar/cancelar (~5min)

- [ ] **A.5.1** Abrir http://localhost:8000/alerts
- [ ] **A.5.2** Criar: ticker=PETR4, indicador=ROE, operador=`>`, threshold=15 → "Criar"
- [ ] **A.5.3** Toast OK, alerta aparece na lista
- [ ] **A.5.4** Click ✕ no alerta → cancela; lista atualiza

### A.6 — i18n PT/EN toggle (~10min)

- [X] **A.6.1** Botão `PT/EN` na topbar (esquerda do 🌙/☀️)
- [X] **A.6.2** Click → cycle pra EN; localStorage `fa_locale=en`
- [X] **A.6.3** Páginas que devem trocar:
  - `/dashboard` (tabs DT)
  - `/carteira` (title, subtitle, tabs Overview/Posições/Trades/Cripto/RF/Outros, sec titles, botões)
  - `/movimentacoes` (filtros, colunas, totais, status badges)
  - `/alerts` (form labels, botões, colunas)
  - `/import` (title + 5 seções)
  - `/screener`, `/watchlist`, `/profile`, `/admin`, `/hub`
  - `/macro`, `/forecast`, `/performance`, `/fundamental`, `/diario`
  - `/backtest`, `/correlation`, `/anomaly`, `/etf`
- [X] **A.6.4** Sidebar mostra "Visão Geral" → "Overview" em EN; "Movimentações" → "Transactions"
- [X] **A.6.5** F5 mantém locale
- [X] **A.6.6** Texto sem `data-i18n` continua em PT (intencional — fall-through)
- [X] **A.6.7** Voltar pra PT — todas mensagens revertem

### A.7 — G4 auth flow visual (~5min)

- [X] **A.7.1** Logout em /dashboard (FAModal "Deseja sair?") → redirect /login
- [X] **A.7.2** Login com "Lembrar-me 7 dias" marcado
- [X] **A.7.3** Após login, /dashboard carrega chip user com email
- [X] **A.7.4** Acessar /carteira → mantém sessão; F5 mantém

### A.8 — /dashboard OCO modal (sem submeter) (~15min)

> Validações UI das Phases A+B+C sem disparar ordens. Pode rodar SEM pregão pq não precisa de fill.

> Pré-requisito: ter pelo menos 1 ordem em status PendingNew. Se não tem, segunda no pregão você cria uma e testa lá. Se tiver alguma de teste anterior persistida, dá pra exercitar agora.

- [X] **A.8.1** Abrir /dashboard aba "Ordens"
- [X] **A.8.2** Em ordem com botão 🛡 (azul) → click abre modal "Anexar OCO"
- [X] **A.8.3** **Phase A**: 1 nível com TP=52 SL=47 → counter "X/X ✓ verde"
- [X] **A.8.4** **Phase B**: click "+ nível" → 2º com qty=0; editar qty 60/40 → confirmar OK no counter
- [X] **A.8.5** Validação sum: tentar 50/40 (=90) → bloqueia com "Soma das qty (90) deve bater parent.qty (X)"
- [X] **A.8.6** Validação proteção: nível com TP+SL ambos desmarcados → erro "Nível N: marque ao menos TP ou SL"
- [X] **A.8.7** **Phase C Trailing**: checkbox "🔄 TRAILING (Phase C)" → trail-box revela
- [X] **A.8.8** Radio R$ ↔ % muda placeholder do input
- [X] **A.8.9** Trailing sem SL marcado → erro "trailing requer SL marcado"
- [X] **A.8.10** **NÃO submeter** — clicar "Cancelar"

### A.9 — /dashboard outras tabs (~10min)

- [X] **A.9.1** Tab **Order** renderiza form (sem enviar)
- [X] **A.9.2** Tab **OCO** legacy renderiza
- [X] **A.9.3** Tab **Pos.** renderiza search ticker + lista assets
- [X] **A.9.4** Tab **List** = Ordens (já testado A.8)
- [X] **A.9.5** Tab **Signals** mostra ML signals (sub-tabs Live/Hist/Mudanças)
- [X] **A.9.6** Tab **Conta** mostra contas + ativa DLL

### A.11 — /overview UI refinements (sessão 26/abr noite) (~10min)

> Mudanças aplicadas nesta sessão (SW v62→v66): PM destacado, conta centralizada, checkbox-group, fullscreen.

- [X] **A.11.1** Backend `/api/v1/wallet/transactions?account_id=<uuid>` aceita filtro de conta (era hardcoded `None`)
- [X] **A.11.2** Em /carteira → trocar conta no filtro topo → "Últimas movimentações" recarrega só com tx daquela conta (DevTools Network: `?account_id=...&limit=5`)
- [X] **A.11.3** PM nos cards (`Pm R$ XX,XX`) aparece em **branco bold** — não mais cinza opaco
- [X] **A.11.4** Coluna "Conta" na tabela de últimas movimentações está **centralizada** (grid 5 colunas estável entre linhas)
- [X] **A.11.5** Toolbar mostra caixa `Mostrar:` com **8 checkboxes** (Todos + 7 fontes: Posições/FIIs/Fundos/Watchlist/Crypto/RF/Outros)
- [X] **A.11.6** Desmarcar "Crypto" + "Outros" → cards reduzem; "Todos" desmarca para refletir
- [X] **A.11.7** F5 mantém seleção (localStorage `fa_overview_sources`)
- [X] **A.11.8** Marcar "Todos" → todas as fontes voltam; desmarcar "Todos" → grid vazio
- [X] **A.11.9** Botão `⛶ Tela cheia` ao lado do `📐 Compact`
- [X] **A.11.10** Click → iframe Overview ocupa tela inteira (sidebar/topbar/abas do /carteira somem)
- [X] **A.11.11** Em fullscreen: background opaco (não transparente), label vira `⛶ Sair`
- [X] **A.11.12** Esc ou click `⛶ Sair` retorna ao layout normal

### A.12 — Sessão 27/abr noite (filtros, layout, carteira única) (~25min)

> Mudanças aplicadas: filtros em tabs do /carteira, modal Histórico em /dashboard, layout de páginas órfãs, fix watchlist auth+tz, /performance carteira-based, consolidação seed em 1 conta "Teste".

**Filtros novos no /carteira**:
- [ ] **A.12.1** Tab Trades: 5 filtros (ticker / data início / data fim / classe / OP) + resumo "N trades · Compras X · Vendas Y" (backend `/trades` aceita date_from/date_to/operation)
- [ ] **A.12.2** Tab Cripto: select Symbol populado dos symbols únicos da carteira; resumo "X de Y"
- [ ] **A.12.3** Tab Renda Fixa: 3 filtros (Tipo / Emissor / IR isento|tributável); + tabela passou a renderizar (loadRf novo, era placeholder estático)
- [ ] **A.12.4** Tab Outros: 2 filtros (Tipo / Moeda)
- [ ] **A.12.5** Tab Posições: nova coluna **Moeda** + filtro Moeda (backend `/positions` agora retorna `currency` do trade)

**/carteira → Cripto resgate**:
- [ ] **A.12.6** Click 💰 Resgate parcial abre modal customizado (não mais `window.prompt`); preview live "Crédito estimado: R$ X (Y%)"

**/dashboard**:
- [ ] **A.12.7** Botão 📊 Histórico na topbar (ao lado MERCADO AO VIVO) abre modal grande com 5 filtros + tabela
- [ ] **A.12.8** Selector de conta na topbar mostra contas sem DLL como `[SEM DLL]` disabled (não mais "Nenhuma conta")
- [ ] **A.12.9** OCO Anexar modal: input qty agora tem label "QTD AÇÕES" + hint "de N (X%)" atualizando em tempo real; rodapé do modal mostra "· N restantes" (gold) ou "· N a mais" (vermelho)

**Layout**:
- [ ] **A.12.10** /movimentacoes agora tem topbar canônica + sidebar (era órfã)
- [ ] **A.12.11** /import idem (substituiu o `<nav>` antigo)

**/watchlist**:
- [ ] **A.12.12** Adicionar ticker funciona (era 401 Not authenticated → fix Bearer header; depois 500 datetime tz → fix _naive() helper no repo)

**/performance** (canonical carteira):
- [ ] **A.12.13** Selector mostra "Teste (Carteira Consolidada Teste)" — não mais "portfólio"
- [ ] **A.12.14** Backend novo: `GET /api/v1/wallet/accounts/{account_id}/performance?period=1y` retorna `account_id` + `account_label` (resolve portfolio 1:1 internamente). Endpoint legacy `/api/v1/portfolios/{id}/performance` mantido para retrocompat.
- [ ] **A.12.15** Empty state CTA aponta pra `/carteira` (era `/portfolios` deprecada)

**Carteira única "Teste" + invariante**:
- [ ] **A.12.16** `/api/v1/wallet/accounts` retorna 1 ativa: id `eeee5555` apelido "Teste"
- [ ] **A.12.17** Contas XP (`aaaa1111`) + BTG (`bbbb2222`) soft-deleted
- [ ] **A.12.18** Todos os ativos (14 trades + 13 positions + 1 crypto + 7 RF + 3 other) migrados para conta Teste
- [ ] **A.12.19** POST sem `investment_account_id` retorna 422 (Pydantic Field obrigatório)
- [ ] **A.12.20** DB-level: `investment_account_id` é `NOT NULL` nas 5 tabelas

### A.10 — Smoke visual 14 páginas (~15min)

> Já testado HTTP 200. Aqui é só passar o olho em cada uma.

- [X] **A.10.1** /dashboard (já em A.8/A.9)
- [X] **A.10.2** /carteira (já em A.1/A.2)
- [X] **A.10.3** /movimentacoes (já em A.3)
- [X] **A.10.4** /alerts (já em A.5)
- [X] **A.10.5** /import (já em A.4)
- [X] **A.10.6** /screener — input filtros + Executar Screener
- [X] **A.10.7** /watchlist — adicionar ticker, listar
- [X] **A.10.8** /admin — tabela users
- [X] **A.10.9** /hub — status serviços (admin-only)
- [X] **A.10.10** /performance — KPIs (precisa portfolio com dados — pode aparecer vazio)
- [X] **A.10.11** /diario — botão "+ Novo Trade"
- [X] **A.10.12** /fundamental — gerar relatório
- [X] **A.10.13** /forecast — controls
- [X] **A.10.14** /macro — snap grid

---

## 🔴 BLOCO B — Pregão ABERTO (segunda 27/abr 10h-18h BRT, ~3h)

> Precisa DLL aceitar ordem viva ou tick real fluindo.

> ✅ **Pré-requisito JÁ FEITO**: profit_agent rodando com Phase A+B+C+D (validado no batch — descoberta `5cf12d0` ativo).

### B.1 — DT cancel order (~5min)

- [ ] **B.1.1** Limit BUY PETR4 R$30 (longe do mercado) → enviar (PendingNew)
- [ ] **B.1.2** Em "Ordens" → click ✕
- [ ] **B.1.3** Status CANCELED em ~5s (polling 600/2000/5000ms)
- [ ] **B.1.4** Fallback `/positions/dll` em 10s consolida estado

### B.2 — DT enviar ordem real (~5min)

- [ ] **B.2.1** Aba Ordem: BUY PETR4 100 @ Market simulação → toast ok
- [ ] **B.2.2** Aparece em Ordens com status FILLED
- [ ] **B.2.3** Aba Pos. mostra posição

### B.3 — OCO legacy (~10min)

- [ ] **B.3.1** Aba OCO: TP 35 + SL 28 stop_limit 27.50 → enviar
- [ ] **B.3.2** Ordem em "Ordens" + polling automático monitora par
- [ ] **B.3.3** Quando uma perna fillar, outra cancela auto

### B.4 — GetPositionV2 (~5min)

- [ ] **B.4.1** Aba Pos. → search PETR4
- [ ] **B.4.2** Retorna preço médio + qty real-time

### B.5 — Cotação live PETR4 (~5min)

- [ ] **B.5.1** Cotação aparece em /dashboard
- [ ] **B.5.2** Origem: profit_agent /quotes (DLL subscrita) primeiro
- [ ] **B.5.3** Fallback Yahoo/BRAPI se profit_agent vazio (Decisão 20)

### B.6 — OCO Phase A end-to-end (~15min)

- [ ] **B.6.1** Limit BUY PETR4 100 @ R$30 longe → enviar
- [ ] **B.6.2** Click 🛡 → modal → TP=52, SL=28 limit=27.50 → "Anexar OCO"
- [ ] **B.6.3** Toast: "OCO anexado · group XXXXXXXX · 1 nível(eis)"
- [ ] **B.6.4** DB: `SELECT status, parent_order_id FROM profit_oco_groups` → 1 row `awaiting`
- [ ] **B.6.5** `/api/v1/agent/oco/groups` retorna 1 group
- [ ] **B.6.6** Reduzir preço da mãe pra fillar
- [ ] **B.6.7** Status vira `active` ou `partial`; TP+SL aparecem em "Ordens"
- [ ] **B.6.8** Log profit_agent: `oco_group.dispatched group=... filled=N/M levels=K`

### B.7 — OCO Phase B Splits (~15min)

- [ ] **B.7.1** Limit BUY VALE3 100 @ valor longe → pending
- [ ] **B.7.2** 🛡 OCO → "+ nível", qty 60/40, TP1=72 SL1=58, TP2=75 SL2=58 → confirma
- [ ] **B.7.3** DB: 2 rows em `profit_oco_levels` com level_idx 1 e 2
- [ ] **B.7.4** Validação sum: tentar 50/40 → mensagem `Soma das qty (90) deve bater parent.qty (100)`

### B.8 — OCO Phase C Trailing R$ (~15min)

- [ ] **B.8.1** BUY PETR4 100 @ market → fill imediato
- [ ] **B.8.2** OCO 1 nível: TP=35 SL=28 + ☑ Trailing R$ 0,50 → confirmar
- [ ] **B.8.3** Mover preço pra +R$ 1 (PETR4 sobe pra ~31)
- [ ] **B.8.4** Log: `trailing.adjusted group=... lv=1 hw=31.0000 new_sl=30.5000`
- [ ] **B.8.5** SL trigger no DB ajusta pra 30.50

### B.9 — OCO Phase C Trailing % (~10min)

- [ ] **B.9.1** OCO em VALE3 com Trailing 1.5% (radio %)
- [ ] **B.9.2** Mover preço +2% → SL trigger atualiza proporcionalmente

### B.10 — OCO Phase C Immediate trigger (~10min)

- [ ] **B.10.1** OCO com SL trigger 50 (ACIMA do last 48 — long, sell), trailing R$ 0,50
- [ ] **B.10.2** Já no submit: log `trailing.immediate_trigger group=... lv=N last=48 trigger=50 side=2`
- [ ] **B.10.3** Ordem market sell disparada imediato pra fechar
- [ ] **B.10.4** DB: `sl_status='sent'` com novo `sl_order_id` (market)

### B.11 — OCO Phase D Cross-cancel live (~15min)

- [ ] **B.11.1** Group active com 2+ níveis
- [ ] **B.11.2** Mover preço pra cima do TP1 → fillar
- [ ] **B.11.3** Log: `oco.tp_filled→sl_cancel group=... lv=1`
- [ ] **B.11.4** Level 1 SL = `cancelled` no DB
- [ ] **B.11.5** Group continua `partial` enquanto outros níveis ativos
- [ ] **B.11.6** Repetir até último nível → `completed`, `completed_at` setado

### B.12 — OCO Phase D Persistence + restart (~15min)

- [ ] **B.12.1** Com 1+ group active no DB, parar profit_agent (admin)
- [ ] **B.12.2** Subir novo: `Start-Process .venv\Scripts\python.exe ...`
- [ ] **B.12.3** Log inicial: `oco.state_loaded groups=N levels=M order_index=K`
- [ ] **B.12.4** `/api/v1/agent/oco/groups` retorna mesmos groups, status preservado
- [ ] **B.12.5** Cross-cancel continua funcionando após restart

### B.13 — Cancel manual de group (~5min)

- [ ] **B.13.1** Group active → `POST /api/v1/agent/oco/groups/{group_id}/cancel`
- [ ] **B.13.2** Resposta: `{ok:true, cancelled_orders:N}` (TP+SL pending)
- [ ] **B.13.3** DB: `status='cancelled'`, `completed_at` setado
- [ ] **B.13.4** Aba Ordens: TP e SL daquele group ficam CANCELED

### B.14 — Indicadores tick-dependent (~10min)

- [ ] **B.14.1** /marketdata?ticker=PETR4 — RSI/MACD/Bollinger reflete tick recente
- [ ] **B.14.2** /dashboard painel ML signals Live: tickers atualizados pós-pregão

### B.15 — DI1 realtime (~5min)

- [ ] **B.15.1** `di1_tick_age_high` deve ficar **resolved** durante pregão (tick < 120s)
- [ ] **B.15.2** Grafana dashboard DI1: 3 painéis com dados frescos

### B.16 — Reconcile loop scheduler (~10min)

- [ ] **B.16.1** Scheduler `reconcile_loop` (a cada 5min em 10h-18h BRT) executa
- [ ] **B.16.2** Order enviada via dashboard → após 5min, status no DB confere com DLL
- [ ] **B.16.3** Se DLL retorna order com status diff, log `reconcile.discrepancy.fixed`

### B.17 — Trade /carteira → DLL (~10min)

- [ ] **B.17.1** Aba Trades em /carteira: criar BUY/SELL
- [ ] **B.17.2** Trade chega no DLL (verifica em /positions)
- [ ] **B.17.3** Status reflete em /positions

---

## 🟠 BLOCO C — Outras dependências (não pregão)

### C.1 — Pushover (precisa celular ligado com app) (~15min)

- [ ] **C.1.1** Grafana UI → Alerting → rule → "Test" → push chega no celular
- [ ] **C.1.2** `di1_tick_age_high` firing fora pregão → critical com siren (priority=1)
- [ ] **C.1.3** Alerta indicador em /alerts prestes a disparar → push normal (priority=0)
- [ ] **C.1.4** Escalation: parar profit_agent 25min → 5 reconcile errors → critical

### C.2 — Sudo manual (você presente, fora pregão) (~30min)

- [ ] **C.2.1** Endpoint `POST /api/v1/agent/restart` com `require_sudo` → 401 + `X-Sudo-Required: true` sem token
- [ ] **C.2.2** FASudo.confirm prompt → senha → POST com header → 200
- [ ] **C.2.3** Health `:8002/health` volta em <10s após restart
- [ ] **C.2.4** Conta DLL re-conectada automaticamente
- [ ] **C.2.5** Phase D log: `oco.state_loaded groups=N` recarregado
- [ ] **C.2.6** Auto-reconnect TimescaleDB: down 20min → reconnect lazy
- [ ] **C.2.7** Log throttled: TICK_V1 callback error (count=21001, 22001 — Sprint Backend V1)

### C.3 — Samples reais BTG/XP (você fornecer) (~30min)

- [ ] **C.3.1** Sample CSV BTG real → /import preview matched ≥80%
- [ ] **C.3.2** Sample OFX BTG → idem
- [ ] **C.3.3** Sample PDF BTG (se houver) → parse_pdf extrai e classifica
- [ ] **C.3.4** Sample CSV/OFX/PDF XP → idem
- [ ] **C.3.5** Edge cases reais: linhas com R$ + IRRF, datas exóticas, tickers com sufixo (PETR4F), valores negativos
- [ ] **C.3.6** Após validação OK: **importar dados reais** dos investimentos (substitui seed teste)

### C.4 — Bloqueado externo (~48h após pedido)

- [ ] **C.4.1** Nelogica 1m bars chegarem
- [ ] **C.4.2** Importar via `scripts/import_historical_1m.py` → `ohlc_1m`
- [ ] **C.4.3** `populate_daily_bars.py --source 1m` → `profit_daily_bars`
- [ ] **C.4.4** `resample_ohlc.py` 5m/15m/30m/60m → `ohlc_resampled`
- [ ] **C.4.5** Treinar pickles ML h3/h5/h21 (Z5)
- [ ] **C.4.6** `/api/v1/ml/predict_ensemble` ganha multi-horizon real

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
   UNION ALL SELECT 'positions', count(*) FROM positions
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

### Restart profit_agent (Windows host, admin)
```powershell
$pid = (Get-NetTCPConnection -LocalPort 8002 -State Listen).OwningProcess
Stop-Process -Id $pid -Force
Start-Process -FilePath ".venv\Scripts\python.exe" `
  -ArgumentList "src\finanalytics_ai\workers\profit_agent.py" `
  -WindowStyle Hidden -RedirectStandardOutput ".profit_agent.log"
```

---

## Resumo executivo

| Bloco | Quando | Sub-itens | Tempo |
|---|---|---|---|
| 🟢 **A** Pregão fechado | agora | 12 seções (~96 checks) | ~2h30 |
| 🔴 **B** Pregão aberto | seg 27/abr 10h-18h BRT | 17 seções (~50 checks) | ~3h |
| 🟠 **C.1** Pushover | celular ligado | 4 checks | ~15min |
| 🟠 **C.2** Sudo presencial | você presente | 7 checks | ~30min |
| 🔵 **C.3** Samples reais | você fornecer | 6 checks | ~30min |
| ⚫ **C.4** Externo | Nelogica chegar | 6 checks | — |

**Validações backend já 100% verdes** (commit `7fe44ff`) — falta só UI/visual + pregão.

**Próximo gatilho**: você executa Bloco A (~1h50, qualquer hora). Reporta inline qualquer FAIL pra eu corrigir na hora. Bloco B segunda 27/abr no pregão.
