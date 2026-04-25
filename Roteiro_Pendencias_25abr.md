# FinAnalytics AI — Pendências consolidadas

> **Data**: 25/abr/2026 (após sessão A+B)
> **Base**: `Roteiro_Testes_Pendentes_24abr.md` + sessão Playwright 25/abr fechou ~30 itens
> **Restantes**: 117 itens `[ ]` + 6 BUGs abertos
> **Login**: marceloabisquarisi@gmail.com / admin123 (master)

---

## Índice

1. [Sessão 27/abr (segunda — pregão aberto)](#1-sessão-27abr-segunda--pregão-aberto)
2. [UI manual com dados reais](#2-ui-manual-com-dados-reais)
3. [Browser smoke páginas restantes](#3-browser-smoke-páginas-restantes)
4. [Pushover (Grafana + celular)](#4-pushover-grafana--celular)
5. [Auth/RBAC/Network edge cases](#5-authrbacnetwork-edge-cases)
6. [Sudo mode + Profit Agent restart](#6-sudo-mode--profit-agent-restart)
7. [Profit Tickers UI](#7-profit-tickers-ui)
8. [C6 — Dividendos (não iniciado)](#8-c6--dividendos-não-iniciado)
9. [Bugs abertos](#9-bugs-abertos)
10. [Tech debt — sessões dedicadas](#10-tech-debt--sessões-dedicadas)
11. [Pendências da Etapa B (não cobertas em Playwright)](#11-pendências-da-etapa-b-não-cobertas-em-playwright)

---

## 1. Sessão 27/abr (segunda — pregão aberto)

**Crítico — só validável com DLL aceitando ordens em pregão.**

- [ ] `/dashboard` Aba Ordens: cancel order individual
  - Fix aplicado 25/abr (r.ok check, polling 600/2000/5000ms, fallback `/positions/dll` em 10s).
  - Cenário: limit BUY PETR4 fora de mercado → ✕ → status CANCELED em ~5s
- [ ] `/dashboard` cotação PETR4 live: primeiro tenta `profit_agent :8002/quotes` (subscrito) → Yahoo → BRAPI
- [ ] Aba Trades em `/carteira`: novo trade BUY → P&L + agrega em Posições
- [ ] Modal Histórico de transactions: filtros período + direção + include_pending toggle
- [ ] F2: Withdraw/trade/crypto que deixaria caixa < 0 → FAModal.confirm antes de submeter (UI side)
- [ ] F3: Campo valor vazio/0/negativo → input highlighted + toast warn + não submete
- [ ] Aviso de saldo insuficiente antes de confirmar trade BUY se caixa não cobre

---

## 2. UI manual com dados reais

### 2.1 Feature B — Contas unificadas (DLL)
- [ ] `/profile#invest`: conta criada mostra campos `dll_account_type/broker_id/account_id/routing_password` vazios → sem quebrar listagem
- [ ] Conectar DLL numa conta existente: botão "Conectar DLL" preenche os 4 campos + marca `dll_active=true`
- [ ] Toggle ativar/desativar DLL: reflete em `/dashboard` Aba Conta
- [ ] Simulador `dll_account_type='simulator'` → não precisa routing_password (env `PROFIT_SIM_*` fallback)
- [ ] `real_operations_allowed` admin-only: marcar conta prod → dashboard permite ordem real

### 2.2 Feature C — Cash Ledger
- [ ] `/profile#invest`: botão Depositar/Sacar numa conta → modal com valor
- [ ] POST `/api/v1/wallet/withdraw` saldo insuficiente → FAModal "Confirma saldo negativo?" antes de enviar
- [ ] Trade SELL → credita em T+1 pending
- [ ] Scheduler `settle_cash_transactions_job` 00:00 BRT → pendentes due_date ≤ hoje viram settled
- [ ] C3b ETF metadata: campos `benchmark`, `management_fee`, `performance_fee`, `liquidity_days` em `/etf` salvam + aparecem no card
- [ ] C4 Crypto D+0: aporte → debita caixa no dia (sem pending); resgate parcial → credita no dia
- [ ] C5 RF D+X (prazo do título):
  - CDB liquidez D+1 → tx pending due_date=T+1
  - LCI liquidez D+30 → pending due_date=T+30
  - Resgate antes vencimento LCI/LCA → warn + não libera caixa até due_date
  - Scheduler `settle_due_transactions_job` processa pendentes due_date ≤ hoje

### 2.3 Feature F UX (8 refinements)
- [ ] **F1** Modal Histórico:
  - Filtros período (início/fim), direção (crédito/débito/todos), include_pending toggle
  - Linha **Total** no footer (soma créditos − débitos)
  - Botão Imprimir → layout clean com FAPrint
- [ ] **F4** Apelido em listings: `/carteira` Trades, `/crypto`, cada linha mostra apelido conta ("XP Principal"); filtrar por conta funciona
- [ ] **F5** Crypto aporte/resgate: `/crypto` botões **Aportar** + **Resgate parcial** (qty validada contra holding.qty)
- [ ] **F6** RF Aplicar visível: `/fixed-income` aba "Buscar Títulos", cada linha botão verde **Aplicar** → modal cascade Conta → Portfolio → campos principal/data; SEM `window.prompt()` nativo
- [ ] **F7** Delete conta com saldo:
  - cash_balance > 0 → 409 "Há saldo em caixa" *(backend valida; UI guard manual)*
  - holdings → 409 "Há investimentos vinculados"
  - zerada + sem holdings → soft-delete `is_active=false`
- [ ] **F8** Corte à esquerda `/fixed-income`: sidebar aberta NÃO sobrepõe; colapsada content ocupa até `--sb-w-collapsed` margin; transição cubic-bezier .22s

### 2.4 G2 Rename portfolio inline
- [ ] `/profile#invest` card de conta mostra **1 carteira "Portfolio"** (não mais lista N)
- [ ] Botão **✎** ao lado do portfolio → modal/prompt rename → PATCH `/api/v1/portfolios/{id}`
- [ ] Rename gera entry em `portfolio_name_history` (old, new, when)
- [ ] Seção atualiza sem reload após rename
- [ ] `/fixed-income` aba Carteira RF: botão rename também disponível

---

## 3. Browser smoke páginas restantes

### 3.1 Golden path crítico
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

### 3.2 Smoke (carrega sem erro JS / helpers OK / FATable sort / FAEmpty CTA)
- [ ] `/correlation`, `/anomaly`, `/sentiment`, `/forecast`, `/backtest`, `/optimizer`, `/var`
- [ ] `/dividendos`, `/etf`, `/laminas`, `/fundos`, `/patrimony`
- [ ] `/opcoes`, `/opcoes/estrategias`, `/vol-surface`, `/daytrade/setups`, `/daytrade/risco`, `/tape`
- [ ] `/marketdata`, `/macro`, `/fintz`, `/import`, `/subscriptions`, `/whatsapp`

---

## 4. Pushover (Grafana + celular)

- [ ] Grafana UI > Alerting > rule > "Test" → push chega no celular
- [ ] `di1_tick_age_high` firing fora pregão → critical com siren (priority=1)
- [ ] Alerta indicador em `/alerts` prestes a disparar → push normal (priority=0)
- [ ] Escalation: parar profit_agent 25min → 5 reconcile errors → critical

---

## 5. Auth/RBAC/Network edge cases

### 5.1 Auth
- [ ] Senha errada → toast vermelho
- [ ] "Lembre-me 7d" expiry estendido (silent refresh)
- [ ] Reset password com/sem token (`/reset-password`)
- [ ] Já-logado em `/login` → redirect `/dashboard`

### 5.2 Sessão
- [ ] Apagar `localStorage.access_token` + tentar ação → redirect `/login`
- [ ] Com refresh token (Lembre-me) → silent refresh sem prompt

### 5.3 RBAC
- [ ] User comum em `/admin` ou `/hub` → 401 backend + "access denied" frontend

### 5.4 Forms
- [ ] qty negativa em trades → toast warn + não submete
- [ ] exit < entry em diário → toast warn + não submete

### 5.5 Network
- [ ] Fast 3G simulado → skeletons aparecem antes do dado
- [ ] Offline → PWA cache serve assets/CSS; `/api/*` falha graciosamente
- [ ] DB down (`docker stop finanalytics_timescale`) → toast vermelho com correlation_id

---

## 6. Sudo mode + Profit Agent restart

**Sensível — afeta host Windows. Validar com cuidado.**

### 6.1 FASudo
- [ ] `FASudo.fetch` em ação destrutiva (deletar usuário admin) → modal com senha; cache 5min
- [ ] 401 + header `X-Sudo-Required` → re-prompt senha
- [ ] `FASudo.fetchJson` retorna parseado

### 6.2 Restart Profit Agent
- [ ] Restart via `/admin` ou `/profile` → confirm password → `os._exit(0)` no agent → NSSM restart automático
- [ ] Health `:8002/health` volta em <10s após restart
- [ ] Conta DLL anterior re-conectada automaticamente (via `dll_active` persistido em DB)

### 6.3 Auto-reconnect
- [ ] `finanalytics_timescale` down 20min + subir → profit_agent reconecta em <5s sem restart manual
- [ ] Log throttled: 3 silent excepts → 1 log/min máx (não spammy)

---

## 7. Profit Tickers UI (`/profit-tickers`)

- [ ] Filtros persistem em `localStorage` ao recarregar
- [ ] Bulk activate: selecionar N tickers + botão "Ativar selecionados"
- [ ] Badge 4 estados:
  - 🟢 **Coleta Ativa** — subscribed=true + has_recent_data=true
  - 🟡 **Aguardando feed** — subscribed=true + has_recent_data=false (<30min)
  - 🔴 **Falha DLL** — subscribed=false + active=true
  - ⚪ **Inativo** — active=false
- [ ] Tooltip em cada badge explicando significado
- [ ] Colunas renomeadas (conferir vs nome anterior)
- [ ] Bulk top500: `scripts/bulk_cadastrar_top500_tickers.py` cadastrou 500 mais líquidos por mediana

---

## 8. C6 — Dividendos (não iniciado)

**Estimativa**: 1 sessão dedicada (~4-6h com UI).

- [ ] Parser de extrato (PDF/CSV/OFX) detecta "DIVIDENDOS RECEBIDOS" / "JCP" / "RENDIMENTOS"
- [ ] Auto-reconciliação: casa CNPJ+data+valor com holding em `positions` → cria `account_transactions` tipo=dividend, direction=credit, settled_at=data
- [ ] UI em `/import`: botão "Importar Dividendos" separado
- [ ] UI Movimentações global: página `/movimentacoes` (ou aba em `/carteira`) listando todas transactions agregadas (depósito/saque/trade/cripto/RF/dividendos) com filtros ticker/portfolio/direção/período
- [ ] Reconciliação manual: linha não-casada → operador anexa ao ticker correto
- [ ] Tests: import de extrato exemplo BTG e XP

---

## 9. Bugs abertos

| # | Bug | Impacto | Próximo passo |
|---|---|---|---|
| BUG2 | G4: 22 páginas com auth inline (não usam `auth_guard.js`) | Médio — perdem refresh 7d | Migrar (~4-6h); dashboard.html já migrado 25/abr |
| BUG3 | G6: `data-i18n` não aplicado nos HTMLs in-page | Baixo — sidebar+topbar OK | Migração Sprint UI gradual (Decisão 18) |
| BUG4 | `/predict_ensemble` 404 para tickers sem pickle | Baixo — h21 OK top-116 | Z5 após arquivo Nelogica |
| BUG5 | Light mode em páginas com `:root` próprio | Baixo — intencional (Decisão 19) | Redesign deliberado por página |
| BUG6 | 3 alert rules só firing após 1º increment | Baixo — esperado | — |
| BUG8 | SMTP backup ausente para Pushover | Médio — se Pushover cair, sem redundância | Configurar SMTP p/ critical |

---

## 10. Tech debt — sessões dedicadas

- [ ] **Z5**: aguardar arquivo Nelogica 1m (~48h) → `runbook_import_dados_historicos.md` + treinar pickles h3/h5/h21
- [ ] **G4 auth refactor**: 22 páginas inline → `auth_guard.js` (`dashboard.html` migrado 25/abr; 21 restantes)
- [ ] **G6 i18n spread**: aplicar `data-i18n` em forms/headers de `/dashboard`, `/carteira`, `/alerts`, `/fixed-income`
- [ ] **Redundância de alertas**: SMTP backup além Pushover (BUG8)
- [ ] **Light mode cleanup**: páginas com `:root` próprio que decidir unificar (Decisão 19)
- [ ] **Etapa 2 refactor portfolio**: revisar `/carteira`, `/fixed-income`, `/dashboard` selectors — confirmar listam só "Portfolio" por conta; atualizar copy/labels que mencionam "carteira default" ou "RF Padrão"

---

## 11. Pendências da Etapa B (não cobertas em Playwright)

### 11.1 Notificações realtime
- [ ] SSE `/api/v1/alerts/stream` aparece em tempo real — EventSource API ready, sem events ativos pra validar
- [ ] Notificações também disparam toast automático

### 11.2 PWA install
- [ ] Chrome/Edge oferece "Instalar app" (ícone na barra)
- [ ] Após instalado: ícone na taskbar/dock; abre fullscreen sem URL
- [ ] Offline: assets/css continuam (cache); `/api/*` falha (esperado)
- [ ] Refresh em página visitada → carrega instantaneamente do cache

### 11.3 FAPrint UI manual
- [ ] `/carteira`, `/performance`, `/portfolios`, `/dividendos`: botão "🖨 Imprimir" — *infra validada via JS; UI manual pra ver preview real*

### 11.4 FACharts UI manual com dados
- [ ] Tooltip com valores; legenda no bottom; cores consistentes — *Chart.js + FACharts validados; precisa página com dados pra ver render real*

---

## Comandos úteis

### Pré-flight
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

### Stop profit_agent (Windows)
```powershell
Get-Process python | Where-Object { $_.MainWindowTitle -like "*profit*" -or $_.CommandLine -like "*profit_agent*" } | Stop-Process -Force
```

---

## Status

- **Total pendente**: ~117 itens distribuídos entre 11 seções
- **Sessões estimadas**:
  - 27/abr (seg, pregão): §1 + §2.1-2.2 (~3h)
  - UI manual com dados: §2.3 + §3.1 (~4h)
  - Smoke 24 páginas: §3.2 (~3h)
  - C6 Dividendos: §8 (~5h)
  - Sudo + restart: §6 (~2h)
  - Pushover ao vivo: §4 (~30min)
- **BUGs**: 6 abertos (1 médio BUG8 SMTP backup, 5 baixos)
- **Bloqueado por externo**: Z5 (Nelogica 1m, ~48h)

---

**Documento gerado em**: 25/abr/2026 (após etapa A backend automated + etapa B Playwright helpers globais)
**Substituído**: `Roteiro_Testes_Pendentes_24abr.md` (continua válido como histórico, com checks 25/abr)
**Próximo gatilho**: 27/abr (segunda) pregão aberto → §1
