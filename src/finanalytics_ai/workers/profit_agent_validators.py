"""Validators e decisões puras para profit_agent — sem ctypes.

Extraído de profit_agent.py (sessão 30/abr/2026) para permitir unit test
em CI Linux. profit_agent.py importa ctypes.WINFUNCTYPE no top-level
(Windows-only), o que impedia testar helpers puros direto.
"""

from __future__ import annotations


def trail_should_immediate_trigger(
    side: int, last_price: float | None, sl_trigger: float | None
) -> bool:
    """Decisão 6 (B.10): retorna True se SL trigger já foi atravessado quando
    trailing é ativado, indicando que safety-net deve disparar market imediato.

    side: 1=buy short (SL acima), 2=sell long (SL abaixo)

    sell long: trigger é piso — last_price <= trigger = já passou (executar)
    buy short: trigger é teto — last_price >= trigger = já passou (executar)

    Em broker simulator esse caminho raramente exercita (broker auto-fillEXEC
    stop-limit já trigado como market). Em broker que rejeita stop-limit
    com trigger atravessado, o monitor toma o lugar.
    """
    if sl_trigger is None or last_price is None:
        return False
    return (side == 2 and last_price <= float(sl_trigger)) or (
        side == 1 and last_price >= float(sl_trigger)
    )


def validate_attach_oco_params(params: dict) -> dict | None:
    """Valida estrutura do request body de attach_oco.

    Retorna None se válido; dict {ok:False, error:...} se inválido.

    Rejeita is_trailing/trail_distance/trail_pct no top-level — esses
    campos são per-level (cada nível pode ter trail próprio em estratégias
    multi-nível). Cliente que passa no top-level é silenciosamente ignorado
    pelo loop adiante (lv.get) e o DB grava is_trailing=False — rejeitar
    explícito evita "tiro no escuro" do request mal montado.
    """
    parent_id = int(params.get("parent_order_id", 0))
    if parent_id <= 0:
        return {"ok": False, "error": "parent_order_id obrigatorio"}
    levels_in = params.get("levels") or []
    if not levels_in:
        return {"ok": False, "error": "levels[] vazio"}
    _trail_keys = ("is_trailing", "trail_distance", "trail_pct")
    _top_trail = [k for k in _trail_keys if k in params]
    if _top_trail:
        return {
            "ok": False,
            "error": (
                f"campos trail no top-level ({_top_trail}) — devem estar "
                "dentro de cada level: levels=[{qty,tp_price,sl_trigger,"
                "sl_limit,is_trailing,trail_distance,trail_pct}]"
            ),
        }
    return None


def compute_trading_result_match(
    local_id: int | None, cl_ord: str | None, message_id: int | None
) -> tuple[str, tuple] | None:
    """P2-futuros fix (sessão 01/mai): decide se um trading_result do callback
    pode ser matched contra `profit_orders` e constrói o WHERE clause.

    Antes (P2 30/abr): WHERE local_order_id = X OR cl_ord_id = Y. Quando
    broker rejeita instantâneo (ex: code=5 "Ordem inválida" em futuros) com
    `r.OrderID.LocalOrderID=0` E `_msg_id_to_local` sem mapping (post-restart),
    o UPDATE fica zero rows. status fica stuck em 10 (PendingNew) até o
    reconcile_loop pegar — mas reconcile só roda 10h-18h.

    Fix: incluir `message_id` no fallback. `profit_orders.message_id` já é
    persistido em `insert_order` (linha 889). Match adicional cobre o caso
    em que `local_order_id` e `cl_ord_id` chegam zerados.

    Retorna:
      None — skip (todos identifiers vazios; UPDATE sem WHERE seria desastre).
      (where_sql, params) — onde where_sql é uma string com placeholders %s
        e params é a tupla na ordem.

    Convenção: identifiers tratados como "vazios":
      local_id: None ou <= 0
      cl_ord:   None, "", whitespace
      message_id: None ou <= 0

    Ao menos um deve estar populado pra retornar match.
    """
    has_local = local_id is not None and local_id > 0
    has_cl = bool(cl_ord and cl_ord.strip())
    has_msg = message_id is not None and message_id > 0
    if not (has_local or has_cl or has_msg):
        return None

    parts: list[str] = []
    params: list[int | str] = []
    if has_local:
        parts.append("local_order_id = %s")
        params.append(local_id)
    if has_cl:
        parts.append("cl_ord_id = %s")
        params.append(cl_ord)
    if has_msg:
        parts.append("message_id = %s")
        params.append(message_id)

    return (" OR ".join(parts), tuple(params))


# ── P1 (28/abr, expandido 04/mai): retry de rejeicao broker-blip ──────────────

# Codes rejection-like do TConnectorTradingMessageResultCode (manual Nelogica).
# Usados em conjunto com pattern matching de msg para distinguir blip do broker
# (transient, retry vale a pena) de rejeicao por regra de negocio (qty invalida,
# saldo insuficiente, fora de horario — retry NAO ajuda).
_RETRY_REJECTION_CODES: tuple[int, ...] = (1, 3, 5, 7, 9, 24)
# Substrings minusculas que indicam blip do broker / subconnection.
# Smoke 04/mai validou em log Delphi: "Cliente nao esta logado" e variantes.
_RETRY_BLIP_PATTERNS: tuple[str, ...] = (
    "cliente n",
    "logado",
    "nao conectado",
    "não conectado",
    "timeout",
    "subconex",
)


def resolve_subscribe_list(
    db_tickers: list[tuple[str, str]],
    env_tickers: list[str],
    db_connected: bool,
    default_exchange: str = "B",
) -> list[tuple[str, str]]:
    """Resolve a lista final de (ticker, exchange) para subscribe no boot.

    Smoke 04/mai (bug raiz P0 #4): logica original era `if db_connected:
    use DB else use env`. Quando DB estava reachable mas vazio (apos
    restart, profit_history_tickers nao seedada), agent terminava com 0
    subscriptions — falha silenciosa que so era notada via /status mostrar
    `subscribed_tickers: []` apesar de market_connected=true.

    Nova semantica (fix): SEMPRE union de DB + env. Env serve como seed
    (defaults sempre presentes); DB adiciona extras (ex: manualmente
    subscritos via POST /subscribe). Dedup por (ticker, exchange) tupla.

    Args:
      db_tickers: lista de (ticker, exchange) do DB (vazia se nao conectado).
      env_tickers: lista de ticker strings do .env (default exchange "B").
      db_connected: se False, db_tickers e' ignorada (caller geralmente
        passa lista vazia ja).
      default_exchange: exchange para tickers do env (default "B" stocks B3).

    Returns:
      Lista de (ticker_upper, exchange_upper) deduplicada, ordem preservada
      (env primeiro, DB depois p/ extras).
    """
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    # Env primeiro — defaults sempre presentes
    for raw in env_tickers:
        if not raw:
            continue
        t = raw.strip().upper()
        if not t:
            continue
        key = (t, default_exchange.upper())
        if key not in seen:
            seen.add(key)
            result.append(key)
    # DB additions (so se conectado)
    if db_connected:
        for ticker, exchange in db_tickers:
            t = (ticker or "").strip().upper()
            if not t:
                continue
            e = (exchange or default_exchange).strip().upper() or default_exchange.upper()
            key = (t, e)
            if key not in seen:
                seen.add(key)
                result.append(key)
    return result


def parse_order_details(order: object) -> dict:
    """Converte um TConnectorOrderOut populado em dict para uso interno.

    Funcao pura — nao acessa DLL. `order` e' esperado ter todos os campos
    preenchidos (depois do 2-pass GetOrderDetails). Usa duck typing para
    funcionar tanto com struct ctypes real quanto com mocks de teste
    (qualquer objeto com `.OrderID`, `.AssetID`, etc.).

    Extraido de profit_agent._get_order_details (04/mai) — antes a logica
    de mapping ficava acoplada com a chamada DLL, dificultando teste sem
    Windows. Agora _get_order_details so faz o 2-pass call e delega aqui.

    Strings vem do DLL como `c_wchar_p` apontando pra buffers pre-alocados;
    `.strip()` remove padding (`' ' * length`). None handling defensivo
    em todos os campos string (DLL pode retornar NULL).

    Args:
      order: struct TConnectorOrderOut populado (ou mock equivalente).

    Returns:
      Dict com keys: local_order_id, cl_ord_id, ticker, exchange,
      quantity, traded_qty, leaves_qty, price, stop_price, avg_price,
      order_side, order_type, order_status, validity_type, text_message.
    """
    return {
        "local_order_id": order.OrderID.LocalOrderID,
        "cl_ord_id": (order.OrderID.ClOrderID or "").strip(),
        "ticker": (order.AssetID.Ticker or "").strip(),
        "exchange": (order.AssetID.Exchange or "").strip(),
        "quantity": order.Quantity,
        "traded_qty": order.TradedQuantity,
        "leaves_qty": order.LeavesQuantity,
        "price": order.Price,
        "stop_price": order.StopPrice,
        "avg_price": order.AveragePrice,
        "order_side": order.OrderSide,
        "order_type": order.OrderType,
        "order_status": order.OrderStatus,
        "validity_type": order.ValidityType,
        "text_message": (order.TextMessage or "").strip(),
    }


def message_has_blip_pattern(message: str | None) -> bool:
    """True se `message` contem ao menos uma substring de blip pattern.

    Variante so-msg (sem code check) usada em `order_cb` quando o status
    final ja foi extraido via `GetOrderDetails` — order_cb sabe que status=8
    via DLL, so precisa decidir se vale retry baseado na msg.

    Em `trading_msg_cb`, usar `should_retry_rejection(code, msg)` que tambem
    filtra por code rejection-like.
    """
    if not message:
        return False
    msg_lower = message.lower()
    return any(p in msg_lower for p in _RETRY_BLIP_PATTERNS)


def should_retry_rejection(code: int, message: str | None) -> bool:
    """Decide se uma rejeicao do trading_msg_cb deve disparar retry P1.

    True quando:
      - `code` esta em `_RETRY_REJECTION_CODES` (1,3,5,7,9,24 = NotConnected,
        RejectedMercury, RejectedHades, RejectedBroker, RejectedMarket,
        BlockedByRisk).
      - `message` (case-insensitive) contem ao menos uma substring de
        `_RETRY_BLIP_PATTERNS`.

    False caso contrario (codes nao rejection-like ou message vazia/sem blip).

    Smoke 04/mai descobriu o expand: pattern original era hardcoded code==3
    + 'logado'. Broker SIM 32003 mostrou variantes (RejectedMercuryLegacy
    code=3 mas tambem combinacoes via OrderCallback).

    Args:
      code: ResultCode do callback (TConnectorTradingMessageResultCode).
      message: r.Message strip; pode ser None ou vazio.

    Returns:
      True se vale retry; False caso contrario.
    """
    if code not in _RETRY_REJECTION_CODES:
        return False
    if not message:
        return False
    msg_lower = message.lower()
    return any(p in msg_lower for p in _RETRY_BLIP_PATTERNS)
