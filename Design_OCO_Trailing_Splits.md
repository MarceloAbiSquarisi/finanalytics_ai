# Design — OCO attach + Trailing Stop + Splits parciais

> **Data**: 25/abr/2026 (sábado, fora pregão)
> **Status**: spec pra revisão (sem código)
> **Aprovação necessária antes de implementar**

---

## 1. Princípios de design

1. **Simulador-friendly**: tudo testável em SIMULAÇÃO sem ordem real
2. **Idempotente**: re-attach ou re-trail num mesmo parent não duplica
3. **Observável**: cada decisão (TP cancela SL, trail ajusta stop) loga em `profit_agent.log`
4. **Persistente**: state em DB (não só memória) — sobrevive a restart do agent
5. **Fail-safe**: se DLL rejeita ajuste de trailing, mantém stop anterior; nunca deixa posição "destravada"
6. **DLL é a fonte de verdade**: estado da ordem (filled/canceled/rejected) sempre confirmado via callback `SetOrderCallback`, não via cache local

---

## 2. Data model

### 2.1 Nova tabela `profit_oco_groups` (TimescaleDB)

```sql
CREATE TABLE profit_oco_groups (
  group_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW(),
  parent_order_id BIGINT,                 -- local_order_id da ordem pai (NULL se OCO solo)
  ticker          VARCHAR(20) NOT NULL,
  exchange        CHAR(1) DEFAULT 'B',
  env             VARCHAR(20) DEFAULT 'simulation',
  account_id      VARCHAR(100) NOT NULL,
  side            VARCHAR(4) NOT NULL,     -- 'buy' | 'sell' (lado das proteções)
  total_qty       BIGINT NOT NULL,         -- qty total da posição protegida
  remaining_qty   BIGINT NOT NULL,         -- qty ainda protegida (decrementa em fills parciais)
  status          VARCHAR(20) DEFAULT 'awaiting',
  -- 'awaiting'  → aguardando parent fill (Feature 1)
  -- 'active'    → OCO disparado, monitorando preço/fills
  -- 'partial'   → algum nível executou; demais ainda ativos
  -- 'completed' → todos os níveis executados ou cancelados
  -- 'cancelled' → cancelado pelo user
  notes           TEXT
);

CREATE INDEX ix_oco_groups_parent ON profit_oco_groups(parent_order_id) WHERE parent_order_id IS NOT NULL;
CREATE INDEX ix_oco_groups_status ON profit_oco_groups(status) WHERE status IN ('awaiting','active','partial');
```

### 2.2 Nova tabela `profit_oco_levels`

Cada par TP+SL é um "nível". Splits parciais = múltiplos níveis no mesmo group.

```sql
CREATE TABLE profit_oco_levels (
  level_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  group_id        UUID NOT NULL REFERENCES profit_oco_groups(group_id) ON DELETE CASCADE,
  level_idx       SMALLINT NOT NULL,       -- 1, 2, 3 (ordem do split)
  qty             BIGINT NOT NULL,         -- qty deste nível (soma dos levels = group.total_qty)
  -- Take Profit
  tp_price        DOUBLE PRECISION,        -- preço alvo TP (NULL se só stop)
  tp_order_id     BIGINT,                  -- local_order_id do TP enviado (NULL até disparar)
  tp_status       VARCHAR(20),             -- 'pending'|'sent'|'filled'|'cancelled'|'rejected'
  -- Stop Loss
  sl_trigger      DOUBLE PRECISION,        -- preço gatilho SL (NULL se só TP)
  sl_limit        DOUBLE PRECISION,        -- preço limite stop-limit (default = trigger)
  sl_order_id     BIGINT,                  -- local_order_id do SL enviado
  sl_status       VARCHAR(20),
  -- Trailing
  is_trailing     BOOLEAN DEFAULT FALSE,
  trail_distance  DOUBLE PRECISION,        -- distância (em R$) que mantém entre last_price e SL
  trail_pct       DOUBLE PRECISION,        -- OU % se trail_distance é NULL
  trail_high_water DOUBLE PRECISION,       -- maior preço já visto (long) ou menor (short)
  -- Constraints
  CONSTRAINT chk_oco_level_has_protection CHECK (tp_price IS NOT NULL OR sl_trigger IS NOT NULL),
  CONSTRAINT chk_oco_level_qty_pos CHECK (qty > 0),
  UNIQUE (group_id, level_idx)
);

CREATE INDEX ix_oco_levels_orders ON profit_oco_levels(tp_order_id, sl_order_id);
```

### 2.3 Estrutura em memória (profit_agent)

Substitui `self._oco_pairs = {tp_id: ...}` (atual) por:

```python
self._oco_state = {
  # group_id → {qty, remaining, levels: [...], parent_id, status, ...}
  group_id_uuid: {
    "parent_order_id": int | None,
    "ticker": str,
    "side": "buy" | "sell",
    "total_qty": int,
    "remaining_qty": int,
    "status": "awaiting" | "active" | "partial" | "completed" | "cancelled",
    "levels": [
      {
        "idx": 1,
        "qty": int,
        "tp_price": float | None,
        "tp_order_id": int | None,
        "tp_status": str | None,
        "sl_trigger": float | None,
        "sl_limit": float,
        "sl_order_id": int | None,
        "sl_status": str | None,
        "is_trailing": bool,
        "trail_distance": float | None,
        "trail_pct": float | None,
        "trail_high_water": float | None,
      },
      ...
    ],
  }
}

# Reverse index pra lookup rápido no callback de order status
self._order_to_group = {
  local_order_id: (group_id, level_idx, "tp" | "sl" | "parent"),
}
```

---

## 3. API endpoints

### 3.1 `POST /api/v1/agent/order/attach_oco` (Feature 1 + 3)

Anexa OCO a uma ordem **pending** (não executada). Quando parent fill, dispara níveis.

**Request body**:
```json
{
  "env": "simulation",
  "parent_order_id": 1234567,
  "side": "sell",                 // lado das proteções; default = oposto do parent
  "levels": [
    {
      "qty": 60,
      "tp_price": 52.00,
      "sl_trigger": 47.00,
      "sl_limit": 46.50            // opcional; default = sl_trigger
    },
    {
      "qty": 40,
      "tp_price": 54.00,
      "sl_trigger": 47.00,
      "sl_limit": 46.50
    }
  ]
}
```

**Validações server-side**:
- `parent_order_id` existe em `profit_orders` E `order_status` ∈ {0=New, 10=PendingNew}
- `sum(levels[i].qty) == parent.quantity` (total bate)
- Para CADA level: `tp_price IS NOT NULL AND sl_trigger IS NOT NULL` (Feature 1: ambos obrigatórios)
- Side: `tp_price > sl_trigger` se sell (proteger long); inverso se buy
- Não pode anexar 2× ao mesmo parent (verifica `awaiting` group já existe → 409)

**Response 201**:
```json
{
  "ok": true,
  "group_id": "...",
  "status": "awaiting",
  "levels_count": 2,
  "total_qty": 100
}
```

### 3.2 `POST /api/v1/agent/order/oco` (refatorado para Feature 3)

Mesmo schema do attach mas SEM `parent_order_id`. Dispara OCO direto na posição atual.

**Backward-compat**: se body só tem `take_profit` + `stop_loss` simples (formato atual), converte pra `levels: [{qty, tp_price, sl_trigger}]` automaticamente.

### 3.3 `POST /api/v1/agent/order/trailing_stop` (Feature 2)

Pode ser SOLO (sem TP) ou parte de levels num group OCO.

**Request**:
```json
{
  "env": "simulation",
  "parent_order_id": 1234567,         // OU posição existente (ticker)
  "ticker": "PETR4",
  "side": "sell",
  "qty": 100,
  "trail_distance": 0.50,              // R$ — XOR com trail_pct
  "trail_pct": 1.0,                    // %
  "initial_trigger": 47.00             // ponto inicial; se NULL usa last_price - distance
}
```

### 3.4 `GET /api/v1/agent/oco/groups` + `GET /oco/groups/{group_id}`

Listar groups ativos + detalhe (para UI).

### 3.5 `POST /api/v1/agent/oco/groups/{group_id}/cancel`

Cancela todos os níveis ativos do group.

### 3.6 `PATCH /api/v1/agent/oco/levels/{level_id}`

Edita preços de um nível ainda não disparado (ex: ajustar TP de R$52 para R$53).

---

## 4. Fluxos de execução (state machine)

### 4.1 Feature 1 — OCO anexado a parent pending

```
[user envia ordem normal /order/send qty=100 @ R$45 limit] → parent.status=PendingNew
[user vai em "Ordens" e clica "🛡 OCO" no parent] → modal abre
[user preenche TP=52 + SL=47 (ambos obrigatórios)] → confirma
[POST /attach_oco] → cria group status=awaiting
   ↓
[DLL eventualmente: parent fill (status=Filled)] → SetOrderCallback dispara
[handler vê group.parent==filled_id] → muda group→active
[para cada level: envia TP (limit qty=L.qty @ tp_price) + SL (stop qty=L.qty @ sl_trigger/limit)]
[grava tp_order_id + sl_order_id em profit_oco_levels]
   ↓
[DLL: TP1 fill] → handler:
   - level1.tp_status=filled, qty_remaining_group -= level1.qty
   - cancela level1.sl_order_id
   - level1.sl_status=cancelled
   - se algum outro level ainda ativo: group.status=partial
   - se todos níveis fechados: group.status=completed
```

### 4.2 Feature 2 — Trailing stop

```
[group ativo OU level marcado is_trailing=true]
[thread _trail_monitor_loop a cada 500ms (mesmo loop do _oco_monitor):]
  - lê last_price do ticker (cache de ticks)
  - para cada level com is_trailing:
      - se side=sell (long): high_water = max(high_water, last_price)
                              new_trigger = high_water - trail_distance
                              se new_trigger > sl_trigger atual:
                                  change_order(sl_order_id, new_price=new_trigger)
                                  level.sl_trigger = new_trigger
                                  log "trail.adjusted"
      - se side=buy (short): low_water = min(low_water, last_price)
                              new_trigger = low_water + trail_distance
                              se new_trigger < sl_trigger atual:
                                  change_order(...)
```

**Cuidados**:
- DLL `SendChangeOrderV2` pode rejeitar (ordem em fill, mercado fechado, etc) → log warning, mantém trigger anterior
- Throttle: ajusta no máximo 1× por segundo por nível (evita flood)
- Persiste `trail_high_water` no DB a cada ajuste pra sobreviver restart

### 4.3 Feature 3 — Splits parciais

Multi-level intrínseco no data model. Quando TP1 executa, SL1 cancela mas TP2+SL2 continuam.

**Edge cases**:
- TP1 e TP2 mesmo preço (ex: ambos R$52)? Permitido — ordens enviadas em paralelo
- SL2 dispara antes de TP1? Cancela TP2+TP1 e SL1 (group.status=completed)
- Total de qty cancelada deve bater com total_qty do group

---

## 5. UI mockup textual (`/dashboard` aba OCO)

### 5.1 Aba "OCO" (refatorada)

```
┌────────────────────────────────────────────────┐
│ OCO / Trailing                                 │
├────────────────────────────────────────────────┤
│ Anexar a ordem existente:                      │
│   [▼ Selecione ordem pending] (auto-popula)   │
│                                                 │
│ OU criar OCO solo:                             │
│   Ticker [PETR4] Qty [100] Side [Sell ▼]      │
│                                                 │
│ Níveis: (mín 1)                    [+ nível]  │
│ ┌──────────────────────────────────────────┐  │
│ │ Nível 1   Qty [60]                  [✕] │  │
│ │ TP [52.00] *obrig.                      │  │
│ │ SL trigger [47.00] *obrig. limit [46.50]│  │
│ │ ☐ Trailing  dist R$ [____] OU pct [__]% │  │
│ └──────────────────────────────────────────┘  │
│ ┌──────────────────────────────────────────┐  │
│ │ Nível 2   Qty [40]                  [✕] │  │
│ │ TP [54.00]                              │  │
│ │ SL trigger [47.00]   limit [46.50]      │  │
│ └──────────────────────────────────────────┘  │
│                                                 │
│ Total: 100/100 ✓                                │
│                                                 │
│         [Cancelar]   [Confirmar OCO]            │
└────────────────────────────────────────────────┘
```

### 5.2 Aba "Ordens" — botão "🛡 OCO" inline em pending

```
| Hora  | Ticker | Side | Qty | Px    | Status   | Ação              |
|-------|--------|------|-----|-------|----------|-------------------|
| 10:23 | PETR4  | BUY  | 100 | 45.00 | Pending  | ✕ cancel  🛡 OCO |
```

Click "🛡 OCO" abre modal pré-preenchido com `parent_order_id` + qty.

### 5.3 Aba "Pos." — view de groups ativos

```
┌─ Posição PETR4 100 @ R$ 45.20 ─────────────────────────┐
│ 🛡 Group #abc123 (status: active)                       │
│   Nível 1: 60 @ TP 52.00 | SL 47.00 [TP enviado]       │
│   Nível 2: 40 @ TP 54.00 | SL 47.00 [Trailing 0.50]    │
│        ↳ trail_high=51.30, current SL ajustado=50.80    │
│                                          [✕ Cancel grupo]│
└─────────────────────────────────────────────────────────┘
```

---

## 6. Edge cases & gotchas

| Cenário | Comportamento |
|---|---|
| Parent cancelado pelo user (antes de fill) | Group em `awaiting` → cancela → status=cancelled, nada disparado |
| Parent rejeitado (DLL retorna erro) | Group → cancelled |
| Restart do profit_agent durante OCO ativo | Boot lê `profit_oco_groups WHERE status IN ('active','partial')`, hidrata `_oco_state`, reattach handlers |
| 2 níveis com mesmo preço TP | OK, ambos enviados; ordem chega via DLL FIFO |
| User edita TP enquanto SL parcial executou | Recalcula qty restante; revalida sum |
| Trailing: DLL recusa change_order | Log warning, mantém stop anterior, retry no próximo tick |
| Mercado fora pregão (fora 10-17 BRT) | Trailing pausado (não tenta change_order); endpoints retornam 200 mas warning "fora pregão" |
| Parent fill PARCIAL (ex: 60 de 100 filled) | Group.total_qty ajusta para 60; níveis re-rateiam proporcionalmente OU mantém qty original e cap em remaining (decisão a tomar) |

---

## 7. Implementação proposta (faseado)

| Fase | Escopo | DB | Backend | UI |
|---|---|---|---|---|
| **A** | Tabelas + Feature 1 (attach OCO 1 nível) | migration `ts_0003_oco_groups.sql` | `attach_oco()` + `/order/attach_oco` + parent fill handler | Modal "🛡 OCO" inline em Ordens pending |
| **B** | Splits parciais (Feature 3) — múltiplos níveis | — (data model já suporta) | refator handlers pra iterar levels | UI dinâmica "+ nível" |
| **C** | Trailing stop (Feature 2) | (campos já no schema) | `_trail_monitor_loop` + `change_order` integration | Checkbox + inputs |
| **D** | Persistência + restart safety | — | boot `_load_oco_state_from_db()` | — |

**Total**: ~7-8h. Cada fase é deployable independente.

---

## 8. Decisões pendentes (precisa input antes de codar)

1. **Trailing: distância em R$ XOR %**? Ou suportar ambos com toggle?
2. **Parent fill PARCIAL**: levels re-rateiam proporcional ou cancelam tudo?
3. **Permitir level só com TP (sem SL)**? Roteiro diz "nenhuma das duas opcional" → forçar ambos sempre. Confirmar.
4. **OCO solo (sem parent) deve forçar ambos TP+SL** ou só Feature 1 obriga?
5. **Limite máximo de níveis** (3? 5? sem limite)?
6. **Trailing: se mercado já passou do trigger inicial?** (ex: SL inicial 47, last 46.50 → dispara imediato OU rejeita)?

---

## 9. Riscos

| Risco | Mitigação |
|---|---|
| DLL não suporta `SendChangeOrderV2` em todas as situações (pode rejeitar) | Try/except + retry; mantém stop anterior se falha |
| Race condition: parent fill + cancel concomitantes | Lock no group_id durante state transitions |
| Memória cresce se groups completed não são purgados | Job cleanup periódico (>30d completed → delete) |
| User cria múltiplos attach_oco no mesmo parent | Constraint unique `WHERE status='awaiting'` por parent_order_id |
| Migration `ts_0003` em hypertable → tuple decompress limit | `SET LOCAL max_tuples_decompressed_per_dml_transaction = 0` (já vimos isso) |

---

## Próximos passos

1. **Você revisa este doc**
2. Responde as 6 decisões da §8
3. Escolhe pace: implementar tudo Fase A→D ou parar entre fases
4. Implemento na ordem A → B → C → D, deployable de fase em fase
