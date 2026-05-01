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
