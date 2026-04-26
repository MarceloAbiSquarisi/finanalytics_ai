# Roteiro de Testes Pendentes — FinAnalytics AI

> **Gerado**: 26/abr/2026 (dom madrugada, após super sessão de ~12h e 35+ commits)
> **Login dev**: `marceloabisquarisi@gmail.com` / `admin123` (master)
> **Stack**: API :8000 (Docker) · profit_agent :8002 (Windows) · TimescaleDB :5433
> **Cache**: SW v39 — pra ver mudanças mais recentes faça `Ctrl+Shift+R`

Documento agrupa todos os testes pendentes organizados por **dependência** (o que precisa pra rodar):

| Zona | Quando | Testes |
|---|---|---|
| 🟢 **Z1 — UI sem pregão** | agora, qualquer hora | Features deployadas hoje noite |
| 🟡 **Z2 — Pregão aberto** | 27/abr 10h-18h BRT | DLL viva, ordens reais |
| 🟠 **Z3 — Pushover** | quando celular disponível | Alerts end-to-end |
| 🟠 **Z4 — Sudo manual** | quando você presente, fora pregão | Restart profit_agent real |
| 🔵 **Z5 — Samples reais** | quando user fornecer | C6 Dividendos validação |
| ⚫ **Z6 — Externo** | bloqueado por terceiros | Nelogica 1m bars (~48h) |

---

## 🟢 Z1 — Testes UI sem pregão

> **Pode fazer agora.** Smoke das features novas dessa noite. Esperado: tudo funciona, sem console errors.

### Z1.1 — `/overview` (novo dashboard)

- [ ] Página carrega; 4 fontes (positions/watchlist/crypto/RF) renderizam cards progressivamente
- [ ] Sparklines SVG inline aparecem em todos exceto RF
- [ ] Filtros funcionam: tabs (Todos/Positions/Watchlist/Crypto/RF), search por ticker, sort, filtro ML
- [ ] Badge ML signal aparece no topo de cada card (BUY verde, SELL vermelho, HOLD dourado)
- [ ] Tooltip do badge ML mostra `h{N}d · sharpe {X.XX} · snap {YYYY-MM-DD}`
- [ ] Botão **↻** ao lado do badge → recalcula ML em tempo real (~4s, spinner girando)
- [ ] Toast OK aparece no recalc com signal+pct
- [ ] **P/L** verde/vermelho em cards de positions e crypto (`(last - avg) × qty`)
- [ ] **🛡 SL** badge aparece em PETR4 (você tem 2 OCO pending — preço esperado R$ 47,00)
- [ ] Tooltip SL: `Stop-loss ativo · stop R$ X · qty Y · N ordens`
- [ ] Status mostra `· ML 2026-04-21` (data do snapshot mais recente)
- [ ] Card click → `/dashboard?ticker=X` (RF → `/fixed-income`)
- [ ] **Seção "Últimas movimentações"** no rodapé com até 5 tx (link "ver todas →" para `/movimentacoes`)
- [ ] **Filtro ML "Apenas BUY"**: só mostra cards com signal=BUY
- [ ] Auto-refresh a cada 30s (status atualiza)

### Z1.2 — `/movimentacoes` (página nova)

- [ ] Página carrega; tabela com 8 colunas (Data, Conta, Tipo, Ticker, Valor, Status, Descrição, ✕)
- [ ] **Sort por coluna**: click em "Data" inverte ordem (↑/↓ indicator); idem para Conta/Tipo/Ticker/Valor/Status
- [ ] **Paginação**: select 50/100/200/500; "Pág X de N"; botões Anterior/Próxima funcionam
- [ ] **Filtros**: busca livre, conta (dropdown auto-populado), tipo (auto-populado dos tx), direção (entradas/saídas), status (settled/pending/cancelled), De/Até (date inputs)
- [ ] **Export CSV**: botão 📥 baixa arquivo `movimentacoes_YYYY-MM-DD.csv` com BOM UTF-8 (abre correto no Excel BR)
- [ ] **Totais** no rodapé: entradas verde, saídas vermelho, pendentes, net liquidado — totaliza TODO o filtrado (não só a página)
- [ ] **Reconciliação manual**: linha de dividendo unmatched (related_id=null) tem botão 🔗 amarelo
- [ ] Click 🔗 → modal pede ticker → POST `/wallet/transactions/{id}/reconcile` → toast OK + reload
- [ ] Botão "🖨 Imprimir" (window.print) funciona

### Z1.3 — `/carteira` (P/L + SL nas tabs)

- [ ] Tab **Posições**: 9 colunas (Ticker, Classe, Qtd, Preço Médio, **Atual**, **P/L**, **SL**, Total Investido, Trades)
- [ ] Atual carrega via `/marketdata/candles` (last close 1d) — placeholder `—` substituído
- [ ] P/L mostra valor + percentual abaixo (verde/vermelho/cinza)
- [ ] SL badge aparece se ticker tem stop-limit pending
- [ ] Tab **Cripto**: 9 colunas com **Atual** + **P/L** novos
- [ ] **BUG15 fix**: Conta exibe apelido + institution_name em **2 linhas** (não colado: "Itau A.2Itau" → "Itau A.2 / Itau")
- [ ] FAToast.ok no resgate parcial de crypto (botão 💰)
- [ ] Tab **Trades** lista trades com data/preço/qty
- [ ] Tab **Outros** já tem coluna "Ganho" (P/L implícito) — sem mudança

### Z1.4 — `/import` (C6 Dividendos UI)

- [ ] Card "💰 Importar Dividendos" verde no topo da seção "Dividendos / Rendimentos"
- [ ] Click → modal abre com 2 steps
- [ ] **Step 1**: select Conta carrega via `/wallet/accounts`; input file aceita `.csv,.ofx,.qfx,.pdf`
- [ ] Sem conta → erro "Selecione uma conta"
- [ ] Sem arquivo → erro "Selecione um arquivo CSV/OFX"
- [ ] Click "Analisar" → POST `/api/v1/import/dividends/preview?account_id=X` (FormData)
- [ ] **Step 2**: tabela com Date, Ticker, Valor, Tipo, Status, Descrição
- [ ] Summary tags: matched (verde), ambiguous (gold), unmatched (red)
- [ ] Checkbox "ignorar unmatched/ambiguous" funciona
- [ ] Botão Voltar volta pro step 1; arquivo perdido = OK (precisa reupload)
- [ ] "Confirmar Importação" → POST `/api/v1/import/dividends/commit?...&user_id=X` → toast OK
- [ ] **PDF support**: upload PDF testa `parse_pdf`. Sem pdfplumber → 400 "pdfplumber não instalado"
- [ ] Sample sintético: `printf "data,desc,valor\n10/04/2026,DIVIDENDOS RECEBIDOS PETR4,150.50" > /tmp/div.csv`

### Z1.5 — `/dashboard` Aba OCO (Phase A+B+C UI)

> **Sem disparar ordem real** (sábado/domingo sem pregão).

- [ ] Aba "Ordens" (lista) mostra ordens pending com botões 🛡 (azul) + ✕ (vermelho)
- [ ] Click **🛡** em ordem pending → modal "Anexar OCO" abre
- [ ] Modal mostra parent info: `#localId · TICKER · qty X · proteção será venda/compra`
- [ ] **Phase A — 1 nível default**: TP+SL preenchidos preview
- [ ] **Phase B — Splits**: click "+ nível (split parcial)" → 2º nível com qty=0 (sugestão = qty restante)
- [ ] Renumeração automática "Nível 1, 2, 3..." ao remover
- [ ] Sempre mantém ≥1 nível
- [ ] Counter "Total: X / parent.qty" colore: ✓ verde / ⚠ falta gold / ⚠ excede red
- [ ] Validação: sum(qty) ≠ parent → bloqueia submit
- [ ] **Phase C — Trailing**: checkbox "🔄 TRAILING (Phase C)" → trail-box collapsible aparece
- [ ] Radio R$ ↔ % muda placeholder do input
- [ ] Validação trailing: sem SL marcado → erro "trailing requer SL marcado"
- [ ] Submit envia POST `/api/v1/agent/order/attach_oco` → resposta esperada `{"error":"not found"}` (até profit_agent restart)
- [ ] **Não disparar nada real** — sem pregão, market está fechado

### Z1.6 — `/alerts` (BUG17 fix)

- [ ] Criar alerta: ROE > 15 PETR4 → 201 (não 422 nem 500)
- [ ] DB: `SELECT user_id FROM indicator_alerts WHERE ticker='PETR4' ORDER BY created_at DESC LIMIT 1` → user_id REAL (não 'user-demo')
- [ ] Listar alerts retorna só os do user logado (não da galera toda)

### Z1.7 — i18n PT/EN

- [ ] Botão **PT/EN** no topo direito da sidebar (esquerda do toggle de tema)
- [ ] Click cicla EN. Páginas que devem trocar:
  - `/dashboard` (tabs, header)
  - `/carteira` (title, subtitle, tabs, sec titles, botões + Novo)
  - `/overview` (não tem chave própria, sidebar troca)
  - `/movimentacoes` (filtros, colunas, totais)
  - `/alerts` (form labels, botões, colunas)
  - `/admin` (title, subtitle)
  - `/hub` (title, subtitle)
  - `/screener`, `/watchlist`, `/import` (titles)
- [ ] Sidebar mostra "Visão Geral" → "Overview" em EN
- [ ] Persistência via localStorage `fa_locale=en`; F5 mantém
- [ ] Texto NÃO marcado com `data-i18n` continua em PT (intencional — fall-through)

### Z1.8 — G4 auth refactor

- [ ] Logout em `/dashboard` → redirect para `/login` (FAModal.confirm Sair)
- [ ] Acessar `/carteira` sem token → redirect `/login`
- [ ] Login com "Lembrar-me 7 dias" → após 24h, página privada faz refresh silencioso (não kicka pro login)
- [ ] Token expirado + refresh válido → silent refresh sem F5
- [ ] Páginas migradas (todas exceto `/login`/`/reset-password`) usam `FAAuth.requireAuth({})`

---

## 🟡 Z2 — Pregão aberto (segunda 27/abr 10h-18h BRT)

> **Janela única.** Só validável com DLL aceitando ordens. Pré-requisito: **profit_agent restartado** com código novo (Phase A+B+C+D ativos). Comando:
>
> ```powershell
> # 1. Mata PID atual (admin)
> Stop-Process -Id <pid> -Force
> # 2. Sobe novo
> Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "src\finanalytics_ai\workers\profit_agent.py" -WindowStyle Hidden -RedirectStandardOutput ".profit_agent.log"
> ```

### Z2.1 — Dashboard DayTrade básico (§B.1)

- [ ] **Cancel order individual** (BUG7 secundário, fix 25/abr):
  - Limit BUY PETR4 R$30 (longe do mercado) → enviar
  - Em "Ordens" lista → click ✕ → status CANCELED em ~5s (polling 600/2000/5000ms)
  - Fallback `/positions/dll` em 10s consolida
- [ ] **Aba Ordem**: BUY PETR4 100 @ Market simulação → toast ok + aparece em Ordens
- [ ] **Aba OCO** (legacy): TP 35 + SL 28 stop_limit 27.50 → ordem em "Ordens" + polling
- [ ] **Aba Pos.**: search PETR4 → GetPositionV2 traz preço médio + qty
- [ ] **Cotação live PETR4**: profit_agent :8002/quotes (subscrito) → Yahoo → BRAPI (Decisão 20)
- [ ] Aba Trades em /carteira: criar BUY/SELL → confirma trade chega no DLL + status reflete em /positions

### Z2.2 — OCO Phase A+B+D end-to-end (§B.4)

> **Restart obrigatório do profit_agent antes destes testes** (rotas `/api/v1/agent/oco/*` precisam estar live).

**A) Attach OCO 1 nível smoke**:
- [ ] Limit BUY PETR4 100 @ R$30 (longe) → enviar (status PendingNew)
- [ ] Em "Ordens" → click 🛡 → modal abre, preencher TP=52, SL trigger=28, SL limit=27.50 → "Anexar OCO"
- [ ] Toast: "OCO anexado · group XXXXXXXX · 1 nível(eis) · disparará ao fill"
- [ ] DB: `SELECT * FROM profit_oco_groups WHERE status='awaiting'` → 1 row
- [ ] `/api/v1/agent/oco/groups` → 1 group; `/oco/groups/{id}` → mostra parent + 1 level
- [ ] `/api/v1/agent/oco/state/reload` → `{ok:true, groups_loaded:1}`

**B) Splits 2 níveis**:
- [ ] Cancelar do passo A
- [ ] Limit BUY VALE3 100 @ valor longe → pending
- [ ] 🛡 OCO → +nível, qty 60/40, TP1=72 SL1=58, TP2=75 SL2=58 → confirma
- [ ] Toast "2 níveis"; DB: `SELECT level_idx, qty, tp_price FROM profit_oco_levels WHERE group_id='X' ORDER BY level_idx` → 2 rows
- [ ] **Validação sum**: tentar com qty 50/40 (=90) → mensagem `Soma das qty (90) deve bater parent.qty (100)`
- [ ] **Validação proteção**: nível com TP+SL ambos desmarcados → `Nível N: marque ao menos TP ou SL`

**C) Parent fill → dispatch automático**:
- [ ] Reduzir preço da ordem mãe pra perto do mercado (ou cancelar e reenviar @ fill)
- [ ] Aguardar fill (callback assíncrono ~5s)
- [ ] `/api/v1/agent/oco/groups/{group_id}` → status `active` ou `partial` (se parcial)
- [ ] DB: `tp_order_id` e/ou `sl_order_id` populados em cada level
- [ ] Aba Ordens mostra TP (LMT sell) e SL (STP sell) novas geradas pelo dispatch
- [ ] Log profit_agent: `oco_group.dispatched group=... filled=N/M levels=K`

**D) Cross-cancel**:
- [ ] Mover preço pra cima do TP1 → quando TP1 fillar:
  - Log: `oco.tp_filled→sl_cancel group=... lv=1`
  - Level 1 SL = `cancelled` no DB
- [ ] Group continua `partial` enquanto níveis restantes ativos
- [ ] Repetir até último nível → `completed`, `completed_at` setado

**E) Persistence (Phase D)**:
- [ ] Com 1+ group active no DB, parar profit_agent (Get-Process | Stop-Process — admin)
- [ ] Subir novo: `Start-Process -FilePath .venv\Scripts\python.exe -ArgumentList src\finanalytics_ai\workers\profit_agent.py -WindowStyle Hidden -RedirectStandardOutput .profit_agent.log`
- [ ] Log inicial: `oco.state_loaded groups=N levels=M order_index=K`
- [ ] `/api/v1/agent/oco/groups` retorna mesmos groups com mesmo status (in-memory restaurado)
- [ ] Cross-cancel continua funcionando após restart

**F) Cancel manual de group**:
- [ ] Group active → `POST /api/v1/agent/oco/groups/{group_id}/cancel`
- [ ] Resposta: `{ok:true, cancelled_orders:N}` (TP+SL pending)
- [ ] DB: `status='cancelled'`, `completed_at` setado
- [ ] Aba Ordens: TP e SL daquele group ficam `CANCELED`

### Z2.3 — OCO Phase C Trailing (§B.5)

**Trail R$**:
- [ ] BUY PETR4 100 @ market → fill imediato em ~30
- [ ] Anexar OCO 1 nível: TP=35 SL=28 + ☑ Trailing R$ 0,50 (apenas SL trail)
- [ ] DB: `is_trailing=true, trail_distance=0.50, trail_pct=null`
- [ ] Mover preço de mercado pra +R$ 1 (ex: PETR4 sobe pra 31) → SL deve receber `change_order` pra trigger=30,50
- [ ] Log: `trailing.adjusted group=... lv=1 hw=31.0000 new_sl=30.5000`

**Trail %**:
- [ ] OCO com Trailing 1.5% (radio %) em VALE3
- [ ] Mover preço +2% → SL trigger atualiza proporcionalmente

**Immediate trigger** (Decisão 6):
- [ ] OCO com SL trigger 50 (acima do last 48 — long, sell), trailing R$ 0,50
- [ ] Já no submit: log `trailing.immediate_trigger group=... lv=N last=48 trigger=50 side=2`
- [ ] Ordem market sell disparada imediato pra fechar a posição
- [ ] DB: `sl_status='sent'` com novo `sl_order_id` (market)

### Z2.4 — Validações tick-dependent (§B.2)

- [ ] Aviso saldo insuficiente antes de confirmar trade BUY (UI guard real-time, depende de cotação atual)
- [ ] Indicadores em `/marketdata?ticker=PETR4` — RSI/MACD/Bollinger reflete tick recente
- [ ] `/dashboard` painel ML signals Live: tickers com BUY/SELL atualizados pós-pregão
- [ ] DI1 realtime: `di1_tick_age_high` deve ficar resolved durante pregão (tick < 120s)

### Z2.5 — Reconcile real-time (§B.3)

- [ ] Scheduler `reconcile_loop` (a cada 5min em 10h-18h BRT) executa: trigger update em `profit_orders` via DLL EnumerateAllOrders
- [ ] Order enviada via dashboard → após 5min, status no DB confere com DLL
- [ ] Se DLL retorna order com status diff, log `reconcile.discrepancy.fixed`

---

## 🟠 Z3 — Pushover (precisa celular)

> Pré-requisito: app Pushover instalado no celular + você logado.

- [ ] Grafana UI → Alerting → rule → "Test" → push chega no celular
- [ ] `di1_tick_age_high` firing (já fora pregão hoje) → critical com siren (priority=1)
- [ ] Alerta indicador em `/alerts` prestes a disparar → push normal (priority=0)
- [ ] Escalation: parar profit_agent 25min → 5 reconcile errors → critical (precisa tolerar agent down ~30min)

---

## 🟠 Z4 — Sudo manual (você presente, fora pregão)

> Restart real do profit_agent end-to-end (FASudo prompt → senha → POST → os._exit → NSSM auto-restart).

- [ ] Endpoint `POST /api/v1/agent/restart` com `require_sudo` → 401 + `X-Sudo-Required: true` sem token
- [ ] FASudo.confirm prompt → senha → POST com header → 200
- [ ] Health `:8002/health` volta em <10s após restart
- [ ] Conta DLL re-conectada automaticamente
- [ ] Phase D log: `oco.state_loaded groups=N` mostra groups in-flight recarregados
- [ ] Auto-reconnect TimescaleDB: `finanalytics_timescale` down 20min — UI manual
- [ ] Log throttled: TICK_V1 callback error usa contador (count=21001, 22001 — 1 log a cada 1000 events) — Sprint Backend V1

---

## 🔵 Z5 — Samples reais (você fornecer)

### C6 Fase 5 — Tests com BTG/XP

- [ ] Sample CSV BTG → `/import` Dividendos modal → preview → matched ≥80% das linhas
- [ ] Sample OFX BTG → idem
- [ ] Sample PDF BTG (se houver) → `parse_pdf` extrai texto + chama `_parse_line` em cada linha com keyword
- [ ] Sample CSV XP → preview correto
- [ ] Sample OFX XP → idem
- [ ] Sample PDF XP → idem
- [ ] **Edge cases reais**: linhas com R$ misturado com IRRF, datas em formato exótico, tickers com sufixo (PETR4F), valores negativos (devolução)

---

## ⚫ Z6 — Externo (bloqueado)

### Z6.1 — Nelogica 1m bars (~48h após pedido)

- [ ] Quando arquivo chegar: `runbook_import_dados_historicos.md`
- [ ] Importar via `scripts/import_historical_1m.py` para `ohlc_1m` (source='nelogica_1m')
- [ ] `populate_daily_bars.py --source 1m` → `profit_daily_bars`
- [ ] `resample_ohlc.py` 5m/15m/30m/60m → `ohlc_resampled`

### Z6.2 — Z5 ML multi-horizon

- [ ] `retrain_top20_h21.py` → adaptar pra h3, h5, h21
- [ ] Treinar pickles para top tickers em cada horizon
- [ ] `/api/v1/ml/predict_ensemble` ganha utilidade real (multi-horizon agregado por sharpe)
- [ ] Validar via `/dashboard` Aba Signals e `/overview` ML badge

---

## Comandos úteis

### Pré-flight
```bash
docker ps --filter name=finanalytics --format "{{.Names}}: {{.Status}}"
curl -s http://localhost:8000/health
curl -s http://localhost:8002/health
```

### Login + token (dev)
```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login -H "Content-Type: application/json" \
  -d '{"email":"marceloabisquarisi@gmail.com","password":"admin123"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
```

### Smoke pós-deploy (sem auth)
```bash
for r in /dashboard /carteira /overview /movimentacoes /alerts /import /screener /watchlist /admin /hub; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000${r}")
  echo "${r}: ${code}"
done
# Esperado: tudo 200
```

### Estado dos OCO groups
```bash
docker exec finanalytics_timescale psql -U finanalytics -d market_data -c \
  "SELECT status, count(*) FROM profit_oco_groups GROUP BY status;"
```

### Restart profit_agent (Windows host, admin)
```powershell
# Pega PID da porta :8002
$pid = (Get-NetTCPConnection -LocalPort 8002 -State Listen).OwningProcess
Stop-Process -Id $pid -Force
# Sobe novo
Start-Process -FilePath ".venv\Scripts\python.exe" `
  -ArgumentList "src\finanalytics_ai\workers\profit_agent.py" `
  -WindowStyle Hidden -RedirectStandardOutput ".profit_agent.log"
```

---

## Resumo

- **Imediato (Z1)**: 8 seções, ~50 testes UI sem pregão
- **Pregão (Z2)**: 5 seções, ~35 testes DLL viva (segunda 27/abr 10h-18h BRT)
- **Bloqueado**: Z3 (celular), Z4 (sudo presencial), Z5 (samples), Z6 (externo)

**Documento gerado**: 26/abr/2026 madrugada
**Próximo gatilho**: você roda Z1 agora; segunda 27/abr 10h BRT roda Z2 com pregão.
