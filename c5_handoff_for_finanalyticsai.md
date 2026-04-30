# Handoff — Contrato C5 (Diário unificado) para o time do FinAnalyticsAI

**De:** time do `finanalyticsai-trading-engine`
**Para:** time do `finanalyticsai` (mantenedor de `routes/diario.py`, `static/diario.html` e do schema `public.trade_journal`)
**Data:** 2026-04-30
**Status:** spec pronta, implementação no engine prevista para Sprint R-06

---

## TL;DR

O `trading-engine` (repo separado, em construção) vai executar ordens autonomamente a partir do R-06. Para que essas execuções apareçam no Diário de Trade já existente sem duplicar UI/migrations:

1. O engine cria sua **própria** tabela `trading_engine_orders.trade_journal` no Postgres `finanalytics`, com **colunas e tipos idênticos** aos do `public.trade_journal` que vocês mantêm.
2. Vocês criam uma **VIEW `public.unified_trade_journal`** que é `UNION ALL` das duas tabelas, com uma coluna extra `source ∈ {'manual', 'engine'}`.
3. A UI `/diario` passa a consumir a VIEW. Pequena mudança visual: pill `Manual ⚪ / Engine 🤖 / Todos` no topo.
4. Tudo idempotente. Falha do engine em escrever no journal **não bloqueia** a execução (write-through best-effort).

Spec canônica e DDL: ver `trading_engine_implementacao.md` §8.5 e `contracts/owned/postgres_trade_journal_v1.sql` no repo `finanalyticsai-trading-engine`.

---

## Por que existe

Vocês já construíram um Diário de Trade completíssimo: tabela com 30+ colunas, hook `_maybe_dispatch_diary` em `profit_agent.py`, UI com equity curve, heatmap mensal estilo Stormer, workflow pendente→completa, sino topbar, analytics por setup/objetivo/psicologia. Funciona bem para fills manuais (humano operando via dashboard) e fills do `profit_agent` (DLL → POST `/from_fill`).

Quando o trading-engine começar a operar autonomamente (R-06), cada fill do robô precisa virar uma entrada no Diário **também**. Três caminhos foram considerados:

- **A.** Engine chama `POST /api/v1/diario/from_fill` igual o profit_agent → acopla engine ao FastAPI no caminho crítico, e mistura entries automáticos com manuais sem distinção visual.
- **B.** Migra o Diário inteiro para o trading-engine → exige nova FastAPI no engine + duplicar UI + migrar dados existentes. Custo alto, baixo benefício.
- **C. (escolhida)** Engine tem journal próprio em schema dedicado; vocês expõem VIEW union para a UI já existente. Acoplamento mínimo, single source of truth para analytics, separação visual manual vs engine.

---

## O que vocês precisam fazer

### Passo 1 — Replicar o DDL canônico (para vocês saberem o schema)

Copiar `contracts/owned/postgres_trade_journal_v1.sql` do repo `finanalyticsai-trading-engine` para:

```
finanalytics_ai_fresh/contracts/downstream/trading_engine_journal_v1.sql
```

A tabela em si é **criada pela migration runner do trading-engine no R-06** — vocês NÃO precisam rodar `CREATE TABLE`. Só precisam ter o arquivo em `contracts/downstream/` para validação cruzada (e para o `schema-drift-check` em CI quando ele estiver ativo).

Colunas (resumo — mesmas do `public.trade_journal` de vocês):

```
id user_id ticker direction entry_date exit_date entry_price exit_price
quantity setup timeframe trade_objective reason_entry expectation
what_happened mistakes lessons emotional_state rating tags
pnl pnl_pct is_winner is_complete external_order_id created_at updated_at
```

Tipos exatos no DDL canônico. Não há colunas novas — qualquer mudança no schema de vocês precisa de coordenação cross-repo (PR pareado).

### Passo 2 — Criar a VIEW `public.unified_trade_journal`

**Quando:** depois que a migration do trading-engine R-06 rodar e criar `trading_engine_orders.trade_journal` (a VIEW dá erro se a tabela não existir).

**Onde:** mantenham em `contracts/views/unified_trade_journal_v1.sql` no repo `finanalyticsai`.

**SQL canônico:**

```sql
CREATE OR REPLACE VIEW public.unified_trade_journal AS
SELECT
    'manual' AS source,
    id, user_id, ticker, direction, entry_date, exit_date,
    entry_price, exit_price, quantity, setup, timeframe, trade_objective,
    reason_entry, expectation, what_happened, mistakes, lessons,
    emotional_state, rating, tags,
    pnl, pnl_pct, is_winner, is_complete, external_order_id,
    created_at, updated_at
FROM public.trade_journal
UNION ALL
SELECT
    'engine' AS source,
    id, user_id, ticker, direction, entry_date, exit_date,
    entry_price, exit_price, quantity, setup, timeframe, trade_objective,
    reason_entry, expectation, what_happened, mistakes, lessons,
    emotional_state, rating, tags,
    pnl, pnl_pct, is_winner, is_complete, external_order_id,
    created_at, updated_at
FROM trading_engine_orders.trade_journal;

GRANT SELECT ON public.unified_trade_journal TO finanalytics;
-- (e para qualquer role da app que hoje lê public.trade_journal)
```

Index não é necessário em VIEW; performance vem dos índices das tabelas base (já existem em ambas).

### Passo 3 — Apontar `routes/diario.py` para a VIEW

Trocar todas as queries que hoje fazem `FROM trade_journal` para `FROM unified_trade_journal`. Pontos a tocar:

- `GET /api/v1/diario/entries` — listagem
- `GET /api/v1/diario/entries/{id}` — single
- `GET /api/v1/diario/stats` — todas as agregações (`by_setup`, `by_objective`, `by_emotion`, `equity_curve`, totals)
- `GET /api/v1/diario/stats/monthly_heatmap` — `extract('year'/'month', exit_date)` em cima da VIEW
- `GET /api/v1/diario/incomplete_count` — sino topbar

**Operações de escrita** (`POST /entries`, `PUT/DELETE /entries/{id}`, `POST /from_fill`, `/complete`, `/uncomplete`) **continuam batendo em `public.trade_journal` direto** — VIEW UNION ALL não é updatable, e a tabela do trading-engine é read-only para vocês.

Adicionar guard: se `source = 'engine'` na query (ex: usuário tenta editar uma entry do robô na UI), retornar 403/400 com mensagem clara ("entries do trading_engine são imutáveis pelo Diário; ver dashboard de operações do engine para detalhes"). Edição do qualitativo do engine — se for desejado no futuro — vira issue separada.

### Passo 4 — UI: pill de filtro por origem

No topo do `/diario`, ao lado dos filtros existentes (Ticker / Setup / Direção / Objetivo / Status):

```
Origem: [Todos] [⚪ Manual] [🤖 Engine]
```

Implementação sugerida (mesmo pattern dos pills de objetivo já existente):

- Estado local em `localStorage.fa_diario_source_filter` (default `"all"`)
- Param `?source=manual|engine` enviado para `/stats`, `/stats/monthly_heatmap`, `/entries` quando filtro != `"all"`
- Backend: filtra `WHERE source = :source` (a VIEW já entrega a coluna)
- Cards: badge `🤖` discreto em entries onde `source='engine'` (igual o badge `⚡ Day Trade` existente)
- Detalhe: campo "Origem" no header acima dos campos quantitativos

### Passo 5 — Tratamento dos campos qualitativos para entries do engine

Entries do engine vêm com a maioria dos campos qualitativos `NULL`:

- `reason_entry` — preenchido com `Signal.rationale` (string curta da estratégia, ex: "Inside bar bull, pullback média 20")
- `expectation`, `what_happened`, `mistakes`, `lessons` — `NULL`
- `emotional_state`, `rating`, `tags` — `NULL`
- `is_complete` — `TRUE` (autônomo, sem review humano)

Sugestão de UX:
- Não mostrar as 5 caixas qualitativas vazias no card de detalhe quando `source='engine'`. Substituir por um único bloco "Decisão da estratégia" com `reason_entry`.
- Botão "⏳ Concluir entrada" / "✅ Completa" — esconder ou desabilitar para entries do engine (já vêm completas).
- Estrelas de rating — esconder ou render como "N/A".

### Passo 6 — `external_order_id` e cross-link com `trading_engine_orders.orders`

O campo `external_order_id` na entry do engine é o `client_order_id` determinístico da ordem (mesmo valor que aparece em `trading_engine_orders.orders.id`). Útil para:

- Drill-down futuro: clicar na entry → mostrar a ordem original (status callback, fills parciais, OCO TP/SL)
- Reconciliation cross-system: se aparecer entry sem ordem correspondente, alarme

Este link é **opcional** para o Passo 4. Pode ficar para uma sprint futura.

### Passo 7 (CRÍTICO — habilita Passos 1-6) — `profit_agent.py`: suprimir `_maybe_dispatch_diary` para ordens originadas no engine

> 🔧 **Patch pronto pra colar:** ver [`docs/c5_finanalyticsai_implementation_patch.md`](c5_finanalyticsai_implementation_patch.md) — migration + diff dos 2 métodos + testes + smoke test end-to-end.

**Por que é crítico**: o engine, em modo integrated, **não pode carregar a ProfitDLL** (licença Nelogica = 1 cliente / 1 conta). Em vez disso, ele envia ordens para a HTTP API que vocês já expõem (`POST :8002/order/send`, mesmo endpoint do dashboard).

Quando uma dessas ordens vira FILLED, o callback de status no `profit_agent.py` dispara o `_maybe_dispatch_diary` automaticamente — que faz `POST /api/v1/diario/from_fill` em `public.trade_journal`. Mas o engine **também** vai escrever a entrada equivalente em `trading_engine_orders.trade_journal` (porque o C5 manda). Sem supressão, o **mesmo fill vira 2 entries na unified VIEW**: uma `'manual'` (do hook) e uma `'engine'` (do journal próprio), ambas com mesmo `external_order_id`. UI duplica e analytics quebra.

#### Solução: handshake `_source` no body

O engine vai incluir o flag `_source: "trading_engine"` no JSON body de toda chamada a `:8002/order/send`, `:8002/order/cancel`, `:8002/order/change`, `:8002/order/oco`, `:8002/order/flatten_ticker`. Padrão consistente com `_account_*` que vocês já injetam via `agent.py`.

Exemplo:
```json
POST :8002/order/send
{
  "env": "simulation",
  "order_type": "limit",
  "order_side": "buy",
  "ticker": "WINFUT",
  "exchange": "F",
  "quantity": 1,
  "price": 130000,
  "is_daytrade": true,

  "_source": "trading_engine",
  "_client_order_id": "linha_dagua:WINFUT:2026-04-30T10:00:00:BUY"
}
```

#### Mudanças no `profit_agent.py`

1. **`_send_order_legacy`** (recebe HTTP body): se `_source` presente, persistir junto com a ordem em `profit_orders`. Adicionar coluna `source VARCHAR(32) DEFAULT NULL` (Alembic) OU usar campo em `meta JSONB` se já existir.

2. **`_maybe_dispatch_diary`** (`profit_agent.py:~3130`): no início da função, ler `source` da row de `profit_orders` correspondente ao fill. Se `source == 'trading_engine'`, **return early**. Logar `diary.suppressed_engine_origin` com `local_order_id` para auditoria.

   ```python
   def _maybe_dispatch_diary(self, item):
       if item.get("order_status") != 2:  # FILLED
           return
       order_row = self._db_lookup_order(item["local_order_id"])
       if order_row and order_row.get("source") == "trading_engine":
           log.info("diary.suppressed_engine_origin",
                    local_order_id=item["local_order_id"])
           return  # engine cuida do journal próprio
       # ... fluxo existente para fills manuais
   ```

3. **Bonus: aceitar `_client_order_id`** como `local_order_id` se vier no body. Hoje vocês geram internamente — o engine precisa que o ID que ele envia seja o mesmo que volta no callback, pra fechar o reconcile end-to-end. Se aceitar o `_client_order_id` do body, o engine consegue rastrear sem segunda tabela de mapping.

#### Sem supressão, o que quebra

- VIEW `unified_trade_journal` mostra duplicatas (cada fill do engine aparece 2x)
- `/api/v1/diario/stats` infla `total_pnl` em 2x para fills do engine
- Heatmap mensal soma duas vezes
- UNIQUE em `external_order_id` na nossa tabela protege **nosso** lado, mas não o de vocês — vocês teriam 2 rows porque os hooks também são idempotentes só pelo lado deles

#### Sem supressão é viável?

Tecnicamente sim, com workaround grosseiro: VIEW deduplica via `WHERE NOT EXISTS (SELECT 1 FROM trading_engine_orders.trade_journal WHERE external_order_id = public.trade_journal.external_order_id)`. Mas exige join custoso em **toda** query da unified VIEW. Não recomendado.

Solução limpa = supressão no hook. Mudança pequena (~10 linhas em `_maybe_dispatch_diary` + uma migration).

---

## Quando isso acontece

| Fase | Status |
|---|---|
| Spec C5 fechada (este doc) | ✅ 2026-04-30 |
| `contracts/owned/postgres_trade_journal_v1.sql` no repo do engine | ✅ 2026-04-30 |
| §4.4 do `trading_engine_implementacao.md` documenta licença DLL e handshake `_source` | ✅ 2026-04-30 |
| **Passo 7** — `profit_agent.py` aceita `_source` e suprime `_maybe_dispatch_diary` para origin engine | 🔴 **bloqueia tudo** (precisa antes do R-06 do engine) |
| Migration cria `trading_engine_orders.trade_journal` no DB | 🟡 R-06 do trading-engine (estimativa: ~2-3 sprints) |
| Vocês criam a VIEW `public.unified_trade_journal` | 🔴 bloqueado pela migration acima |
| UI ganha pill de origem (Passos 3-5) | 🔴 bloqueado pela VIEW |
| Engine começa a popular a tabela | 🔴 R-06 concluído |

**Timing operacional:** o engine vai operar primeiro em paper trading (R-06 a R-08). Os fills paper vão para o journal igual fills live. Então o pill `🤖 Engine` aparece com dados em **modo paper** primeiro, ainda antes de qualquer ordem real do robô.

---

## Verificação

Quando os 5 passos estiverem prontos:

```sql
-- Ambas as tabelas existem
SELECT count(*) FROM public.trade_journal;
SELECT count(*) FROM trading_engine_orders.trade_journal;

-- VIEW retorna union, com coluna 'source'
SELECT source, count(*) FROM public.unified_trade_journal GROUP BY source;

-- Idempotência: re-postar fill do engine não duplica
-- (testado pelo time do trading-engine; não há nada para vocês fazerem aqui)
```

```bash
# UI
curl 'http://localhost:8000/api/v1/diario/entries?source=engine&limit=5'
# Deve retornar lista; cada item tem campo "source": "engine"

curl 'http://localhost:8000/api/v1/diario/stats?source=manual'
# Deve agregar APENAS entries com source='manual'
```

---

## Coordenação cross-repo

- **PR pareado**: quando o trading-engine for fechar o R-06 (que cria a tabela), abrir PR pareado no FinAnalyticsAI com a VIEW + UI changes. Mergear o do FinAnalyticsAI **depois** que a migration do engine rodou no banco.
- **Schema drift**: se o time de vocês mudar `public.trade_journal` (ex: adicionar coluna nova), avisar o time do engine **antes** do merge. A coluna precisa entrar nos dois schemas e na VIEW no mesmo deploy. Convenção de evolução em `trading_engine_implementacao.md` §8.5.
- **Roadmap V2**: trocar `entry_price`/`exit_price`/`pnl` de `FLOAT` para `NUMERIC(18,4)` (precisão correta para preços B3). Bloqueado pela escolha de tipos de vocês — UNION ALL exige tipos compatíveis. Considerem alinhar quando fizerem a próxima migration de tipos.

---

## Nota sobre licença ProfitDLL e fluxo de ordens

A licença Nelogica do `ProfitDLL` permite **um cliente / uma conta por ativação**. Hoje o `profit_agent.py` de vocês detém essa licença (NSSM service Windows `FinAnalyticsAgent`). O trading-engine **NÃO vai abrir uma segunda sessão na DLL** em modo `integrated` — em vez disso, vai rotear ordens via a HTTP API que vocês já expõem (`POST http://host.docker.internal:8002/order/send`, mesmo endpoint que o dashboard usa).

Detalhamento técnico completo: §4.4 do `trading_engine_implementacao.md`. Tabela completa de routing (market data live, ordens, OCO, flatten, reconcile) está lá.

Resumo:
- `profit_agent` continua sendo o **único** cliente da DLL.
- Engine, em integrated, vira só mais um cliente HTTP do `:8002` (igual o dashboard).
- Engine envia `_source: "trading_engine"` no body para diferenciar e suprimir o `_maybe_dispatch_diary` (Passo 7 acima).
- Em modo `standalone` (deploy R-10 em VPS isolada, sem profit_agent no mesmo host), o engine carrega DLL própria via bridge Windows local — cenário fora do escopo deste handoff.

---

## Contato

Spec viva em `trading_engine_implementacao.md` §8.5 (no repo `finanalyticsai-trading-engine`).
Dúvidas/ajustes: abrir issue em qualquer um dos dois repos taggeando `C5`.
