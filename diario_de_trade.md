# Diário de Trade — Funcionalidades Implementadas

Documento consolidado das funcionalidades do módulo **Diário de Trade** do FinAnalytics AI.
Atualizado em 2026-04-30.

---

## 1. Visão Geral

O Diário de Trade é o módulo que combina **registro qualitativo + quantitativo** de cada operação executada. Substitui planilhas (estilo Stormer "Resumo dos trades") por um sistema integrado com a DLL Profit (auto-cria entradas no FILLED), com analytics inline (equity curve, heatmap mensal, performance por setup, psicologia) e workflow de "pendente → completa" para forçar disciplina de revisão pós-trade.

**Camadas:**
- **DB** — tabela `trade_journal` (PostgreSQL `finanalytics`)
- **Backend** — `routes/diario.py` (FastAPI) + `repositories/diario_repo.py` (SQLAlchemy async)
- **Hook DLL → API** — `_maybe_dispatch_diary` em `profit_agent.py` (fire-and-forget, idempotente)
- **UI** — `static/diario.html` (SPA vanilla JS, ~1600 linhas, sub-menu Day Trade)
- **Observabilidade** — contador no sino topbar (`FANotif.setSystemBadge`)

---

## 2. Schema (`trade_journal`)

Modelo declarado em `infrastructure/database/repositories/diario_repo.py::DiarioModel`. Auto-criado via `Base.metadata.create_all()` no startup; alterações incrementais via Alembic.

| Coluna | Tipo | Notas |
|---|---|---|
| `id` | UUID (string 36) PK | gerado em Python |
| `user_id` | VARCHAR(100) NOT NULL | default `"user-demo"`, indexado |
| **Quantitativo** | | |
| `ticker` | VARCHAR(20) NOT NULL | uppercased automaticamente, indexado |
| `direction` | VARCHAR(4) NOT NULL | `BUY` ou `SELL` |
| `entry_date` | TIMESTAMPTZ NOT NULL | indexado |
| `exit_date` | TIMESTAMPTZ NULL | NULL = trade aberto |
| `entry_price` | FLOAT NOT NULL | > 0 (validado Pydantic) |
| `exit_price` | FLOAT NULL | > 0 quando preenchido |
| `quantity` | FLOAT NOT NULL | > 0 |
| **Setup** | | |
| `setup` | VARCHAR(50) NULL | livre — UI sugere ~20 setups (pin_bar, setup_91, larry_williams, breakout, rsi, …) |
| `timeframe` | VARCHAR(10) NULL | 1m/5m/15m/30m/1h/1d |
| `trade_objective` | VARCHAR(20) NULL | `daytrade` \| `swing` \| `buy_hold` (Alembic 0019) |
| **Qualitativo** | | |
| `reason_entry` | TEXT | "Por que entrou?" |
| `expectation` | TEXT | "O que esperava que acontecesse?" |
| `what_happened` | TEXT | "O que de fato aconteceu?" |
| `mistakes` | TEXT | erros cometidos |
| `lessons` | TEXT | lições aprendidas |
| `emotional_state` | VARCHAR(30) | calm / focused / anxious / fearful / fomo / greedy / revenge / tired / overconfident |
| `rating` | INT 1-5 | qualidade da execução (estrelas) |
| `tags` | VARCHAR(500) | comma-separated, serializa de/para `list[str]` |
| **Calculados** | | armazenados para queries eficientes |
| `pnl` | FLOAT | `(exit-entry)*qty` (BUY) ou `(entry-exit)*qty` (SELL) |
| `pnl_pct` | FLOAT | `pnl / (entry*qty) * 100` |
| `is_winner` | BOOL | `pnl > 0` |
| **Workflow** | | Alembic 0020 |
| `is_complete` | BOOL NOT NULL DEFAULT FALSE | flag de "qualitativa preenchida" |
| `external_order_id` | VARCHAR(64) UNIQUE NULL | id da ordem origem (DLL `local_order_id`) — chave de idempotência do hook |
| **Metadata** | | |
| `created_at` / `updated_at` | TIMESTAMPTZ | `onupdate` automático |

Índice único parcial: `ux_trade_journal_external_order_id ON trade_journal(external_order_id) WHERE external_order_id IS NOT NULL` — garante idempotência no hook sem bloquear entries manuais.

---

## 3. Endpoints REST (`/api/v1/diario`)

Todos retornam JSON. `user_id` default = `"user-demo"` (multi-tenant pendente).

### CRUD básico
| Método | Path | Descrição |
|---|---|---|
| GET | `/entries` | Lista paginada com filtros: `ticker`, `setup`, `direction`, `trade_objective`, `is_complete`, `limit≤500`, `offset` |
| POST | `/entries` | Cria entrada (Pydantic `EntryCreate`) — recalcula P&L, `is_winner` |
| GET | `/entries/{id}` | Retorna 1 entrada; 404 se inexistente ou de outro user |
| PUT | `/entries/{id}` | Atualização parcial (Pydantic `EntryUpdate`); recalcula P&L |
| DELETE | `/entries/{id}` | 204 No Content |

### Workflow de preenchimento
| Método | Path | Descrição |
|---|---|---|
| POST | `/entries/{id}/complete` | seta `is_complete=true` |
| POST | `/entries/{id}/uncomplete` | seta `is_complete=false` |
| GET | `/incomplete_count` | retorna `{count, user_id}` — usado pelo sino topbar |

### Hook automático (DLL → diário)
| Método | Path | Descrição |
|---|---|---|
| POST | `/from_fill` | Idempotente por `external_order_id`. Chamado pelo `profit_agent` quando uma ordem entra em FILLED. Retorna `{entry, created}` (created=False quando duplicata) |

### Analytics
| Método | Path | Descrição |
|---|---|---|
| GET | `/stats` | Snapshot agregado (ver §6). Aceita `?trade_objective=` |
| GET | `/stats/monthly_heatmap` | Matriz year × month de P&L, estilo planilha Stormer (ver §7) |

### Página HTML
| Método | Path | Descrição |
|---|---|---|
| GET | `/diario` | SPA `static/diario.html` (montada na rota raíz, não em `/api/v1`) |
| GET | `/api/v1/diario/page` | Rota alternativa que serve o mesmo HTML |

---

## 4. Hook automático: FILLED → entrada pré-preenchida

Pipeline disparado por **toda** ordem que transita para `status=2` (FILLED) na callback de status do `profit_agent`:

```
DLL trading_msg_cb → db_worker.put → _maybe_dispatch_diary → POST /from_fill
                                                     ↓
                                           thread daemon (não bloqueia DLL)
```

**Detalhes:**
- `_maybe_dispatch_diary(item)` em `profit_agent.py:3130` checa `order_status==2` + `avg_price` + `traded_qty` presentes.
- Lookup em `profit_orders` (já atualizada na callback) busca `ticker` e `order_side`. Aceita `order_side` como int (1=Buy, 2=Sell) **ou** string legacy ("buy"/"sell") — fix `e41d286`.
- Idempotência **dupla**:
  1. Set local `_diary_notified` evita re-disparo na mesma sessão (P4-aware: callback DLL pode ser chamado múltiplas vezes).
  2. UNIQUE constraint em `external_order_id` no DB: `repo.create_from_fill` retorna `(existing, False)` se já existir.
- Entrada criada com `is_complete=False` → aparece com badge ⏳ PENDENTE no UI até usuário preencher qualitativa e clicar "Concluir entrada".
- Configurável via env: `PROFIT_DIARY_HOOK_URL` (default `http://localhost:8000/api/v1/diario/from_fill`) e `PROFIT_DIARY_USER_ID` (default `"user-demo"`).
- Captura `timeframe` registrado em `_tf_by_local_id` (preenchido no `_send_order_legacy`).

---

## 5. UI — `/diario`

SPA single-file (~1600 linhas) com layout 2-colunas dentro do template padrão (sidebar Day Trade > Diário).

### 5.1 Header de stats (topo)
- Total de trades (fechados)
- Win Rate (verde ≥50%, gold ≥40%, vermelho <40%)
- P&L Total (formatado pt-BR)
- Rating médio (estrelas)
- ⏳ **Pendentes** (chip amarelo, só aparece se >0; clique filtra incompletas)
- Botão `+ Novo Trade` (abre modal de criação)

### 5.2 Coluna esquerda — Lista filtrável
Filtros (5):
- **Ticker** (input texto, busca substring)
- **Setup** (select com ~20 opções organizadas em optgroups: Price Action / Clássicos BR / Tendência / Osciladores / Outros)
- **Direção** (BUY / SELL)
- **Objetivo** (Day Trade / Swing / Buy & Hold)
- **Status** (Incompletas / Completas)

Cada card mostra: ticker (azul, destaque), badge BUY/SELL, badge ⏳ PENDENTE quando aplicável, badge de objetivo (cor Day Trade=vermelho / Swing=azul / Buy & Hold=verde), setup, P&L (verde/vermelho), data de entrada, timeframe, emoji emocional, rating em estrelas, tags (até 3 visíveis). Cards pendentes têm borda esquerda 3px amarela.

### 5.3 Coluna direita — Dashboard com 6 abas

#### Aba 1: Equity Curve
- 2 insight cards (winners verdes / losers vermelhos)
- Chart line: P&L acumulado por data (`exit_date` ASC), cor verde se equity final ≥0
- Chart bar: P&L por operação (cores condicionais)

#### Aba 2: Por Setup
- Chart bar horizontal: P&L total por setup
- Tabela: Setup | Trades | Win% | P&L Total | P&L médio% — sortable via `data-fa-table` (FATable auto-init)

#### Aba 3: Objetivo (Day Trade / Swing / Buy & Hold)
- Insight cards por objetivo (cores específicas)
- Chart bar horizontal: P&L total por objetivo
- Tabela detalhada por objetivo
- **Filtro pill global** no topo do dashboard: pills "Todos / ⚡ Day Trade / 📈 Swing / 🏛 Buy & Hold". Quando selecionado, filtra **Equity / Setup / Psicologia / Mensal** (não a tabela de comparação por objetivo). Persistido em `localStorage.fa_diario_obj_filter`.

#### Aba 4: Psicologia
- Doughnut chart: distribuição emocional
- Bar chart: P&L médio por estado emocional
- **Insights automáticos**:
  - 🏆 Setup mais lucrativo
  - ✅ Melhor estado emocional ("Proteja esse estado")
  - ⚠️ Pior estado emocional ("Evite operar nesse estado")

#### Aba 5: Mensal (Heatmap estilo planilha Stormer)
Matriz **Ano × Mês** com células coloridas:
- Linhas: anos (mais recente em cima)
- Colunas: Jan-Dez + Total marginal por ano
- Footer: Total marginal por mês (somatório de todos os anos) + Grand Total
- Intensidade da cor escala com `|pnl| / max_abs_pnl_celula` — 0.10 a 0.65 alpha
- Tooltip por célula: P&L formatado, trades, wins, win rate
- Legenda visual no rodapé
- Header `hm-meta`: total de trades fechados, P&L global, WR global, e nome do filtro de objetivo se aplicado

#### Aba 6: Detalhe (selecionada ao clicar em card da lista)
- Header: ticker grande + direção + objetivo + setup + P&L + P&L%
- Grid 3×2: Entrada / Saída / Quantidade / Data entrada / Data saída / Timeframe
- 5 caixas qualitativas: Por que entrou / O que esperava / O que aconteceu / Erros / Lições
- Footer: emoji emocional, rating em estrelas, tags
- Botões: **⏳ Concluir entrada** (amarelo, quando pendente) ou **✅ Completa** (verde, quando completa, alterna de volta) + **Editar**

### 5.4 Modal Novo/Editar Trade
Formulário em 4 seções:
1. **Dados Quantitativos**: Ticker, Direção, Timeframe, Objetivo, Datas entrada/saída, Setup, Preços, Quantidade
2. **Análise Qualitativa**: 5 textareas (motivo / expectativa / aconteceu / erros / lições)
3. **Estado Emocional & Avaliação**: Select emocional (9 estados com emoji) + rating 1-5 estrelas clicáveis
4. **Tags**: chips dinâmicos com input "Enter ou vírgula para adicionar"; backspace remove última

Validação: Ticker, Preço Entrada, Quantidade e Data Entrada são obrigatórios — toast de erro via `FAToast.err`.

---

## 6. Endpoint `/stats` — Estrutura retornada

```json
{
  "total_entries": 42,
  "closed_trades": 35,
  "open_trades": 7,
  "winners": 22,
  "losers": 13,
  "win_rate": 62.9,
  "total_pnl": 4530.50,
  "avg_rating": 3.8,
  "by_setup": [
    {"setup": "pin_bar", "trades": 12, "total_pnl": 1850.0, "avg_pnl_pct": 1.4, "win_rate": 75.0},
    ...
  ],
  "by_objective": [
    {"objective": "daytrade", "trades": 25, "total_pnl": 2100.0, "avg_pnl_pct": 0.8, "win_rate": 60.0},
    {"objective": "swing", ...},
    {"objective": "buy_hold", ...}
  ],
  "by_emotion": [
    {"state": "calm", "count": 15, "avg_pnl": 180.5},
    {"state": "fomo", "count": 4, "avg_pnl": -85.0},
    ...
  ],
  "equity_curve": [
    {"date": "2026-01-10T15:00:00+00:00", "equity": 300.0, "pnl": 300.0},
    {"date": "2026-01-12T14:30:00+00:00", "equity": 215.0, "pnl": -85.0},
    ...
  ]
}
```

Filtro `?trade_objective=daytrade|swing|buy_hold` aplica em **todas** as queries **exceto** `by_objective` (que continua sendo o eixo de comparação).

---

## 7. Endpoint `/stats/monthly_heatmap`

Matriz year × month estilo planilha Stormer "Resumo dos trades":

```json
{
  "years": [2025, 2026],
  "months": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
  "by_year": {
    "2026": {
      "1": {"pnl": 1200.0, "trades": 8, "wins": 5, "win_rate": 62.5},
      "2": {"pnl": -340.0, "trades": 6, "wins": 2, "win_rate": 33.3},
      ...
    }
  },
  "year_totals": {
    "2026": {"pnl": 4530.50, "trades": 35, "wins": 22, "win_rate": 62.9}
  },
  "month_totals": {
    "1": {"pnl": 1450.0, "trades": 11, "wins": 7, "win_rate": 63.6},
    ...
  },
  "grand_total": {"pnl": 6800.0, "trades": 80, "wins": 48, "win_rate": 60.0},
  "trade_objective": null
}
```

Implementado via `extract('year'/'month', exit_date)` + `func.sum(pnl)` agrupado. Trades sem `exit_date` ou sem `pnl` (abertos) são excluídos.

---

## 8. Workflow Pendente → Completa

**Por que existe:** trade FILLED automaticamente cria entry com qualitativa em branco. O Diário só vira útil se o usuário **pós-revisa** cada trade. O flag `is_complete` força essa disciplina:

1. Hook `_maybe_dispatch_diary` cria entry com `is_complete=False`.
2. UI mostra card com badge ⏳ PENDENTE + borda amarela.
3. Chip amarelo no header **Pendentes: N** clicável → filtra apenas incompletas.
4. **Sino topbar** (`FANotif.setSystemBadge`) recebe `key='diary_pending', count: N, href: '/diario'` → badge de notificação compartilhado com o resto do sistema.
5. Usuário abre o detalhe do trade, edita os campos qualitativos, clica **⏳ Concluir entrada** → POST `/complete` → vira ✅ Completa.
6. Se errar, pode clicar de novo no badge ✅ → POST `/uncomplete` reverte.

`refreshIncompleteCount()` é chamado em `init()`, após `saveEntry`, `deleteEntry` e `toggleComplete` para manter contadores sincronizados.

---

## 9. Filtros e categorização

### Setups suportados (UI sugere; campo livre no DB)
- **Price Action**: pin_bar, inside_bar, engulfing, fakey
- **Clássicos BR**: setup_91 (Stormer), larry_williams, turtle_soup, hilo
- **Tendência**: breakout, pullback_trend, first_pullback
- **Osciladores**: rsi, macd, combined (RSI+MACD), bollinger, ema_cross, momentum
- **Outros**: gap_and_go, bollinger_squeeze, outro

### Estados emocionais
9 opções com emoji + cor: calm 😌 (verde), focused 🎯 (azul), anxious 😰 (gold), fearful 😨 (red), fomo 😤 (laranja), greedy 🤑 (laranja escuro), revenge 🔥 (vermelho), tired 😴 (cinza), overconfident 😎 (lilás).

### Objetivo de trade (eixo ortogonal a setup)
- ⚡ **daytrade**: entra/sai mesmo pregão (vermelho)
- 📈 **swing**: dias a semanas (azul)
- 🏛 **buy_hold**: longo prazo (verde)

Distribuições (e win rates) são naturalmente diferentes entre os 3 horizontes — daí o filtro pill no dashboard.

---

## 10. Observabilidade

- **Logs estruturados**: `diario.entry.created`, `diario.from_fill`, `diary.posted`, `diary.lookup_failed`, `diary.dispatch_error`, `diary.post_http_error`, `diary.post_error`.
- **Sino topbar**: badge de notificação para `diary_pending` (compartilhado com outras keys do sistema via `FANotif`).
- Não há alert rule Grafana específica para o diário (até 30/abr/2026). Existe alerta `ml_snapshot_stale` para o pipeline ML, mas o diário é input manual + hook DLL, sem job automático para monitorar idade.

---

## 11. Tests

`tests/unit/infrastructure/test_diario_repo.py` — SQLite in-memory via `sqlalchemy.ext.asyncio`, sem PostgreSQL real.

**Cobertura (28 cenários):**
- `TestCreate` — persistência, P&L BUY/SELL, trade aberto sem P&L, ticker uppercased, tags serialização
- `TestGet` — existing, nonexistent → None, multi-tenant (wrong user → None)
- `TestList` — todos, filtro ticker/setup/direction, user vazio
- `TestUpdate` — recalcula P&L em update de exit_price, nonexistent → None, ticker uppercase em update
- `TestDelete` — true/false, remove do DB
- `TestStats` — empty, win_rate, equity_curve ordenada, by_setup, by_emotion

Skip gracioso se `diario_repo` não importável (mensagem aponta `_fix_diario.ps1`).

---

## 12. Migrations Alembic

| Revision | Conteúdo |
|---|---|
| (criação inicial) | tabela criada via `Base.metadata.create_all()` no startup — sem alembic dedicado |
| `0019_diario_trade_objective` (2026-04-27) | `ADD COLUMN trade_objective VARCHAR(20)` IF NOT EXISTS. Validação no Pydantic, não CHECK |
| `0020_diario_is_complete` (2026-04-27) | `ADD COLUMN is_complete BOOLEAN NOT NULL DEFAULT FALSE` + `external_order_id VARCHAR(64)` + `CREATE UNIQUE INDEX ux_trade_journal_external_order_id ... WHERE external_order_id IS NOT NULL` |

Idempotentes (`IF NOT EXISTS`) — seguras em ambientes onde `ALTER` manual já rodou.

---

## 13. Histórico de evolução

Commits chave:
- `c731a2c` — `feat(diario): implementa Diario de Trade completo` — base CRUD + UI + stats
- `1716da4` — fix: remove entrada duplicada do Diário no menu (já está em Day Trade)
- `89aea66` — `FAEmpty` com CTA "+ Novo Trade" para lista vazia
- `1b006d3` — fix SyntaxError em `/diario` + bump SW v4→v5
- `bba4fbc` — refactor G4: migra para `FAAuth.requireAuth` (silent refresh + lembre-me 7d)
- `a4570a5` — i18n G6 spread em 5 páginas incluindo diário (`data-i18n="diario.title"`, etc)
- `3573db1` — features /diario M1-M5
- `f6e6229` — heatmap mensal (planilha Stormer)
- `568e9a3` — diary hook P4-aware no profit_agent + cleanup stale pending orders
- `e41d286` — fix: diary hook trata `order_side` smallint (era bug `'int has no .lower'`)

Roadmap implícito (não implementado):
- Multi-tenant real (hoje `user-demo` hardcoded)
- Alert rule Grafana para `incomplete_count > N` por mais de X dias
- Export CSV/PDF do diário (planilha completa estilo Stormer)
- Anexar screenshot do gráfico no momento da entrada
- Vincular entry → ordem real em `profit_orders` (FK) para drilldown completo
- Cálculo de drawdown máximo na equity curve
- Streak (sequência de wins/losses) e payoff ratio nos stats
