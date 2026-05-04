# profit_agent — Referência (Endpoints + Arquitetura + Gotchas DLL)

> Para troubleshooting operacional, ver `runbook_profit_agent.md`. Este doc é referência da API/código.

## Endpoints HTTP (`:8002`)

```
GET  /health                    → ok:true
GET  /status                    → conexão, ticks, ordens
GET  /ticks/{ticker}             → últimos N trades do ticker
GET  /summary                    → snapshot OHLC diário via DailyCallback
GET  /book/{ticker}              → depth book
GET  /orders                    → lista ordens do banco
POST /order/send                → envia ordem (limit/market/stop)
POST /order/cancel              → cancela por local_order_id
POST /order/cancel_all          → cancela todas
POST /order/change              → altera preço/qty (SendChangeOrderV2)
POST /order/oco                 → OCO: TP (limit) + SL (stop-limit) vinculados
POST /order/zero_position       → zera posição (SendZeroPositionV2)
POST /order/flatten_ticker      → cancel pending + zero_position
GET  /oco/status/{tp_id}        → status par OCO
GET  /positions                 → posição via banco
GET  /positions/dll             → EnumerateAllOrders (DLL) + reconcilia banco
GET  /positions/assets          → EnumerateAllPositionAssets (DLL)
GET  /position/{ticker}         → GetPositionV2 (DLL) ?type=1 (DayTrade) | 2 (Swing)
GET  /orders/reconcile          → reconcilia ordens DB vs DLL
POST /collect_history           → coleta histórico de ticks
GET  /metrics                   → Prometheus exposition
POST /restart                   → restart via NSSM (requer sudo_token via proxy /api/v1/agent/restart)
POST /subscribe                 → subscribe ticker à DLL (body `{"ticker":"X","exchange":"B|F"}`)
```

Todos acessíveis via `/api/v1/agent/...` no proxy FastAPI :8000 (resolve bloqueio Kaspersky).

## Handshake C5 (trading-engine ↔ profit_agent)

`/order/send` aceita 2 campos opcionais no body p/ origens externas (engine/auto_trader):
- `_source` → persiste em `profit_orders.source`; suprime hook do diário se `'trading_engine'` (engine mantém journal próprio).
- `_client_order_id` → persiste em `cl_ord_id`; resposta ecoa para reconcile sem 2ª tabela.

Spec: `c5_handoff_for_finanalyticsai.md`. Migration: `alembic/versions/ts_0003_profit_orders_source.py`.

## Arquitetura

### Classes principais
- `ProfitAgent` — wrapper da DLL + HTTP server (BaseHTTPRequestHandler, em `profit_agent_http.py`)
- Structs ctypes: `TConnectorAccountIdentifier`, `TConnectorAssetIdentifier`, `TConnectorOrder`, `TConnectorOrderOut`, `TConnectorAssetIdentifierOut`, `TConnectorTradingAccountPosition` (em `profit_agent_types.py`)

### Métodos críticos
```python
agent._send_order_legacy(params)   # envia ordem via SendOrder DLL
agent._get_order_details(oid)      # 2-pass GetOrderDetails (fix 04/mai)
agent.send_oco_order(params)       # OCO manual (TP + SL)
agent.get_oco_status(tp_id, env)   # status par OCO
agent._oco_monitor_loop()          # thread daemon 500ms auto-cancela
agent.get_positions_dll(env)       # EnumerateAllOrders — assinatura CORRETA:
                                   # POINTER(TConnectorAccountIdentifier), c_ubyte, c_long, callback
agent.enumerate_position_assets()  # EnumerateAllPositionAssets
agent.get_position_v2(ticker, ...) # GetPositionV2 — ok=False é NORMAL, dados na struct
agent.cancel_order(params)         # SendCancelOrderV2
agent.change_order(params)         # SendChangeOrderV2
agent._retry_rejected_order(id)    # P1 retry 5 attempts (refactor 04/mai)
agent._hard_exit()                 # kernel32.TerminateProcess (mata DLL ConnectorThread)
agent._kill_zombie_agents(...)     # netstat scan + taskkill no boot
```

## Bugs conhecidos / gotchas da DLL

- `GetPositionV2` retorna `ok=False` mas dados estão corretos na struct — não tratar como erro
- `EnumerateAllOrders`: primeiro param DEVE ser `POINTER(TConnectorAccountIdentifier)`, não `c_int, c_wchar_p`
- Callbacks DEVEM ser armazenados em `self._gc_*` para evitar garbage collection
- DLL é 64-bit — Python deve ser 64-bit
- Callbacks rodam na ConnectorThread — **read-only OK** (ex: `GetOrderDetails`); evitar mutations (ex: `SendOrder`) que disparem novos callbacks
- `open_side=200` na posição = zerada (valor byte residual da DLL)
- **Broker subconnection blips** (P1, refactor 04/mai): broker rejeita SendOrder com codes 1/3/5/7/9/24 + msg "Cliente não está logado" / "logado" / "timeout". Mitigado via auto-retry em `trading_msg_cb` + fallback retry no `watch_loop` (max 5 attempts, delay 1.5s). Tunável via `PROFIT_RETRY_*` env vars.
- **TConnectorOrder callback layout** (P4 fix): `SetOrderCallback` recebe APENAS `TConnectorOrderIdentifier` 24B. Status/ticker/qty/text_message rich vêm via `dll.GetOrderDetails(byref(order))` chamado de dentro do `order_cb` (2-pass pattern oficial Nelogica — ver `feedback_get_order_details_callback.md`).
- `r.OrderID.LocalOrderID` em `trading_msg_cb` vem 0 em alguns codes. Use fallback `_msg_id_to_local` mapping populado em `_send_order_legacy`.
- `os._exit(0)` não termina processo limpo — DLL ConnectorThread C++ bloqueia. Sempre usar `_hard_exit()` (`kernel32.TerminateProcess`).
- **Lot size B3** stocks default 100; broker rejeita silenciosamente `Risco Simulador: Quantidade da ordem deve ser múltiplo do lote`. Strategies devem retornar qty múltiplo + ideal validar local pre-`SendOrder`.
- **`/restart` NameError silencioso** (fix 04/mai): handler em `profit_agent_http.py` precisa import explícito de `_hard_exit` de `profit_agent.py`. Sem isso, processo não morre. Validar com `Get-CimInstance Win32_Process | Select CreationDate` após /restart.

## Order Types / Side / Status

- Type: `1` = Market, `2` = Limit, `4` = StopLimit
- Side: `1` = Buy, `2` = Sell
- Status: `0` = New, `1` = PartialFilled, `2` = Filled, `4` = Canceled, `8` = Rejected, `10` = PendingNew

## Validity / Time In Force

- DDL: `profit_orders.validity_type VARCHAR(8) DEFAULT 'GTC'` + `validity_date TIMESTAMPTZ`
- DLL ProfitDLL não expõe ValidityType no SendOrder — enforcement é local via `gtd_enforcer_loop` no scheduler (60s, cancela GTD expirada via `/order/cancel`; fallback `status=8 + error='gtd_expired_cancel_failed'`)

## Resilience patterns

Operam em condições de simulator/broker degradado (callback final falha, ordens stuck, sessão piscando):
- `_get_last_price` (cache + fallback profit_ticks)
- `_watch_pending_orders_loop` (adaptive 1s/5s polling DLL — refactor 04/mai)
- `_persist_trail_hw_if_moved` (trail HW survives restart)
- `_kill_zombie_agents` (detect-only, não mata)
- `_resolve_active_contract` (alias WDOFUT/WINFUT → contrato vigente)

Detalhe + ids dos bugs (P7/P9/P10) em `historico/sessoes_29abr_01mai.md`.

## Referências externas

- Manual + samples Nelogica: `D:/Projetos/references/ProfitDLL/`
  - `Manual/Manual - ProfitDLL pt_br.pdf`
  - `Exemplo Python/main.py` — referência pra `printOrder` + `stateCallback` + `orderCallback`
  - `Exemplo Delphi/Wrapper/CallbackHandlerU.pas` — same pattern em Delphi
