"""
OCO + Trail loops do profit_agent — extraido em 01/mai/2026.

Funcoes top-level recebem agent (instancia ProfitAgent) via parametro.
State compartilhado vive em agent: _oco_pairs, _oco_groups, _oco_lock,
_db, _stop_event, _last_prices, etc.

Funcoes extraidas:
  load_oco_legacy_pairs_from_db: P10 fix — recarrega pares OCO legacy no boot
  oco_monitor_loop: thread @500ms auto-cancela TP quando SL fill (legacy pairs)
  dispatch_oco_group: dispara TP+SL atrelados quando entry filla
  load_oco_state_from_db: boot helper que repopula _oco_groups
  oco_groups_monitor_loop: thread @500ms reage a fills nos pares
  check_levels_fill: detecta fills + cancela contraparte + resolve grupo
  get_last_price: cache _last_prices com fallback profit_ticks
  trail_compute_new_sl: calcula novo SL apos move favoravel
  persist_trail_hw_if_moved: persiste trail_high_water em mudanca
  trail_check_immediate_trigger: dispara SL imediato se preco atual ja
    cruza SL no atach
  trail_monitor_loop: thread @1s atualiza SL via change_order quando hw move
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import logging
import os
import time

from finanalytics_ai.workers.profit_agent_validators import trail_should_immediate_trigger

log = logging.getLogger("profit_agent.oco")


def load_oco_legacy_pairs_from_db(agent) -> int:
    """P10 fix: reconstrói _oco_pairs in-memory a partir de profit_orders.

    Strategy_id padrão `oco_legacy_pair_<tp_id>_sl` codifica o pareamento.
    Roda no boot — sem isso, pairs perdidos no restart deixavam SL órfão.

    Retorna número de pares carregados.
    """
    if agent._db is None:
        return 0
    try:
        with agent._db._lock:
            cur = agent._db._conn.cursor()
            cur.execute(
                "SELECT local_order_id, ticker, exchange, env, price, stop_price, "
                "strategy_id FROM profit_orders "
                "WHERE strategy_id LIKE 'oco_legacy_pair_%%_sl' "
                "AND order_status IN (0, 1, 10)"
            )
            rows = cur.fetchall()
            cur.close()
    except Exception as exc:
        log.warning("oco_legacy.load_failed err=%s", exc)
        return 0
    if not hasattr(self, "_oco_pairs"):
        agent._oco_pairs = {}
    loaded = 0
    for sl_row in rows:
        sl_id, ticker, exchange, env, price, stop_price, strategy_id = sl_row
        try:
            tp_id = int(strategy_id.split("_")[3])  # oco_legacy_pair_<TP>_sl
        except (ValueError, IndexError):
            continue
        # Verifica se TP ainda está pendente também
        try:
            tp_row = agent._db.fetch_one(
                "SELECT order_status, price FROM profit_orders WHERE local_order_id=%s",
                (tp_id,),
            )
            if not tp_row or tp_row[0] not in (0, 1, 10):
                continue
            tp_price = float(tp_row[1]) if tp_row[1] else 0.0
        except Exception:
            continue
        agent._oco_pairs[tp_id] = {
            "pair_id": sl_id,
            "env": env,
            "type": "tp",
            "ticker": ticker,
            "price": tp_price,
        }
        agent._oco_pairs[sl_id] = {
            "pair_id": tp_id,
            "env": env,
            "type": "sl",
            "ticker": ticker,
            "price": float(stop_price) if stop_price else 0.0,
        }
        loaded += 1
    if loaded:
        log.info("oco_legacy.loaded pairs=%d", loaded)
    return loaded

def oco_monitor_loop(agent) -> None:
    """
    Background thread: monitora pares OCO via EnumerateAllOrders a cada 500ms.
    Quando uma perna executa (status=2), cancela a outra automaticamente.
    """
    log.info("oco_monitor.started")
    while not agent._stop_event.is_set():
        try:
            if hasattr(self, "_oco_pairs") and agent._oco_pairs:
                # Obtém status atual de todas as ordens
                result = agent.get_positions_dll()
                orders_by_id = {o["local_id"]: o for o in result.get("orders", [])}
                to_remove = []
                pairs_snapshot = dict(agent._oco_pairs)
                for local_id, pair_info in pairs_snapshot.items():
                    order = orders_by_id.get(local_id)
                    if not order:
                        continue
                    status = order.get("order_status", -1)
                    if status == 2:  # Filled — cancela a perna oposta
                        pair_id = pair_info["pair_id"]
                        pair_env = pair_info.get("env", "simulation")
                        log.info(
                            "oco.filled local_id=%d type=%s → canceling pair %d",
                            local_id,
                            pair_info.get("type"),
                            pair_id,
                        )
                        agent.cancel_order({"local_order_id": pair_id, "env": pair_env})
                        to_remove.extend([local_id, pair_id])
                    elif status in (4, 8):  # Cancelada ou rejeitada — remove do mapa
                        to_remove.append(local_id)
                for rid in set(to_remove):
                    agent._oco_pairs.pop(rid, None)
                if to_remove:
                    log.info(
                        "oco_monitor.removed ids=%s remaining=%d",
                        to_remove,
                        len(agent._oco_pairs),
                    )
        except Exception as e:
            log.warning("oco_monitor error: %s", e)
        time.sleep(0.5)

# ──────────────────────────────────────────────────────────────────
# OCO multi-level (Phase A 26/abr/2026) — Design_OCO_Trailing_Splits.md
# Suporta: attach a parent pending, N levels, TP/SL individualmente
# opcionais (Decisão 3), parent fill parcial → re-rateio (Decisão 2).
# Trailing (Decisão 1) e UI splits são Phases B/C.
# ──────────────────────────────────────────────────────────────────

def attach_oco(self, params: dict) -> dict:
    """Cria group OCO anexado a uma ordem pending.

    Params:
      env, parent_order_id (int), side ('buy'|'sell' opc — default oposto),
      levels: [{qty, tp_price?, sl_trigger?, sl_limit?, is_trailing?,
                trail_distance?, trail_pct?}],
      is_daytrade, user_account_id, portfolio_id, notes
    """
    if agent._db is None:
        return {"ok": False, "error": "DB nao inicializado"}
    try:
        invalid = _validate_attach_oco_params(params)
        if invalid is not None:
            return invalid
        parent_id = int(params["parent_order_id"])
        levels_in = params["levels"]
        env = params.get("env", "simulation")

        # 1. Valida parent: existe + status pending
        row = agent._db.fetch_one(
            "SELECT ticker, exchange, order_side, quantity, order_status, "
            "broker_id, account_id, sub_account_id, user_account_id, "
            "portfolio_id, env FROM profit_orders WHERE local_order_id = %s",
            (parent_id,),
        )
        if not row:
            return {"ok": False, "error": f"parent {parent_id} nao existe"}
        (
            p_ticker,
            p_exch,
            p_side_int,
            p_qty,
            p_status,
            p_broker,
            p_acct,
            p_sub,
            p_uacct,
            p_pid,
            p_env,
        ) = row
        if p_status not in (0, 10):  # New, PendingNew
            return {"ok": False, "error": f"parent status={p_status} (precisa pending 0/10)"}

        # 2. Valida levels
        side_opt = params.get("side")
        if side_opt:
            side_int = 1 if str(side_opt).lower() == "buy" else 2
        else:
            side_int = 1 if p_side_int == 2 else 2  # oposto do parent

        tot = 0
        for lv in levels_in:
            q = int(lv.get("qty", 0))
            if q <= 0:
                return {"ok": False, "error": "level.qty deve ser > 0"}
            tp = lv.get("tp_price")
            sl = lv.get("sl_trigger")
            if tp is None and sl is None:
                return {"ok": False, "error": "level precisa ao menos 1 TP ou SL"}
            if tp is not None and sl is not None:
                if side_int == 2 and float(tp) <= float(sl):
                    return {"ok": False, "error": "tp deve ser > sl em sell (proteger long)"}
                if side_int == 1 and float(tp) >= float(sl):
                    return {"ok": False, "error": "tp deve ser < sl em buy (proteger short)"}
            tot += q
        if tot != int(p_qty):
            return {"ok": False, "error": f"sum(levels.qty)={tot} != parent.qty={p_qty}"}

        # 3. Insere group + levels (constraint unique impede duplo attach)
        try:
            grp_row = agent._db.fetch_one(
                """INSERT INTO profit_oco_groups
                   (parent_order_id, env, ticker, exchange, side, total_qty, remaining_qty,
                    status, is_daytrade, broker_id, account_id, sub_account_id,
                    user_account_id, portfolio_id, notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'awaiting', %s, %s, %s, %s, %s, %s, %s)
                   RETURNING group_id::text""",
                (
                    parent_id,
                    env,
                    p_ticker,
                    p_exch,
                    side_int,
                    p_qty,
                    p_qty,
                    bool(params.get("is_daytrade", True)),
                    p_broker,
                    p_acct,
                    p_sub,
                    p_uacct,
                    p_pid,
                    params.get("notes"),
                ),
            )
        except Exception as exc:
            msg = str(exc)
            if "ux_oco_groups_one_awaiting_per_parent" in msg or "duplicate key" in msg:
                return {"ok": False, "error": f"parent {parent_id} ja tem OCO ativo"}
            raise
        group_id = grp_row[0]

        level_ids = []
        for idx, lv in enumerate(levels_in, start=1):
            tp = lv.get("tp_price")
            sl = lv.get("sl_trigger")
            slim = lv.get("sl_limit") if lv.get("sl_limit") is not None else sl
            lvl_row = agent._db.fetch_one(
                """INSERT INTO profit_oco_levels
                   (group_id, level_idx, qty, tp_price, sl_trigger, sl_limit,
                    is_trailing, trail_distance, trail_pct)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING level_id::text""",
                (
                    group_id,
                    idx,
                    int(lv["qty"]),
                    float(tp) if tp is not None else None,
                    float(sl) if sl is not None else None,
                    float(slim) if slim is not None else None,
                    bool(lv.get("is_trailing", False)),
                    float(lv["trail_distance"])
                    if lv.get("trail_distance") is not None
                    else None,
                    float(lv["trail_pct"]) if lv.get("trail_pct") is not None else None,
                ),
            )
            level_ids.append(lvl_row[0])

        # 4. State em memória
        if not hasattr(self, "_oco_groups"):
            agent._oco_groups = {}
            agent._order_to_group = {}
        agent._oco_groups[group_id] = {
            "parent_order_id": parent_id,
            "env": env,
            "ticker": p_ticker,
            "exchange": p_exch,
            "side": side_int,
            "total_qty": int(p_qty),
            "remaining_qty": int(p_qty),
            "status": "awaiting",
            "is_daytrade": bool(params.get("is_daytrade", True)),
            "broker_id": p_broker,
            "account_id": p_acct,
            "sub_account_id": p_sub,
            "user_account_id": p_uacct,
            "portfolio_id": p_pid,
            "levels": [
                {
                    "level_id": lid,
                    "idx": i + 1,
                    "qty": int(lv["qty"]),
                    "tp_price": lv.get("tp_price"),
                    "tp_order_id": None,
                    "tp_status": None,
                    "sl_trigger": lv.get("sl_trigger"),
                    "sl_limit": lv.get("sl_limit")
                    if lv.get("sl_limit") is not None
                    else lv.get("sl_trigger"),
                    "sl_order_id": None,
                    "sl_status": None,
                    "is_trailing": bool(lv.get("is_trailing", False)),
                    "trail_distance": lv.get("trail_distance"),
                    "trail_pct": lv.get("trail_pct"),
                }
                for i, (lid, lv) in enumerate(zip(level_ids, levels_in))
            ],
        }
        agent._order_to_group[parent_id] = (group_id, 0, "parent")

        log.info(
            "oco_group.attached group=%s parent=%d ticker=%s qty=%d levels=%d",
            group_id,
            parent_id,
            p_ticker,
            p_qty,
            len(levels_in),
        )
        return {
            "ok": True,
            "group_id": group_id,
            "parent_order_id": parent_id,
            "ticker": p_ticker,
            "total_qty": p_qty,
            "levels": [{"level_id": l, "idx": i + 1} for i, l in enumerate(level_ids)],
        }
    except Exception as exc:
        log.exception("attach_oco error: %s", exc)
        return {"ok": False, "error": str(exc)}

def dispatch_oco_group(agent, group_id: str, filled_qty: int) -> None:
    """Dispara TPs e SLs dos levels após parent fill (total ou parcial).

    Decisão 2 (re-rateio): se filled < total, cada level.qty *= filled/total
    arredondado pra baixo; sobra acumula no último level.
    """
    grp = agent._oco_groups.get(group_id)
    if not grp or grp["status"] not in ("awaiting",):
        return
    try:
        total = grp["total_qty"]
        ratio = filled_qty / total if total > 0 else 0
        if ratio <= 0:
            return

        # Re-rateio
        adjusted_qtys = []
        running = 0
        n = len(grp["levels"])
        for i, lv in enumerate(grp["levels"]):
            if i == n - 1:
                q = filled_qty - running
            else:
                q = int(lv["qty"] * ratio)
                running += q
            adjusted_qtys.append(max(q, 0))

        # Atualiza DB (group + levels qty se mudou)
        agent._db.execute(
            "UPDATE profit_oco_groups SET total_qty = %s, remaining_qty = %s, "
            "status = 'active', updated_at = NOW() WHERE group_id = %s",
            (filled_qty, filled_qty, group_id),
        )
        grp["total_qty"] = filled_qty
        grp["remaining_qty"] = filled_qty
        grp["status"] = "active"

        for lv, q in zip(grp["levels"], adjusted_qtys):
            lv["qty"] = q
            agent._db.execute(
                "UPDATE profit_oco_levels SET qty = %s, updated_at = NOW() WHERE level_id = %s",
                (q, lv["level_id"]),
            )

        # Envia ordens TP/SL pra cada level com qty > 0
        base_params = {
            "env": grp["env"],
            "ticker": grp["ticker"],
            "exchange": grp["exchange"],
            "is_daytrade": grp["is_daytrade"],
            "user_account_id": grp["user_account_id"],
            "portfolio_id": grp["portfolio_id"],
        }
        side_str = "buy" if grp["side"] == 1 else "sell"
        for lv in grp["levels"]:
            if lv["qty"] <= 0:
                continue
            # TP (limit)
            if lv["tp_price"] is not None:
                tp_res = agent._send_order_legacy(
                    {
                        **base_params,
                        "order_type": "limit",
                        "order_side": side_str,
                        "price": float(lv["tp_price"]),
                        "stop_price": -1,
                        "quantity": int(lv["qty"]),
                        "strategy_id": f"oco_grp_{group_id[:8]}_lv{lv['idx']}_tp",
                    }
                )
                if tp_res.get("ok"):
                    lv["tp_order_id"] = tp_res["local_order_id"]
                    lv["tp_status"] = "sent"
                    agent._db.execute(
                        "UPDATE profit_oco_levels SET tp_order_id = %s, tp_status = 'sent', "
                        "updated_at = NOW() WHERE level_id = %s",
                        (tp_res["local_order_id"], lv["level_id"]),
                    )
                    agent._order_to_group[tp_res["local_order_id"]] = (group_id, lv["idx"], "tp")
                else:
                    log.warning(
                        "oco.tp_send_failed level=%s err=%s",
                        lv["level_id"],
                        tp_res.get("error"),
                    )
            # SL (stop-limit)
            if lv["sl_trigger"] is not None:
                sl_res = agent._send_order_legacy(
                    {
                        **base_params,
                        "order_type": "stop",
                        "order_side": side_str,
                        "price": float(lv["sl_limit"]),
                        "stop_price": float(lv["sl_trigger"]),
                        "quantity": int(lv["qty"]),
                        "strategy_id": f"oco_grp_{group_id[:8]}_lv{lv['idx']}_sl",
                    }
                )
                if sl_res.get("ok"):
                    lv["sl_order_id"] = sl_res["local_order_id"]
                    lv["sl_status"] = "sent"
                    agent._db.execute(
                        "UPDATE profit_oco_levels SET sl_order_id = %s, sl_status = 'sent', "
                        "updated_at = NOW() WHERE level_id = %s",
                        (sl_res["local_order_id"], lv["level_id"]),
                    )
                    agent._order_to_group[sl_res["local_order_id"]] = (group_id, lv["idx"], "sl")
                else:
                    log.warning(
                        "oco.sl_send_failed level=%s err=%s",
                        lv["level_id"],
                        sl_res.get("error"),
                    )

        log.info(
            "oco_group.dispatched group=%s filled=%d/%d levels=%d",
            group_id,
            filled_qty,
            total,
            len(grp["levels"]),
        )
    except Exception as exc:
        log.exception("dispatch_oco_group error group=%s: %s", group_id, exc)

def load_oco_state_from_db(agent) -> int:
    """Phase D: recarrega groups awaiting/active/partial do DB para agent._oco_groups
    após restart. Reconstrói também agent._order_to_group (reverse index) a partir
    de tp_order_id e sl_order_id dos levels.

    Retorna número de groups carregados. Idempotente — sobrescreve estado atual.
    """
    if agent._db is None:
        return 0
    try:
        with agent._db._lock:
            cur = agent._db._conn.cursor()
            cur.execute(
                """SELECT group_id::text, parent_order_id, env, ticker, exchange, side,
                          total_qty, remaining_qty, status, is_daytrade, broker_id,
                          account_id, sub_account_id, user_account_id, portfolio_id
                   FROM profit_oco_groups
                   WHERE status IN ('awaiting','active','partial')
                   ORDER BY created_at"""
            )
            grp_rows = cur.fetchall()
            if not grp_rows:
                cur.close()
                return 0
            group_ids = [r[0] for r in grp_rows]
            # Carrega levels de todos groups em 1 query
            cur.execute(
                """SELECT level_id::text, group_id::text, level_idx, qty,
                          tp_price, tp_order_id, tp_status,
                          sl_trigger, sl_limit, sl_order_id, sl_status,
                          is_trailing, trail_distance, trail_pct, trail_high_water
                   FROM profit_oco_levels
                   WHERE group_id::text = ANY(%s)
                   ORDER BY group_id, level_idx""",
                (group_ids,),
            )
            lvl_rows = cur.fetchall()
            cur.close()

        # Agrupa levels por group_id
        levels_by_group: dict[str, list[dict]] = {}
        for lr in lvl_rows:
            (
                lid,
                gid,
                idx,
                qty,
                tp_p,
                tp_oid,
                tp_st,
                sl_t,
                sl_l,
                sl_oid,
                sl_st,
                is_tr,
                t_dist,
                t_pct,
                t_hw,
            ) = lr
            levels_by_group.setdefault(gid, []).append(
                {
                    "level_id": lid,
                    "idx": idx,
                    "qty": int(qty),
                    "tp_price": float(tp_p) if tp_p is not None else None,
                    "tp_order_id": int(tp_oid) if tp_oid is not None else None,
                    "tp_status": tp_st,
                    "sl_trigger": float(sl_t) if sl_t is not None else None,
                    "sl_limit": float(sl_l) if sl_l is not None else None,
                    "sl_order_id": int(sl_oid) if sl_oid is not None else None,
                    "sl_status": sl_st,
                    "is_trailing": bool(is_tr),
                    "trail_distance": float(t_dist) if t_dist is not None else None,
                    "trail_pct": float(t_pct) if t_pct is not None else None,
                    "trail_high_water": float(t_hw) if t_hw is not None else None,
                }
            )

        # Reconstrói agent._oco_groups + agent._order_to_group
        agent._oco_groups = {}
        agent._order_to_group = {}
        for gr in grp_rows:
            (
                gid,
                parent_id,
                env,
                ticker,
                exch,
                side,
                tot,
                rem,
                status,
                isd,
                brk,
                acct,
                sub,
                uacct,
                pid,
            ) = gr
            agent._oco_groups[gid] = {
                "parent_order_id": int(parent_id) if parent_id is not None else None,
                "env": env,
                "ticker": ticker,
                "exchange": exch,
                "side": int(side),
                "total_qty": int(tot),
                "remaining_qty": int(rem),
                "status": status,
                "is_daytrade": bool(isd),
                "broker_id": brk,
                "account_id": acct,
                "sub_account_id": sub,
                "user_account_id": uacct,
                "portfolio_id": pid,
                "levels": levels_by_group.get(gid, []),
            }
            # Reverse index: parent + cada TP/SL ainda 'sent'
            if parent_id is not None and status == "awaiting":
                agent._order_to_group[int(parent_id)] = (gid, 0, "parent")
            for lv in levels_by_group.get(gid, []):
                if lv["tp_order_id"] is not None:
                    agent._order_to_group[lv["tp_order_id"]] = (gid, lv["idx"], "tp")
                if lv["sl_order_id"] is not None:
                    agent._order_to_group[lv["sl_order_id"]] = (gid, lv["idx"], "sl")

        log.info(
            "oco.state_loaded groups=%d levels=%d order_index=%d",
            len(agent._oco_groups),
            len(lvl_rows),
            len(agent._order_to_group),
        )
        return len(agent._oco_groups)
    except Exception as exc:
        log.exception("load_oco_state_from_db error: %s", exc)
        return 0

def oco_groups_monitor_loop(agent) -> None:
    """Background thread: monitora groups OCO multi-level a cada 500ms.

    Responsabilidades:
    - Detecta parent fill (status=2 ou 1 com leaves==0) → _dispatch_oco_group
    - Detecta TP/SL fill em levels → cancela contraparte e fecha level
    - Marca group=completed quando todos levels fechados
    """
    log.info("oco_groups_monitor.started")
    while not agent._stop_event.is_set():
        try:
            if hasattr(self, "_oco_groups") and agent._oco_groups:
                result = agent.get_positions_dll()
                orders_by_id = {o["local_id"]: o for o in result.get("orders", [])}
                groups_snapshot = list(agent._oco_groups.items())
                for group_id, grp in groups_snapshot:
                    # 1. Awaiting → checa parent fill
                    if grp["status"] == "awaiting":
                        parent_order = orders_by_id.get(grp["parent_order_id"])
                        if parent_order:
                            pst = parent_order.get("order_status", -1)
                            traded = int(parent_order.get("traded_qty", 0))
                            if pst == 2 and traded > 0:  # Filled
                                agent._dispatch_oco_group(group_id, traded)
                            elif pst == 1 and traded > 0:  # PartialFilled
                                agent._dispatch_oco_group(group_id, traded)
                            elif pst in (4, 8):  # Cancelled/Rejected → cancela group
                                agent._db.execute(
                                    "UPDATE profit_oco_groups SET status='cancelled', "
                                    "completed_at=NOW(), updated_at=NOW() WHERE group_id=%s",
                                    (group_id,),
                                )
                                grp["status"] = "cancelled"
                                log.info("oco_group.cancelled_with_parent group=%s", group_id)
                    # 2. Active/partial → checa fills de TP/SL
                    elif grp["status"] in ("active", "partial"):
                        agent._check_levels_fill(group_id, grp, orders_by_id)
        except Exception as e:
            log.warning("oco_groups_monitor error: %s", e)
        time.sleep(0.5)

def check_levels_fill(agent, group_id: str, grp: dict, orders_by_id: dict) -> None:
    """Verifica fills de TP/SL em cada level; cancela contraparte; fecha group quando tudo done."""
    any_open = False
    any_filled_now = False
    for lv in grp["levels"]:
        tp_oid = lv.get("tp_order_id")
        sl_oid = lv.get("sl_order_id")
        tp_st = lv.get("tp_status")
        sl_st = lv.get("sl_status")
        level_open = (tp_oid and tp_st in ("sent", "pending")) or (
            sl_oid and sl_st in ("sent", "pending")
        )
        if not level_open:
            continue

        # TP filled? cancela SL
        if tp_oid and tp_st == "sent":
            tpo = orders_by_id.get(tp_oid)
            if tpo:
                s = tpo.get("order_status", -1)
                if s == 2:  # filled
                    lv["tp_status"] = "filled"
                    agent._db.execute(
                        "UPDATE profit_oco_levels SET tp_status='filled', updated_at=NOW() "
                        "WHERE level_id=%s",
                        (lv["level_id"],),
                    )
                    any_filled_now = True
                    if sl_oid and sl_st == "sent":
                        agent.cancel_order({"local_order_id": sl_oid, "env": grp["env"]})
                        lv["sl_status"] = "cancelled"
                        agent._db.execute(
                            "UPDATE profit_oco_levels SET sl_status='cancelled', updated_at=NOW() "
                            "WHERE level_id=%s",
                            (lv["level_id"],),
                        )
                        log.info("oco.tp_filled→sl_cancel group=%s lv=%d", group_id, lv["idx"])
                    continue
                elif s in (4, 8):
                    lv["tp_status"] = "cancelled" if s == 4 else "rejected"

        # SL filled? cancela TP
        if sl_oid and sl_st == "sent":
            slo = orders_by_id.get(sl_oid)
            if slo:
                s = slo.get("order_status", -1)
                if s == 2:
                    lv["sl_status"] = "filled"
                    agent._db.execute(
                        "UPDATE profit_oco_levels SET sl_status='filled', updated_at=NOW() "
                        "WHERE level_id=%s",
                        (lv["level_id"],),
                    )
                    any_filled_now = True
                    if tp_oid and tp_st == "sent":
                        agent.cancel_order({"local_order_id": tp_oid, "env": grp["env"]})
                        lv["tp_status"] = "cancelled"
                        agent._db.execute(
                            "UPDATE profit_oco_levels SET tp_status='cancelled', updated_at=NOW() "
                            "WHERE level_id=%s",
                            (lv["level_id"],),
                        )
                        log.info("oco.sl_filled→tp_cancel group=%s lv=%d", group_id, lv["idx"])
                    continue
                elif s in (4, 8):
                    lv["sl_status"] = "cancelled" if s == 4 else "rejected"

        # Re-avalia se este level ainda tem perna aberta
        still_open = (lv["tp_status"] == "sent") or (lv["sl_status"] == "sent")
        if still_open:
            any_open = True

    # Atualiza status do group
    if not any_open:
        agent._db.execute(
            "UPDATE profit_oco_groups SET status='completed', completed_at=NOW(), "
            "updated_at=NOW() WHERE group_id=%s",
            (group_id,),
        )
        grp["status"] = "completed"
        log.info("oco_group.completed group=%s", group_id)
    elif any_filled_now and grp["status"] == "active":
        agent._db.execute(
            "UPDATE profit_oco_groups SET status='partial', updated_at=NOW() WHERE group_id=%s",
            (group_id,),
        )
        grp["status"] = "partial"

# ──────────────────────────────────────────────────────────────────
# OCO Phase C (26/abr) — Trailing stop
# Decisão 1: aceita trail_distance (R$) XOR trail_pct (%)
# Decisão 6: se mercado já além do trigger ao criar → dispara market imediato
# ──────────────────────────────────────────────────────────────────

def get_last_price(agent, ticker: str) -> float | None:
    """Última cotação com fallback ao DB.

    `_last_prices` é populado no callback de tick; após restart fica vazio
    até primeiro tick de cada ticker chegar. Pra resilience (broker degradado,
    callback inativo, restart recente), fallback consulta `profit_ticks`
    last row do ticker. Cache hit é zero-overhead.
    """
    last = agent._last_prices.get(ticker)
    if last is not None and last > 0:
        return float(last)
    # Tenta alias resolvido tambem (WDOFUT/WDOK26)
    if ticker in FUTURES_ALIASES or ticker[:3] in ("WDO", "WIN", "IND", "DOL", "BIT"):
        resolved = agent._resolve_active_contract(ticker, "F")
        if resolved != ticker:
            last2 = agent._last_prices.get(resolved)
            if last2 is not None and last2 > 0:
                agent._last_prices[ticker] = float(last2)  # cache fwd
                return float(last2)
    # Fallback: DB
    if agent._db is None:
        return None
    try:
        row = agent._db.fetch_one(
            "SELECT price FROM profit_ticks WHERE ticker=%s "
            "AND time > NOW() - INTERVAL '5 min' ORDER BY time DESC LIMIT 1",
            (ticker,),
        )
        if row and row[0]:
            price = float(row[0])
            if price > 0:
                agent._last_prices[ticker] = price  # cache fwd
                return price
    except Exception:
        pass
    return None

def trail_compute_new_sl(agent, side: int, last_price: float, lv: dict) -> float | None:
    """Calcula novo SL baseado em high_water + distance/pct.

    Retorna None se SL não deve mover (high_water não favorável).
    side=2 (sell, proteger long): SL = high_water - distance (max ratchet up)
    side=1 (buy, proteger short): SL = low_water + distance (min ratchet down)
    """
    hw = lv.get("trail_high_water")
    moved = False
    if side == 2:  # proteger long — high_water é o MAX
        if hw is None or last_price > hw:
            hw = last_price
            lv["trail_high_water"] = hw
            moved = True
        if lv.get("trail_distance"):
            agent._persist_trail_hw_if_moved(lv, moved)
            return hw - float(lv["trail_distance"])
        if lv.get("trail_pct"):
            agent._persist_trail_hw_if_moved(lv, moved)
            return hw * (1 - float(lv["trail_pct"]) / 100.0)
    else:  # buy — low_water é o MIN
        if hw is None or last_price < hw:
            hw = last_price
            lv["trail_high_water"] = hw
            moved = True
        if lv.get("trail_distance"):
            agent._persist_trail_hw_if_moved(lv, moved)
            return hw + float(lv["trail_distance"])
        if lv.get("trail_pct"):
            agent._persist_trail_hw_if_moved(lv, moved)
            return hw * (1 + float(lv["trail_pct"]) / 100.0)
    return None

def persist_trail_hw_if_moved(agent, lv: dict, moved: bool) -> None:
    """Persiste `trail_high_water` em DB quando mover. Resilience contra
    restart: sem isso, hw em memória resetava pra NULL após cada restart
    (`_load_oco_state_from_db` carrega do DB), trail nunca acumulava em
    sessões com instabilidade NSSM.
    """
    if not moved or not agent._db or not lv.get("level_id"):
        return
    try:
        agent._db.execute(
            "UPDATE profit_oco_levels SET trail_high_water=%s, updated_at=NOW() "
            "WHERE level_id=%s",
            (lv["trail_high_water"], lv["level_id"]),
        )
    except Exception:
        pass  # Best-effort; reconcile pegará

def _load_pending_orders_from_db(self) -> None:
    from finanalytics_ai.workers.profit_agent_watch import load_pending_orders_from_db

    load_pending_orders_from_db(self)

def _watch_pending_orders_loop(self) -> None:
    from finanalytics_ai.workers.profit_agent_watch import watch_pending_orders_loop

    watch_pending_orders_loop(self)

def _trail_check_immediate_trigger(
    self, group_id: str, grp: dict, lv: dict, last_price: float
) -> bool:
    """Decisão 6: se SL trigger inicial já foi atravessado quando trailing
    é ativado, dispara ordem market imediata.

    Retorna True se disparou, False caso contrário."""
    side = grp["side"]
    trig = lv.get("sl_trigger")
    # Detecção extraída para validator puro (testável sem ctypes).
    if not trail_should_immediate_trigger(side, last_price, trig):
        return False
    log.info(
        "trailing.immediate_trigger group=%s lv=%d last=%.4f trigger=%.4f side=%d",
        group_id,
        lv["idx"],
        last_price,
        float(trig),
        side,
    )
    # Cancela SL pending (se existe) e envia market do lado oposto pra fechar
    if lv.get("sl_order_id") and lv.get("sl_status") == "sent":
        agent.cancel_order({"local_order_id": lv["sl_order_id"], "env": grp["env"]})
        lv["sl_status"] = "cancelled"
    side_str = "buy" if side == 1 else "sell"
    market_res = agent._send_order_legacy(
        {
            "env": grp["env"],
            "ticker": grp["ticker"],
            "exchange": grp["exchange"],
            "is_daytrade": grp["is_daytrade"],
            "user_account_id": grp["user_account_id"],
            "portfolio_id": grp["portfolio_id"],
            "order_type": "market",
            "order_side": side_str,
            "price": -1,
            "stop_price": -1,
            "quantity": int(lv["qty"]),
            "strategy_id": f"oco_grp_{group_id[:8]}_lv{lv['idx']}_trail_imm",
        }
    )
    if market_res.get("ok"):
        lv["sl_order_id"] = market_res["local_order_id"]
        lv["sl_status"] = "sent"  # market deve fillar rápido
        agent._db.execute(
            "UPDATE profit_oco_levels SET sl_order_id=%s, sl_status='sent', "
            "trail_high_water=%s, updated_at=NOW() WHERE level_id=%s",
            (market_res["local_order_id"], last_price, lv["level_id"]),
        )
        agent._order_to_group[market_res["local_order_id"]] = (group_id, lv["idx"], "sl")
    return True

def trail_monitor_loop(agent) -> None:
    """Background thread: pra cada level com is_trailing=true e SL aberto,
    atualiza SL via change_order quando high_water move favoravelmente.

    Roda a cada 1s — balance entre responsividade e overhead.
    """
    log.info("trail_monitor.started")
    if not hasattr(self, "_trail_last_log_ts"):
        agent._trail_last_log_ts: dict[str, float] = {}
    while not agent._stop_event.is_set():
        try:
            if not hasattr(self, "_oco_groups") or not agent._oco_groups:
                time.sleep(1.0)
                continue
            groups_snap = list(agent._oco_groups.items())
            for group_id, grp in groups_snap:
                if grp["status"] not in ("active", "partial", "awaiting"):
                    continue
                last = agent._get_last_price(grp["ticker"])
                if last is None or last <= 0:
                    # Log raro para detectar problema de feed
                    last_log = agent._trail_last_log_ts.get(f"{group_id}:noprice", 0)
                    if time.time() - last_log > 30:
                        log.warning(
                            "trail.no_price group=%s ticker=%s — cache+DB sem cotacao",
                            group_id,
                            grp["ticker"],
                        )
                        agent._trail_last_log_ts[f"{group_id}:noprice"] = time.time()
                    continue
                # Heartbeat log periódico para observabilidade do trail
                last_log = agent._trail_last_log_ts.get(group_id, 0)
                if time.time() - last_log > 15:
                    for _lv in grp["levels"]:
                        if _lv.get("is_trailing"):
                            log.info(
                                "trail.tick group=%s lv=%d ticker=%s last=%.4f hw=%s sl=%s",
                                group_id,
                                _lv.get("idx", 0),
                                grp["ticker"],
                                last,
                                _lv.get("trail_high_water"),
                                _lv.get("sl_trigger"),
                            )
                    agent._trail_last_log_ts[group_id] = time.time()
                for lv in grp["levels"]:
                    if not lv.get("is_trailing"):
                        continue
                    # Awaiting → checa imediato (parent ainda nao fillou —
                    # ou seja, apenas armazena high_water em runtime)
                    if grp["status"] == "awaiting":
                        agent._trail_compute_new_sl(grp["side"], last, lv)
                        continue
                    # Active/partial — só ajusta SE SL ativa nesse level
                    if not lv.get("sl_order_id") or lv.get("sl_status") != "sent":
                        continue

                    # Decisão 6: se SL trigger atual já foi cruzado → market imediato
                    if agent._trail_check_immediate_trigger(group_id, grp, lv, last):
                        continue

                    # Calcula novo SL trigger
                    new_sl = agent._trail_compute_new_sl(grp["side"], last, lv)
                    if new_sl is None:
                        continue
                    cur_trig = lv.get("sl_trigger")
                    if cur_trig is None:
                        continue
                    # Ratchet: só move SE favorecer (sell long: subir SL; buy short: descer SL)
                    moved = (grp["side"] == 2 and new_sl > float(cur_trig) + 0.01) or (
                        grp["side"] == 1 and new_sl < float(cur_trig) - 0.01
                    )
                    if not moved:
                        continue
                    # Trail R$: arredonda 2 decimais. Limit = trigger (stop-market emulado).
                    new_sl = round(new_sl, 2)
                    new_lim = round(new_sl, 2)
                    ret = agent.change_order(
                        {
                            "env": grp["env"],
                            "local_order_id": lv["sl_order_id"],
                            "price": new_lim,
                            "stop_price": new_sl,
                            "quantity": int(lv["qty"]),
                        }
                    )
                    moved_ok = bool(ret.get("ok"))

                    # P7 fallback (28/abr): broker simulator rejeita change_order
                    # em ordens stop-limit (ret=-2147483645). Quando change falha,
                    # cancel+create novo SL. Mantém trailing funcional mesmo com
                    # broker degradado.
                    if not moved_ok:
                        # Cooldown 30s para evitar loop infinito + log spam quando
                        # cancel também falha (broker order not found, etc).
                        cd_ts = lv.get("_trail_fallback_cooldown_until", 0)
                        if time.time() < cd_ts:
                            continue
                        agent._trail_fallback_count = (
                            getattr(self, "_trail_fallback_count", 0) + 1
                        )
                        log.warning(
                            "trailing.change_failed_fallback_to_cancel_create "
                            "group=%s lv=%d new_sl=%.4f ret=%s",
                            group_id,
                            lv["idx"],
                            new_sl,
                            ret.get("ret"),
                        )
                        cancel_ret = agent.cancel_order(
                            {
                                "env": grp["env"],
                                "local_order_id": lv["sl_order_id"],
                            }
                        )
                        if not cancel_ret.get("ok"):
                            log.warning(
                                "trailing.cancel_failed group=%s lv=%d sl_id=%d "
                                "ret=%s — cooldown 30s",
                                group_id,
                                lv["idx"],
                                lv["sl_order_id"],
                                cancel_ret.get("ret"),
                            )
                            lv["_trail_fallback_cooldown_until"] = time.time() + 30
                        if cancel_ret.get("ok"):
                            # Cria novo SL stop-limit
                            side_str = "sell" if grp["side"] == 2 else "buy"
                            new_sl_res = agent._send_order_legacy(
                                {
                                    "env": grp["env"],
                                    "ticker": grp["ticker"],
                                    "exchange": grp["exchange"],
                                    "is_daytrade": grp["is_daytrade"],
                                    "user_account_id": grp.get("user_account_id"),
                                    "portfolio_id": grp.get("portfolio_id"),
                                    "order_type": "stop",
                                    "order_side": side_str,
                                    "price": new_lim,
                                    "stop_price": new_sl,
                                    "quantity": int(lv["qty"]),
                                    "strategy_id": (
                                        f"oco_grp_{group_id[:8]}_lv{lv['idx']}_sl_trail"
                                    ),
                                }
                            )
                            if new_sl_res.get("ok"):
                                old_sl_id = lv["sl_order_id"]
                                lv["sl_order_id"] = new_sl_res["local_order_id"]
                                agent._order_to_group[lv["sl_order_id"]] = (
                                    group_id,
                                    lv["idx"],
                                    "sl",
                                )
                                agent._order_to_group.pop(old_sl_id, None)
                                agent._db.execute(
                                    "UPDATE profit_oco_levels SET sl_order_id=%s, "
                                    "updated_at=NOW() WHERE level_id=%s",
                                    (lv["sl_order_id"], lv["level_id"]),
                                )
                                moved_ok = True
                                log.info(
                                    "trailing.cancel_create group=%s lv=%d "
                                    "old_sl=%d new_sl_id=%d new_sl=%.4f",
                                    group_id,
                                    lv["idx"],
                                    old_sl_id,
                                    lv["sl_order_id"],
                                    new_sl,
                                )
                            else:
                                log.warning(
                                    "trailing.create_failed group=%s lv=%d err=%s "
                                    "— cooldown 30s",
                                    group_id,
                                    lv["idx"],
                                    new_sl_res.get("error"),
                                )
                                lv["_trail_fallback_cooldown_until"] = time.time() + 30

                    if moved_ok:
                        lv["sl_trigger"] = new_sl
                        lv["sl_limit"] = new_lim
                        agent._db.execute(
                            "UPDATE profit_oco_levels SET sl_trigger=%s, sl_limit=%s, "
                            "trail_high_water=%s, updated_at=NOW() WHERE level_id=%s",
                            (new_sl, new_lim, lv["trail_high_water"], lv["level_id"]),
                        )
                        agent._trail_adjust_count = getattr(self, "_trail_adjust_count", 0) + 1
                        log.info(
                            "trailing.adjusted group=%s lv=%d hw=%.4f new_sl=%.4f",
                            group_id,
                            lv["idx"],
                            lv["trail_high_water"],
                            new_sl,
                        )
        except Exception as e:
            log.warning("trail_monitor error: %s", e)
        time.sleep(1.0)

