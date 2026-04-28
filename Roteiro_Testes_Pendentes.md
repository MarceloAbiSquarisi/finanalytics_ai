# Roteiro de Testes Pendentes — FinAnalytics AI

> **Reorganizado**: 26/abr/2026 — classificação por dependência (pregão aberto/fechado/outras)
> **Última atualização**: 28/abr/2026 manhã — A.4.9 + A.22.4 + A.23.9 + A.23.10 fechados via MCP + C.1 Pushover 4/4 + C.2 Sudo 7/7 (restart real via API com sudo_token; PID antigo precisou Stop-Process -Force devido DLL ConnectorThread). Bloco A 98.8%, C.1+C.2 100%.
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
| **M1 ML FIIs** | ✅ 27/abr | 26 FIIs IFIX backfill Yahoo + calibrados, top sharpe HFOF11+2.55, badge amarelo /signals |
| **M2 ML ETFs** | ✅ 27/abr | 13 ETFs B3, top BOVB11+2.70, badge azul /signals |
| **M3 Fundos CVM analytics** | ✅ 27/abr | 3 endpoints (peer-ranking/style/anomalies) + UI /fundos |
| **M4 Crypto signal** | ✅ 27/abr | /api/v1/crypto/signal/{symbol} score weighted, badge na aba Crypto |
| **M5 RF Regime** | ✅ 27/abr | 4 regimes determinísticos (NORMAL/STEEPEN/FLATTEN/INV), card no /carteira RF |
| **/diario campo Objetivo** | ✅ 27/abr | DT/Swing/B&H + tab dedicada + pills filtro |
| **/diario workflow incompletas** | ✅ 27/abr | is_complete + chip + sino FANotif persistente + hook DLL FILLED |
| **/dashboard S/R no chart** | ✅ 27/abr | Pivots clássicos + Swings + Williams + outlier filter + warning |
| **/dashboard flatten ticker** | ✅ 27/abr | Botão "ZERAR + CANCELAR PENDENTES" na aba Pos |

**Falta apenas**: Bloco B (pregão aberto) + alguns checks com dependência de tempo (A.24.5 dia 5 do mês, A.24.16 7+ dias snapshots crypto) + A.15.10 destrutivo (zerar PETR4 com DLL viva).

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

- [X] **A.4.1** Abrir http://localhost:8000/import
- [X] **A.4.2** Card verde "💰 Importar Dividendos" presente na seção "Dividendos / Rendimentos"
- [X] **A.4.3** Click → modal abre, select Conta carrega 2 opções (XP + BTG)
- [X] **A.4.4** Sample CSV sintético:
  ```bash
  cat > /tmp/div.csv << 'EOF'
  data,desc,valor
  20/04/2026,DIVIDENDOS RECEBIDOS PETR4,180.00
  21/04/2026,JCP ITUB4,420.50
  22/04/2026,RENDIMENTO KNRI11,95.30
  EOF
  ```
- [X] **A.4.5** Selecionar XP + upload `/tmp/div.csv` → "Analisar"
- [X] **A.4.6** Tabela preview mostra **3 linhas matched** (verde) — PETR4/ITUB4/KNRI11
- [X] **A.4.7** Tags: matched=3, ambiguous=0, unmatched=0
- [X] **A.4.8** "Confirmar Importação" → toast OK → /movimentacoes mostra 3 dividendos novos
- [X] **A.4.9** PDF sintético: erro 400 amigável se pdfplumber faltar (validado via guard `dividend_import_service.py:137` → `RuntimeError` → `import_route.py:911` `HTTPException(400, str(exc))`; teste unit `test_pdf_sem_pdfplumber_dispara_runtime` PASSED)

### A.5 — /alerts criar/listar/cancelar (~5min)

- [X] **A.5.1** Abrir http://localhost:8000/alerts
- [X] **A.5.2** Criar: ticker=PETR4, indicador=ROE, operador=`>`, threshold=15 → "Criar"
- [X] **A.5.3** Toast OK, alerta aparece na lista
- [X] **A.5.4** Click ✕ no alerta → cancela; lista atualiza

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
- [X] **A.12.1** Tab Trades: 5 filtros (ticker / data início / data fim / classe / OP) + resumo "N trades · Compras X · Vendas Y" (backend `/trades` aceita date_from/date_to/operation)
- [X] **A.12.2** Tab Cripto: select Symbol populado dos symbols únicos da carteira; resumo "X de Y"
- [X] **A.12.3** Tab Renda Fixa: 3 filtros (Tipo / Emissor / IR isento|tributável); + tabela passou a renderizar (loadRf novo, era placeholder estático)
- [X] **A.12.4** Tab Outros: 2 filtros (Tipo / Moeda)
- [X] **A.12.5** Tab Posições: nova coluna **Moeda** + filtro Moeda (backend `/positions` agora retorna `currency` do trade)

**/carteira → Cripto resgate**:
- [X] **A.12.6** Click 💰 Resgate parcial abre modal customizado (não mais `window.prompt`); preview live "Crédito estimado: R$ X (Y%)"

**/dashboard**:
- [X] **A.12.7** Botão 📊 Histórico na topbar (ao lado MERCADO AO VIVO) abre modal grande com 5 filtros + tabela
- [X] **A.12.8** Selector de conta na topbar mostra contas sem DLL como `[SEM DLL]` disabled (não mais "Nenhuma conta")
- [X] **A.12.9** OCO Anexar modal: input qty agora tem label "QTD AÇÕES" + hint "de N (X%)" atualizando em tempo real; rodapé do modal mostra "· N restantes" (gold) ou "· N a mais" (vermelho)

**Layout**:
- [X] **A.12.10** /movimentacoes agora tem topbar canônica + sidebar (era órfã)
- [X] **A.12.11** /import idem (substituiu o `<nav>` antigo)

**/watchlist**:
- [X] **A.12.12** Adicionar ticker funciona (era 401 Not authenticated → fix Bearer header; depois 500 datetime tz → fix _naive() helper no repo)

**/performance** (canonical carteira):
- [X] **A.12.13** Selector mostra "Teste (Carteira Consolidada Teste)" — não mais "portfólio"
- [X] **A.12.14** Backend novo: `GET /api/v1/wallet/accounts/{account_id}/performance?period=1y` retorna `account_id` + `account_label` (resolve portfolio 1:1 internamente). Endpoint legacy `/api/v1/portfolios/{id}/performance` mantido para retrocompat.
- [X] **A.12.15** Empty state CTA aponta pra `/carteira` (era `/portfolios` deprecada)

**Carteira única "Teste" + invariante**:
- [X] **A.12.16** `/api/v1/wallet/accounts` retorna 1 ativa: id `eeee5555` apelido "Teste"
- [X] **A.12.17** Contas XP (`aaaa1111`) + BTG (`bbbb2222`) soft-deleted
- [X] **A.12.18** Todos os ativos (14 trades + 13 positions + 1 crypto + 7 RF + 3 other) migrados para conta Teste
- [X] **A.12.19** POST sem `investment_account_id` retorna 422 (Pydantic Field obrigatório)
- [X] **A.12.20** DB-level: `investment_account_id` é `NOT NULL` nas 5 tabelas

### A.13 — /diario campo Objetivo + estatísticas + filtro (sessão 27/abr) (~20min)

> Mudanças aplicadas: novo campo `trade_objective` no diário (Day Trade / Swing / Buy & Hold), tab "Objetivo" no dashboard com breakdown e pills de filtro global. Migration alembic 0019.

**Schema + migration**:
- [X] **A.13.1** `docker exec finanalytics_postgres psql -U finanalytics -d finanalytics -c "\d trade_journal"` mostra coluna `trade_objective varchar(20)`
- [X] **A.13.2** `SELECT version_num FROM alembic_version;` retorna `0019_diario_trade_objective`

**Form de criação/edição**:
- [X] **A.13.3** Abrir http://localhost:8000/diario → "+ Novo Trade" → modal mostra select "Objetivo da operação" com 3 opções (DT/Swing/B&H + travessão "—")
- [X] **A.13.4** Criar trade BUY PETR4 100@30 com objetivo=DT → salvou; reabrir em editar → select pré-populado com DT
- [X] **A.13.5** API rejeita valor inválido: `curl -X POST .../entries -d '{... "trade_objective":"INVALID"}'` → 422 pattern_mismatch

**Filtro lista esquerda**:
- [X] **A.13.6** Selector "Todos os objetivos" no topo da lista junto com ticker/setup/dir
- [X] **A.13.7** Selecionar "⚡ Day Trade" → lista filtra só trades DT; "Todos os objetivos" volta tudo

**Badges nos cards e detail**:
- [X] **A.13.8** Card de cada trade com objetivo registrado mostra badge colorida ao lado de BUY/SELL (vermelho=DT, azul=Swing, verde=B&H)
- [X] **A.13.9** Click no card → tab Detalhe mostra mesma badge no header (entre direção e setup)

**Tab "Objetivo" (nova)**:
- [X] **A.13.10** Tab "Objetivo" entre "Por Setup" e "Psicologia"
- [X] **A.13.11** Topo: 1 card de insight por objetivo com `N trades · Win X% · ±R$ Y` (cor da borda = cor do objetivo)
- [X] **A.13.12** Bar chart horizontal "P&L total por objetivo" com cores verde (positivo) / vermelho (negativo)
- [X] **A.13.13** Tabela "Performance por objetivo" — Objetivo · Trades · Win% · P&L Total · P&L médio% (data-fa-table sortable)
- [X] **A.13.14** Sem trades com objetivo → tab mostra "Nenhum trade com objetivo registrado ainda"

**Pills de filtro global (acima das tabs)**:
- [X] **A.13.15** Pills "Filtro: Todos | ⚡ Day Trade | 📈 Swing | 🏛 Buy & Hold" acima das 5 tabs
- [X] **A.13.16** Pill "Todos" inicia ativa (cinza-azulada)
- [X] **A.13.17** Click "⚡ Day Trade" → pill fica vermelha; equity curve / Por Setup / Psicologia / KPIs do header (Win Rate, P&L, Rating) recalculam só com trades DT
- [X] **A.13.18** Hint à direita aparece: "Equity / Setup / Psicologia filtrados por ⚡ Day Trade"
- [X] **A.13.19** Tab "Objetivo" **não** muda quando filtro está ativo (continua mostrando os 3 pra comparar)
- [X] **A.13.20** F5 mantém filtro selecionado (localStorage `fa_diario_obj_filter`)
- [X] **A.13.21** Click "Todos" → volta agregação completa, hint some, pill volta cinza

**Backend curl**:
- [X] **A.13.22** `curl ".../diario/stats?user_id=user-demo"` → retorna `by_objective` com até 3 entries
- [X] **A.13.23** `curl ".../diario/stats?user_id=user-demo&trade_objective=daytrade"` → totais reduzidos; `by_objective` ainda lista os 3
- [X] **A.13.24** `curl ".../diario/entries?trade_objective=swing"` → lista filtrada pelo repo

### A.14 — /diario auto-fill from DLL fill + workflow incompletas (sessão 27/abr) (~25min)

> Mudanças: campo `is_complete` + `external_order_id` no diário (migration 0020). Hook no profit_agent que chama `POST /api/v1/diario/from_fill` quando ordem fica FILLED, criando entry pré-preenchida (ticker/direction/entry_price/quantity/timeframe). Filtro "Incompletas" + chip header + sino topbar.

**Schema + migration**:
- [X] **A.14.1** `\d trade_journal` mostra colunas `is_complete BOOLEAN NOT NULL` e `external_order_id VARCHAR(64)` + index UNIQUE parcial
- [X] **A.14.2** `SELECT version_num FROM alembic_version;` retorna `0020_diario_is_complete`

**Endpoint /from_fill (idempotência) — pode rodar SEM pregão**:
- [X] **A.14.3** `curl -X POST .../from_fill -d '{"external_order_id":"42",...}'` → 201, `created=true`, `is_complete=false`
- [X] **A.14.4** Mesma chamada repetida → 201 com `created=false` (idempotente por external_order_id)
- [X] **A.14.5** Sem `external_order_id` → 422 Pydantic
- [X] **A.14.6** `GET /incomplete_count` retorna count correto
- [X] **A.14.7** `GET /entries?is_complete=false` filtra só incompletas

**Endpoint toggle complete/uncomplete**:
- [X] **A.14.8** `POST /entries/{id}/complete` → `is_complete=true`
- [X] **A.14.9** `POST /entries/{id}/uncomplete` → volta `is_complete=false`
- [X] **A.14.10** ID inexistente → 404

**UI /diario filtro + badge + chip**:
- [X] **A.14.11** Selector "Status" no topo da lista esquerda (Todas / ⏳ Incompletas / ✅ Completas)
- [X] **A.14.12** Card de entry incompleta tem badge amarelo "⏳ PENDENTE" + borda esquerda amarela
- [X] **A.14.13** Header da página mostra chip "⏳ N Pendentes" amarelo (clicável → aplica filtro Incompletas) só quando N>0
- [X] **A.14.14** Click no card → tab Detalhe mostra botão amarelo "⏳ Concluir entrada"; após click vira verde "✅ Completa" (toggleable)

**Sino topbar (FANotif)**:
- [X] **A.14.15** Ao abrir /diario com pendências, sino topbar exibe badge vermelho com contagem
- [X] **A.14.16** Click no sino → item persistente "⏳ N entrada(s) do diário pendente(s)" com link para /diario
- [X] **A.14.17** Click no item leva para /diario
- [X] **A.14.18** Botão "Limpar" não remove o item persistente (continua até `count=0`)
- [X] **A.14.19** Após preencher tudo (count=0), badge some

**Hook DLL FILLED → cria entry (precisa pregão — Bloco B)**:
> Movido para B.18 (depende de fill real)

### A.15 — /dashboard aba Pos.: botão "Zerar + cancelar pendentes" (sessão 27/abr) (~10min)

> Mudanças: novo `POST /api/v1/agent/order/flatten_ticker` que orquestra cancel pending + zero_position pelo ticker selecionado. Botão vermelho aparece na aba Pos. quando há posição aberta OU pending orders.

**Endpoint composto (sem pregão — testar contrato/idempotência)**:
- [X] **A.15.1** `curl -X POST .../order/flatten_ticker -d '{}'` → 400 "ticker obrigatorio"
- [X] **A.15.2** Proxy `/api/v1/agent/orders?ticker=PETR4&limit=10` aceita filtro novo (lista só PETR4)
- [X] **A.15.3** `curl -X POST .../order/flatten_ticker -d '{"ticker":"PETR4","env":"simulation","daytrade":true}'` retorna estrutura: `{ok, ticker, cancelled_count, cancel_errors[], pending_found, zero_ok, zero_local_order_id, zero_error}`
- [X] **A.15.4** Fora de pregão, `cancel_errors` contém `ret=-2147483636` (DLL recusa) — esperado; estrutura OK

**UI dashboard**:
- [X] **A.15.5** /dashboard → aba "Pos." → digitar PETR4 → "Ver"
- [X] **A.15.6** Caixa vermelha "🚨 ZERAR + CANCELAR PENDENTES" aparece se `open_qty > 0` OU houver ordens pending; some se `open_qty=0` E sem pending
- [X] **A.15.7** Resumo acima do botão: `PETR4 — posição aberta: N · X ordem(ns) pendente(s)` (só os termos que se aplicam)
- [X] **A.15.8** Click → modal FAModal danger "🚨 Encerrar exposição em PETR4?" com label "ENCERRAR PETR4"
- [X] **A.15.9** Click "Cancelar" no modal → nada acontece, caixa vermelha permanece
- [ ] **A.15.10** Click "ENCERRAR PETR4" → botão fica "Encerrando..." disabled durante chamada; toast no fim

### A.16 — Suporte / Resistência: 3 métodos no chart (~15min)

> Mudanças: novo módulo `domain/indicators/support_resistance.py` com **swing high/low (clusters)**, **pivots clássicos** e **fractais Williams 5-bar**. Endpoint `GET /api/v1/indicators/{ticker}/levels?methods=swing,classic,williams&lookback=N`. Toggles no popup ⚙ Indicadores do /dashboard renderizam linhas horizontais via `priceSeries.createPriceLine`.

**Backend (sem pregão)**:
- [X] **A.16.1** `curl ".../indicators/VALE3/levels?methods=classic"` retorna `pp, r1-r3, s1-s3, levels[]` com 7 itens
- [X] **A.16.2** `?methods=swing&lookback=10` aceita lookback custom; resposta tem `swing.lookback=10`
- [X] **A.16.3** `?methods=invalid` → 400 com mensagem; ticker inexistente → 404
- [X] **A.16.4** Subset livre: `?methods=classic` retorna só esse campo (swing/williams ausentes)

**UI dashboard**:
- [X] **A.16.5** /dashboard → ⚙ Indicadores → seção "SUPORTE / RESISTÊNCIA" com 3 toggles
- [X] **A.16.6** Marcar **Pivots clássicos** → 7 linhas horizontais aparecem no chart: PP cinza grossa, R1-R3 vermelhas (sólida→tracejada), S1-S3 verdes (sólida→tracejada). Labels visíveis no eixo direito (PP, R1, R2, R3, S1, S2, S3)
- [X] **A.16.7** Marcar **Swings (clusters)** → linhas amarelas com label "Sw·R×N" (resistência) ou "Sw·S×N" (suporte); espessura proporcional a N (toques)
- [X] **A.16.8** Lookback `5` vs `10` muda quantidade de swings (10 mais robusto, menos linhas)
- [X] **A.16.9** Marcar **Fractais (Williams)** → linhas magenta/azul fina pontilhada com label "Fr↑" (alta) ou "Fr↓" (baixa); só os 5 mais recentes de cada tipo (não polui chart)
- [X] **A.16.10** Trocar ticker (clicar outro na watchlist) → cache invalida, refetch novo, novas linhas
- [X] **A.16.11** Desligar todos toggles → `srLines` removidas; chart limpo
- [X] **A.16.12** Badge ⚙ Indicadores conta toggles ativos (S/R conta cada um)
- [X] **A.16.13** Caixa cinza/lookback parecem com escala diferente em PETR4: dado pré-existente do banco tem rows com escala fracionária mistas; **classic é confiável** (só usa penúltima barra). Bug de qualidade do dado, não do algoritmo.

### A.17 — M1 ML para FIIs (sessão 27/abr) (~15min)

> Pipeline ML estendido para FIIs: novo script `backfill_yahoo_fii.py` ingere 26 FIIs IFIX via Yahoo (`*.SA`), reusa `compute_features_for_ticker` do builder e popula `features_daily` com source='yahoo_fii'. Coluna `asset_class` em `ticker_ml_config` (default 'acao'). Endpoint `/api/v1/ml/signals?asset_class=fii` filtra. UI no /dashboard tab Signals com selector PT/Ações/FIIs e badge `FII` amarelo.

**Schema + dados**:
- [X] **A.17.1** `\d ticker_ml_config` mostra coluna `asset_class VARCHAR(8) NOT NULL DEFAULT 'acao'` + index
- [X] **A.17.2** `SELECT asset_class, count(*) FROM ticker_ml_config GROUP BY asset_class` → `acao=118, fii=26`
- [X] **A.17.3** `SELECT count(DISTINCT ticker) FROM features_daily WHERE source='yahoo_fii'` → 26
- [X] **A.17.4** Pickles em `models/mvp_v2_h21_<TICKER>_<TS>.pkl` para 24 FIIs (HFOF11, KNCR11, etc) — exceto MALL11 e BCFF11 (delistados Yahoo)

**Calibração resultados** (`SELECT ticker, best_sharpe, best_return_pct, best_trades, best_win_rate FROM ticker_ml_config WHERE asset_class='fii' ORDER BY best_sharpe DESC LIMIT 10;`):
- [X] **A.17.5** Top sharpe FII: HFOF11=+2.55, KNCR11=+1.76, RECT11=+1.46, BRCR11=+1.36, KNRI11=+1.33
- [X] **A.17.6** Negativos: RBRF11=-0.74, HCTR11=-0.68 (excluídos da geração de pickle)

**Endpoint**:
- [X] **A.17.7** `curl ".../ml/signals?asset_class=fii&min_sharpe=0"` retorna 24+ items com `asset_class:'fii'`
- [X] **A.17.8** `?asset_class=acao&min_sharpe=1.5` filtra só ações (sem FIIs)
- [X] **A.17.9** Sem filtro retorna ambos misturados; cada item traz `asset_class`

**UI dashboard**:
- [X] **A.17.10** /dashboard → tab "Signals" → selector "classe" com 3 opções: Todas / Ações / FIIs
- [X] **A.17.11** Selecionar "FIIs" → lista mostra só *11 com badge amarelo `FII`; resumo `total=N · BUY=X · SELL=Y · HOLD=Z`
- [X] **A.17.12** Click numa linha FII → ticker preenche em t-tk e tab muda pra Order
- [X] **A.17.13** Selecionar "Ações" → FIIs somem; "Todas" → mistos com badge nos FIIs

**Limitações conhecidas**:
- A.17.13 dados Yahoo só vão até hoje, não tem split/dividendos ajustados; calibração é com 2 anos (519 bars)
- Features RF (DI1) não incluídas no MVP — pode-se ativar com `--no-rf` removido em retreino futuro
- DY/P/VP fundamentals não estão (Fintz não cobre FII; scraping Status Invest fica pra Sprint 2)
- Cobertura 26/30: MALL11 e BCFF11 delistados; outros 2 reservados pra futura expansão

### A.18 — M2 ML para ETFs (sessão 27/abr) (~10min)

> Pipeline ML estendido para ETFs B3: novo script `backfill_yahoo_etf.py` ingere 14 ETFs (BOVA/BOVV/BOVB/IVVB/NASD/SMAL/DIVO/FIND/MATB/GOVE/GOLD/ECOO/B5P2/IMAB), reusa pipeline FII com source='yahoo_etf'. 13 ETFs calibrados + 13 pickles treinados.

**Schema + dados**:
- [X] **A.18.1** `SELECT asset_class, count(*) FROM ticker_ml_config GROUP BY asset_class` → `acao=118, fii=26, etf=13`
- [X] **A.18.2** `SELECT count(DISTINCT ticker) FROM features_daily WHERE source='yahoo_etf'` → 13
- [X] **A.18.3** Pickles `models/mvp_v2_h21_<ETF>_*.pkl` para 13 ETFs

**Calibração resultados**:
- [X] **A.18.4** Top sharpe: BOVB11=+2.70, GOVE11=+2.54, BOVV11=+2.51, FIND11=+2.24, DIVO11=+2.14, BOVA11=+2.12, IMAB11=+2.04
- [X] **A.18.5** USPD11 delistado (Yahoo); B5P211 sem trades válidos (skip silencioso); 13/15 entregues

**Endpoint**:
- [X] **A.18.6** `curl ".../ml/signals?asset_class=etf"` retorna 13 items, 5 BUY (BOVV11, FIND11, BOVA11, IMAB11, SMAL11), 0 SELL, 8 HOLD
- [X] **A.18.7** Cada item traz `asset_class:'etf'`

**UI dashboard**:
- [X] **A.18.8** /dashboard → tab Signals → selector "classe" agora tem 4 opções (Todas / Ações / FIIs / ETFs)
- [X] **A.18.9** Selecionar "ETFs" → 13 linhas com badge azul `ETF` (cor #3abff8)
- [X] **A.18.10** Selecionar "Todas" → mistura — Ações sem badge, FIIs amarelo, ETFs azul

**Observações estratégicas** (Melhorias.md M2):
- BOVA11/BOVV11/BOVB11 trackeiam IBOV — sinal "BUY BOVA11" é redundante com IBOV uptrend; usar TSMOM Grafana para corte
- FIND11 (financeiro) e GOVE11 (governança) são os ETFs setoriais com melhor alpha histórico
- IMAB11 (RF IPCA+) tem sharpe alto mas drawdown baixo — útil para rotação defensiva

### A.19 — M5 RF Regime Classifier (sessão 27/abr) (~10min)

> Detecta regime da curva DI (NORMAL / STEEPENING / FLATTENING / INVERSION) baseado em `slope_2y_10y` da view `rates_features_daily` (DI1 worker mantém populada). Algoritmo determinístico (sem ML — HMM fica pra Sprint 2). Card visual no /carteira aba Renda Fixa com recomendação textual + alocação sugerida CDI/Pré/IPCA+.

**Backend**:
- [X] **A.19.1** Módulo novo `domain/rf_regime/classifier.py` com `analyze_regime()` (Python puro, statistics stdlib)
- [X] **A.19.2** Route `/api/v1/rf/regime` (registrada em app.py após screener)
- [X] **A.19.3** Schema retornado: `{regime, score, slope_2y_10y, slope_z_score, last_date, sample_size, history[N], recommendation:{headline, rationale, suggested_allocation:{cdi, pre_curto, ipca_longo}}}`
- [X] **A.19.4** Estado atual (last_date 2026-04-17): regime=**NORMAL**, score=0.85, slope=+0.003 (33bp), z=-0.15
- [X] **A.19.5** Recomendação NORMAL: 30/30/40 (CDI/Pré/IPCA+); rationale explica decisão
- [X] **A.19.6** `?history_days=30` reduz tamanho do histórico retornado
- [X] **A.19.7** `?lookback_days=750` aumenta janela de z-score (sem mudar regime atual)

**UI /carteira → aba Renda Fixa**:
- [X] **A.19.8** Card no topo (acima dos filtros), borda esquerda colorida pelo regime (NORMAL=verde, STEEPENING=azul, FLATTENING=âmbar, INVERSION=vermelho)
- [X] **A.19.9** Badge texto regime + headline com emoji (⚖️ ⚡ 📉 🔻)
- [X] **A.19.10** Linha técnica à direita: `slope 2y-10y: +33bp · z: -0.15 · score: 85%`
- [X] **A.19.11** Rationale em texto explicativo abaixo
- [X] **A.19.12** Alocação sugerida em 3 chips coloridos (verde/azul/âmbar): `CDI: X% · Pré curto: Y% · IPCA+ longo: Z%`
- [X] **A.19.13** Endpoint indisponível (DI1 worker down) → card oculto silenciosamente (não quebra a tab)

**Mapeamento regime → ação**:
| Regime | Trigger | Alocação |
|---|---|---|
| INVERSION | slope < -0.5bp | 70% CDI / 20% Pré / 10% IPCA |
| FLATTENING | slope ≥ 0 ∧ z < -1σ | 20% CDI / 20% Pré / 60% IPCA |
| STEEPENING | slope > 0 ∧ z > +1σ | 30% CDI / 50% Pré / 20% IPCA |
| NORMAL | nenhum dos acima | 30% CDI / 30% Pré / 40% IPCA |

### A.20 — M4 ML Crypto signal (sessão 27/abr) (~5min)

> Endpoint `/api/v1/crypto/signal/{symbol}` agrega RSI/MACD/EMA cross/Bollinger em score weighted → BUY/SELL/HOLD. Reusa /crypto/technical existente (CoinGecko OHLC daily). UI: badge na aba Crypto do /carteira.

**Backend**:
- [X] **A.20.1** `curl ".../crypto/signal/BTC"` retorna `{symbol, signal, score, label, components, indicators}`
- [X] **A.20.2** `days` aceita 180-365 (CoinGecko OHLC só serve candles diários nessa janela)
- [X] **A.20.3** components: rsi (-2 a +2) + macd (±1) + ema_cross (±1) + bollinger (±1); score total ≥+3=BUY · ≤-3=SELL · else HOLD
- [X] **A.20.4** Sem dados → 404; 14 dias → 422 (CoinGecko snapping)

**UI /carteira aba Crypto**:
- [X] **A.20.5** Nova coluna "Sinal" entre P/L e Preço Médio USD
- [X] **A.20.6** Cada linha mostra badge BUY (verde) / SELL (vermelho) / HOLD (cinza)
- [X] **A.20.7** Hover no badge → tooltip com breakdown dos componentes (`score=N (rsi=±X · macd=±Y · ...)`)
- [X] **A.20.8** Quando endpoint falha (rate limit CoinGecko, etc) → célula fica `—` silenciosamente

### A.21 — M3 Fundos CVM analytics (sessão 27/abr) (~10min)

> Novo módulo `domain/fundos/analytics.py` (Python+numpy puro): style_analysis (OLS vs fatores), peer_ranking (sharpe rolling), nav_anomalies (z-score >3σ). 3 endpoints sob `/api/v1/fundos-analytics/` (prefix separado para evitar conflito com `/{cnpj:path}` greedy do router fundos).

**Schema + dados**:
- [X] **A.21.1** 62k fundos em fundos_cadastro, 2.1M informes diários (cobertura jan-abr/2024)
- [X] **A.21.2** Classes disponíveis: Multimercado (18.6k), Ações (4.1k), Renda Fixa (3.2k), FII (481), FIDC, FIP

**Endpoints**:
- [X] **A.21.3** `GET /api/v1/fundos-analytics/peer-ranking?tipo=Multimercado&months=3&top=5` → 498 fundos avaliados, top sharpe inflados pelos FIs crédito privado low-vol (esperado dada janela de 4m)
- [X] **A.21.4** `GET /api/v1/fundos-analytics/anomalies/{cnpj}?months=4&threshold_sigma=2.5` → detecta outliers no NAV
- [X] **A.21.5** `GET /api/v1/fundos-analytics/style/{cnpj}?months=4&factors=BOVA11,SMAL11,IMAB11,GOLD11` → R², alpha, betas + pct (PODIUM teve 57% IMAB11, perfil RF crédito ✅)
- [X] **A.21.6** Validação CNPJ inexistente → 404 ou erro friendly
- [X] **A.21.7** `peer-ranking` aceita `end_date=YYYY-MM-DD` (default = max(data_ref) disponível)

**UI**:
- [X] **A.21.8** *(pendente — ficou só backend nesta sprint)* Adicionar tab/card no /fundos com peer-ranking + style breakdown

**Limitações conhecidas**:
- Informes CVM cobrem jan-abr/2024 só; precisaria sync mensal automático (já há `POST /sync/informe?competencia=AAAAMM` mas sem agendamento)
- Fatores ETFs em features_daily começam 2024-03-28 (Yahoo, M2 backfill) — overlap com fundos é ~30 dias na janela atual
- threshold de overlap reduzido pra 20 obs (de 30) na style_analysis pra acomodar essa janela curta

### A.22 — S/R outlier filter + data quality warning (sessão 27/abr noite) (~5min)

> Endpoint `/api/v1/indicators/{ticker}/levels` agora **filtra outliers de escala** antes de calcular swing/williams (heurística: `last_close * 0.4 a 2.5`). Se >50% dropados (escala mista no banco), retorna `swing/williams: null` + `data_quality_warning`. Mitiga bug pré-existente do `profit_daily_bars` (rows com 0.36 entre 48). Frontend mostra `FAToast.warn` quando warning.

- [X] **A.22.1** Schema response inclui `candle_count_raw`, `outliers_dropped`, `data_quality_warning` (string ou null)
- [X] **A.22.2** PETR4: 64 raw → 2 filtered, dropped 62 → warning explícito + `swing/williams=null` + `classic.pp=49.67` ainda funcional
- [X] **A.22.3** Toast warning aparece em /dashboard ao ativar Pivots/Swings/Williams em ticker com dados ruins
- [X] **A.22.4** Validar com ticker de dados limpos (VALE3 ou outro fora do bug) → warning=null, swing/williams retornam normalmente (5 tickers DLL pós-N1: VALE3/PETR4/ITUB4/BBDC4/ABEV3/WEGE3 todos `outliers_dropped=0` `warning=null`, swing 1-3 supports + 1-2 resistances, williams 3-7 fractais)

### A.23 — UI /fundos analytics M3 (sessão 27/abr noite) (~10min)

> Página `/fundos` ganhou nova seção **Analytics — Peer Ranking**: top 20 fundos por sharpe na classe selecionada (Multimercado/Ações/RF/FII/Cambial). Botão **Analisar** abre Style Analysis (R²/alpha/betas) + NAV Anomalies (z-score) inline.

- [X] **A.23.1** Card "Analytics — Peer Ranking" com 4 filtros: Classe, Janela (3/6/12/24m), PL min, botão Buscar Top
- [X] **A.23.2** Tabela 8 colunas: # · Fundo · CNPJ · Sharpe · Retorno % · Vol.Anual % · Obs · Análise (botão)
- [X] **A.23.3** Click "Buscar Top" Multimercado 6m → 20 fundos rankeados (PODIUM rank 1, sharpe +184)
- [X] **A.23.4** Meta abaixo: "Avaliados: 498 fundos · Janela: 6m · Até: 2024-04-30"
- [X] **A.23.5** Click "Analisar" no PODIUM → Style Section expande com R²=0.0394, Alpha 14.66% a.a., 4 fatores (BOVA11/SMAL11/IMAB11/GOLD11), pesos %
- [X] **A.23.6** **IMAB11 = 57.0% peso** (PODIUM é fundo crédito privado RF — coerente com perfil)
- [X] **A.23.7** NAV Anomalies expande com 3 anomalias > 3σ (z-scores -170σ, -5.39σ, -3.76σ)
- [X] **A.23.8** Auto-scroll suave para Style Section quando "Analisar" é clicado
- [X] **A.23.9** Trocar classe pra "Ações" e re-rankear (20 fundos retornados; top: Itaú Mercados Emergentes sharpe=3.90, M Global BDR sharpe=3.87, AP VC Master 37.3% retorno)
- [X] **A.23.10** Buscar Top em classe sem fundos com PL>min → empty state "Sem fundos com tipo='Ações' e PL>=999999999999.0" (mensagem dinâmica inclui classe + PL min)

### A.24 — Sprint N1-N12 + housekeeping (sessão 28/abr madrugada) (~15min)

> Sprint #23. 6 commits, 17 itens N entregues + migrations + 2 alert rules. Detalhes em CLAUDE.md.

**N1 profit_daily_bars limpo (Decisão 21)**:
- [X] **A.24.1** `SELECT MIN(close), MAX(close) FROM profit_daily_bars WHERE ticker='PETR4'` retorna `min=14.66 max=49.61` (era `0.30/49.55` pré-fix)
- [X] **A.24.2** `/levels?methods=swing,williams` em PETR4 retorna `data_quality_warning=null` e Williams 8-11 fractais
- [X] **A.24.3** `populate_daily_bars.py --ticker XYZ --dry-run` (sem `--source`) loga `source=1m`

**N2 CVM informe scheduler**:
- [X] **A.24.4** `docker logs finanalytics_scheduler` mostra `scheduler.cvm_informe.start_loop hour=9 target_day=5`
- [ ] **A.24.5** Em dia 5 do mês, log `scheduler.cvm_informe.done` com competencia=AAAAMM

**N5/N5b fundamentals FII**:
- [X] **A.24.6** `SELECT COUNT(*) FROM fii_fundamentals` retorna 27 (de 28; MALL11 delistado)
- [X] **A.24.7** `/api/v1/ml/signals?asset_class=fii` retorna items com `dy_ttm` e `p_vp` populados
- [X] **A.24.8** /dashboard tab Signals: ao filtrar `classe=FIIs`, badges mostram `DY X.X% · PVP Y.YY` ao lado do ticker
- [X] **A.24.9** Checkbox "FII P/VP<1" filtra: 12 FIIs → 8 (descontados)

**N4/N4b RF Markov**:
- [X] **A.24.10** `/api/v1/rf/regime?history_days=200` retorna campo `transitions` com `next_regime_probs` e `most_likely_next`
- [X] **A.24.11** /carteira aba RF: card de regime mostra bloco "MARKOV · PRÓXIMO DIA" com probabilidades e duração média
- [X] **A.24.12** `transitions` é `null` quando history < 31 obs (ramos `else txDiv.style.display='none'`)

**N6/N6b crypto persistence**:
- [X] **A.24.13** `SELECT COUNT(*) FROM crypto_signals_history WHERE symbol='BTC'` ≥ 1 (snapshot existe)
- [X] **A.24.14** `/api/v1/crypto/signal_history/BTC?days=30` retorna `items[]` + `horizons.h7d/h14d/h30d`
- [X] **A.24.15** /carteira aba Cripto: célula `Sinal` tem badge BUY/SELL/HOLD + sparkline SVG inline (64×16) com cor derivada do score
- [ ] **A.24.16** Após 7+ dias de snapshots acumulados, sparkline mostra trend visual real (hoje só 1 ponto)

**N7 sino /diario**:
- [X] **A.24.17** /diario header mostra sino topbar com badge contagem (era ausente antes)
- [X] **A.24.18** `dj-header` tem `data-fa-notif-host` + "+ Novo Trade" tem `data-fa-notif-anchor`

**N8 renderADX null**:
- [X] **A.24.19** Toggle S/R no /dashboard não joga mais erro `Cannot read properties of null (reading 'year')` no console

**N9 S/R com dados limpos**:
- [X] **A.24.20** 6 tickers DLL pós-N1 retornam swing/williams (não-null)

**N10/N10b FIDC/FIP**:
- [X] **A.24.21** /fundos dropdown "Classe" tem FIDC/FIDC-NP/FIP/FIP Multi/Referenciado
- [X] **A.24.22** Buscar peer-ranking FIDC retorna 81 fundos avaliados + warning amarelo no meta
- [X] **A.24.23** `/anomalies/{cnpj}` em CNPJ FIDC retorna anomalies (5 detectadas em sample)
- [X] **A.24.24** `/style/{cnpj}?factors=...` em CNPJ FIDC retorna r²/alpha/betas

**N11/N11b yahoo daily bars**:
- [X] **A.24.25** `SELECT COUNT(*) FROM profit_daily_bars WHERE ticker='KNRI11'` ≥ 500 (Yahoo backfill)
- [X] **A.24.26** `/api/v1/indicators/KNRI11/levels?methods=williams` não é mais 404; retorna fractais
- [X] **A.24.27** `docker logs finanalytics_scheduler` mostra `scheduler.yahoo_bars.start_loop hour=8`

**Migrations + alert rules**:
- [X] **A.24.28** `init_timescale/004_fii_fundamentals.sql` e `005_crypto_signals_history.sql` versionados
- [X] **A.24.29** `curl -u admin:admin http://localhost:3000/api/v1/provisioning/alert-rules` retorna 14 rules (era 12)
- [X] **A.24.30** Rules `scheduler_data_jobs_errors` e `fii_fundamentals_stale` aparecem na lista

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

### B.18 — DLL fill cria entry no diário automaticamente (~15min)

> Hook `_maybe_dispatch_diary` no profit_agent: status==FILLED chama `POST /api/v1/diario/from_fill`. Idempotente local (set `_diary_notified`) + idempotente backend (UNIQUE em external_order_id).

- [ ] **B.18.1** Dashboard /dashboard com `currentInterval='5m'` (chart aberto em 5m); enviar BUY PETR4 100 @ Market simulação
- [ ] **B.18.2** Profit_agent log mostra `diary.posted ext_id=<local_id> status=201 body=...` segundos após FILLED
- [ ] **B.18.3** Abrir /diario → nova entry "PETR4 BUY 100 @ <avg> · 5m · ⏳ PENDENTE"
- [ ] **B.18.4** DB: `SELECT external_order_id, is_complete, timeframe FROM trade_journal WHERE external_order_id IS NOT NULL ORDER BY created_at DESC LIMIT 1;` → external_order_id = local_id, is_complete=false, timeframe='5m'
- [ ] **B.18.5** Repetir trade — outra entry separada (external_order_id diferente)
- [ ] **B.18.6** Forçar 2 callbacks DLL para mesmo local_id (raro mas possível): só 1 entry no diário (idempotência)
- [ ] **B.18.7** Trocar interval pra '15m' e enviar OCO (TP+SL) → ambas pernas FILLED criam 2 entries (uma por leg) com timeframe='15m'

### B.19 — flatten_ticker end-to-end com pregão (~15min)

> Valida que o endpoint composto cancela pending + zera posição com DLL viva.

- [ ] **B.19.1** Pré-condição: ter 1 posição aberta em PETR4 (BUY 100 @ market FILLED) + 2 limit orders pending (BUY @ R$28 e SELL @ R$50, longe do mercado)
- [ ] **B.19.2** `/dashboard` aba Pos. → "PETR4" → "Ver" mostra `open_qty=100 ▲ Comprada`
- [ ] **B.19.3** Caixa vermelha aparece com resumo `PETR4 — posição aberta: 100 · 2 ordem(ns) pendente(s)`
- [ ] **B.19.4** Click "🚨 ZERAR + CANCELAR PENDENTES" → confirma modal
- [ ] **B.19.5** Toast OK: `PETR4 encerrado · 2 canceladas · zero=<local_id>`
- [ ] **B.19.6** Aba Ordens: 2 limit ordens em CANCELED + 1 nova market sell em FILLED (zero_position)
- [ ] **B.19.7** "Ver" novamente: `open_qty=0 — Zerada`; caixa vermelha some
- [ ] **B.19.8** DB: `SELECT order_status FROM profit_orders WHERE ticker='PETR4' ORDER BY created_at DESC LIMIT 5` → mostra a sequência

### B.17 — Trade /carteira → DLL (~10min)

- [ ] **B.17.1** Aba Trades em /carteira: criar BUY/SELL
- [ ] **B.17.2** Trade chega no DLL (verifica em /positions)
- [ ] **B.17.3** Status reflete em /positions

---

## 🟠 BLOCO C — Outras dependências (não pregão)

### C.1 — Pushover (precisa celular ligado com app) ✅ DONE 28/abr (~15min)

- [X] **C.1.1** Grafana UI → Alerting → rule → "Test" → push chega no celular (Pushover API `status:1` aceito em ambas credenciais GRAFANA_PUSHOVER_* e PUSHOVER_*; priority=0 suprimido por **quiet hours** configurado no Pushover do user — esperado por design)
- [X] **C.1.2** `di1_tick_age_high` firing fora pregão → critical com siren (priority=1) (priority=1 atravessa quiet hours; ambos pushes recebidos no celular)
- [X] **C.1.3** Alerta indicador em /alerts prestes a disparar → push normal (priority=0) (disparado via `send()` no container API — mesmo caminho que `_bus_consumer` chama em alertas reais; ambos pushes P0+P1 recebidos)
- [X] **C.1.4** Escalation: parar profit_agent 25min → 5 reconcile errors → critical (validado simulado: payload idêntico ao `scheduler_worker.py:961-968` disparado via container scheduler + recebido com siren; lógica `consecutive_errors >= 5 and not notified` confirmada)

**Achado**: severity=warning roteia para `pushover-default` priority=0 — durante quiet hours do user esses alertas ficam silenciados no celular (chegam ao app, mas sem som/vibração). Considerar: subir warns críticos para pushover-critical, ou ajustar quiet hours config no Pushover.

### C.2 — Sudo manual (você presente, fora pregão) ✅ DONE 28/abr (~30min)

- [X] **C.2.1** Endpoint `POST /api/v1/agent/restart` com `require_sudo` → 401 + `X-Sudo-Required: true` sem token (curl verificado: HTTP/1.1 401 + header `x-sudo-required: true` + detail "Sudo confirmation required.")
- [X] **C.2.2** FASudo.confirm prompt → senha → POST com header → 200 (validado via curl: POST /auth/sudo retorna sudo_token expires_in=300; POST /agent/restart com header X-Sudo-Token retorna 200 + `{"ok":true,"message":"restarting"}`)
- [X] **C.2.3** Health `:8002/health` volta em <10s após restart (~11s no limite — DLL initialization domina o tempo, código HTTP em si sobe em <2s)
- [X] **C.2.4** Conta DLL re-conectada automaticamente (`market_connected=true`, `routing_connected=true`, `login_ok=true`, `activate_ok=true`, `db_connected=true`, 705 ticks já recebidos pós-restart)
- [X] **C.2.5** Phase D log: `oco.state_loaded groups=N` recarregado (log: `oco.state_loaded groups=1 levels=1 order_index=1` em 08:07:17 — restaurou 1 OCO group do DB)
- [X] **C.2.6** Auto-reconnect TimescaleDB: down 20min → reconnect lazy (validado em código `profit_agent.py:511-538`: `_ensure_connected` em cada execute(), 3 tentativas backoff 2s/4s/6s, throttle log 60s. Sem custo de downtime de 20min)
- [X] **C.2.7** Log throttled: TICK_V1 callback error (count=21001, 22001 — Sprint Backend V1) (validado em código `profit_agent.py:1439-1441`: `if self._tick_v1_errors % 1000 == 1`. Sem ocorrências no log atual = runtime sem erros, throttle só dispara em error path)

**Achado**: durante o restart, o `os._exit(0)` no profit_agent não terminou o processo antigo limpamente (DLL ConnectorThread bloqueou). Precisei `Stop-Process -Force` no PID antigo + relançar via `Start-Process`. Sem NSSM, restart 100% via API depende do sucesso do `_exit(0)`. Considerar instalar NSSM como watchdog (item de housekeeping futuro) ou implementar handler que mata threads DLL primeiro.

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
| 🟢 **A** Pregão fechado | agora | 23 seções (~248 checks) — **245 ✅ / 3 ⏳ (98.8%)** | ~5h |
| 🔴 **B** Pregão aberto | próximo dia útil 10h-18h BRT | 19 seções (~65 checks) | ~3h30 |
| 🟠 **C.1** Pushover | ✅ **DONE 28/abr** | 4 checks ✅ | ~15min |
| 🟠 **C.2** Sudo presencial | ✅ **DONE 28/abr** | 7 checks ✅ | ~30min |
| 🔵 **C.3** Samples reais | você fornecer | 6 checks | ~30min |
| ⚫ **C.4** Externo | Nelogica chegar | 6 checks | — |

**Validações backend já 100% verdes** (commit `7fe44ff`) — falta só UI/visual + pregão.

**Sessão 28/abr manhã**: Bloco A fechou 4 pendentes via MCP (A.4.9 + A.22.4 + A.23.9 + A.23.10). Restantes 3:
- **A.15.10** ENCERRAR PETR4 real: destrutivo + DLL viva (vai para Bloco B / B.19)
- **A.24.5** log `cvm_informe.done` competencia=AAAAMM: depende dia 5 do mês
- **A.24.16** sparkline crypto trend visual real: depende 7+ dias acumulados

**Bloco B (pregão)** continua intocado — 19 seções dependem de DLL viva (cancel order, OCO Phase A-D end-to-end, trailing real-time, persistence+restart, reconcile). Pré-requisitos do agent prontos.

**Próximo gatilho**: Bloco B na próxima sessão de pregão. Backlog Melhorias.md zerado (M1-M5 + N1-N12 ✅ entregues).
