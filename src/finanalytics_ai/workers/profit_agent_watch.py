"""
Watch pending orders — extraido em 01/mai/2026.

Mitigacao P9: detecta status final de ordens pendentes via DLL polling
(broker degradado/callback falho → DB stuck status=10 eternamente).

Funcoes recebem agent (instancia ProfitAgent) via parametro — acessa
agent._db (DBWriter), agent._pending_orders (dict), agent._pending_lock
(Lock), agent._stop_event (Event), agent.get_positions_dll() (method).

load_pending_orders_from_db: boot helper que popula _pending_orders
a partir do DB (fix sessao 30/abr — cobre restart NSSM/container).

watch_pending_orders_loop: thread daemon @5s detecta orphan no_dll_record
e timeout 5min. Marca status=8 quando confirma orfã.
"""

from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger("profit_agent.watch")


def _f(env: str, default: float) -> float:
    """Le env var como float com fallback."""
    try:
        v = os.environ.get(env)
        return float(v) if v else default
    except (TypeError, ValueError):
        return default


# Tunables — refactor 04/mai (broker_blip): ciclo mais rapido para
# orders fresh, retry mais agressivo. Defaults projetados para flapping
# tipico (1-2s por crDisconnected→crBrokerConnected ciclo).
_WATCH_FAST_POLL_SEC = _f("PROFIT_WATCH_FAST_POLL_SEC", 1.0)
_WATCH_SLOW_POLL_SEC = _f("PROFIT_WATCH_SLOW_POLL_SEC", 5.0)
_WATCH_FRESH_AGE_SEC = _f("PROFIT_WATCH_FRESH_AGE_SEC", 30.0)
_FALLBACK_RETRY_DELAY_SEC = _f("PROFIT_FALLBACK_RETRY_DELAY_SEC", 1.5)


def load_pending_orders_from_db(agent) -> None:
    """Boot helper (sessão 30/abr): popula `_pending_orders` com ordens
    em status pendente nas últimas N horas para que `_watch_pending_orders_loop`
    cubra restarts do agent.

    Antes desse fix, restart limpava o dict in-memory e órfãs ficavam fora
    do watch até cleanup_stale_pending_orders_job rodar (1×/dia 23h BRT) —
    gap visível no flatten WDOFUT 30/abr (9 órfãs de 28-29/abr ainda
    retornando ret=-2147483636).

    Janela default 24h (env `PROFIT_WATCH_LOAD_HOURS`). Mais tempo amplia
    cobertura mas aumenta carga em DLL polling no loop.
    """
    if not agent._db:
        return
    try:
        hours = int(os.environ.get("PROFIT_WATCH_LOAD_HOURS", "24"))
        rows = agent._db.fetch_all(
            """SELECT local_order_id, ticker, env, created_at
                 FROM profit_orders
                WHERE order_status IN (0, 1, 10)
                  AND created_at >= NOW() - (%s::int * INTERVAL '1 hour')
                ORDER BY created_at DESC
                LIMIT 500""",
            (hours,),
        )
        now = time.time()
        with agent._pending_lock:
            for row in rows or []:
                local_id, ticker, env, created_at = row[0], row[1], row[2], row[3]
                # ts_sent: usa idade real da ordem (created_at) para que
                # o loop logo classifique como orphan/timeout se DLL
                # já não enumera (vs assumir age=0 e ficar 60s no limbo).
                age_s = max(0.0, (now - created_at.timestamp()) if created_at else 0.0)
                agent._pending_orders[int(local_id)] = {
                    "ts_sent": now - age_s,
                    "ticker": ticker or "",
                    "env": env or "simulation",
                }
        log.info("watch_pending_orders.loaded n=%d hours=%d", len(rows or []), hours)
    except Exception as exc:
        log.warning("watch_pending_orders.load_failed error=%s", exc)


def watch_pending_orders_loop(agent) -> None:
    """Mitigação P9: detecta status final de ordens pendentes via DLL polling.

    Cenário: broker degradado/callback falho → DB stuck status=10 (PendingNew)
    eternamente. Reconcile loop normal só corrige se DLL ainda enumera, mas
    ordens já encerradas saem do `EnumerateAllOrders` rápido.

    Fluxo:
      1. `_send_order_legacy` registra `local_id` em `_pending_orders`.
      2. Este loop varre a cada 5s, chama `EnumerateAllOrders` (já atualiza DB
         via `get_positions_dll` para ordens enumeradas).
      3. Para `local_id` registrado:
         - Se DLL enumera → status updated pelo reconcile, remove do registry
           se não-pendente.
         - Se DLL NÃO enumera + DB ainda em status pendente após 60s →
           marca como `status=8` (Rejected) com `error_message='watch_orphan_no_dll_record'`.
         - Após 5min, remove do registry mesmo se ainda pending (não vai resolver).
    """
    log.info(
        "watch_pending_orders.started fast=%.1fs slow=%.1fs fresh_age=%.1fs",
        _WATCH_FAST_POLL_SEC,
        _WATCH_SLOW_POLL_SEC,
        _WATCH_FRESH_AGE_SEC,
    )
    while not agent._stop_event.is_set():
        try:
            with agent._pending_lock:
                if not agent._pending_orders:
                    time.sleep(_WATCH_SLOW_POLL_SEC)
                    continue
                snap = dict(agent._pending_orders)

            # Enumera + reconcile DB (já atualiza ordens enumeradas)
            env = next(iter(snap.values()))["env"]
            res = agent.get_positions_dll(env=env)
            dll_orders = res.get("orders", []) if isinstance(res, dict) else []
            seen_local = {o.get("local_id") for o in dll_orders if o.get("local_id")}

            now = time.time()
            to_drop: list[int] = []
            for local_id, info in snap.items():
                age = now - info["ts_sent"]
                # Read DB status atual (após reconcile run)
                if not agent._db:
                    continue
                row = agent._db.fetch_one(
                    "SELECT order_status FROM profit_orders WHERE local_order_id=%s",
                    (local_id,),
                )
                if not row:
                    if age > 60:
                        to_drop.append(local_id)
                    continue
                cur_status = int(row[0])
                if cur_status not in (0, 1, 10):
                    # Final state — remove
                    log.info(
                        "watch.order_resolved local_id=%d status=%d age=%.1fs",
                        local_id,
                        cur_status,
                        age,
                    )
                    # P1 fallback (04/mai): se status=8 (Rejected) detectado via
                    # polling em < 30s e _retry_params nao iniciou retry, schedule
                    # um. Cobre o caso onde trading_msg_cb nao recebeu callback de
                    # rejeicao (ex.: broker subconnection blip drop callback).
                    # Idempotente: _retry_rejected_order trata retry_started=True
                    # como no-op. Max attempts=3 ja garantido la.
                    if cur_status == 8 and age < _WATCH_FRESH_AGE_SEC:
                        retry_entry = None
                        already_started = False
                        if hasattr(agent, "_retry_lock") and hasattr(
                            agent, "_retry_params"
                        ):
                            with agent._retry_lock:
                                retry_entry = agent._retry_params.get(local_id)
                                already_started = bool(
                                    retry_entry and retry_entry.get("retry_started")
                                )
                        if retry_entry and not already_started:
                            log.info(
                                "watch.fallback_retry_scheduled local_id=%d age=%.1fs delay=%.1fs reason=silent_status8",
                                local_id,
                                age,
                                _FALLBACK_RETRY_DELAY_SEC,
                            )
                            t = threading.Timer(
                                _FALLBACK_RETRY_DELAY_SEC,
                                agent._retry_rejected_order,
                                args=(local_id,),
                            )
                            t.daemon = True
                            t.start()
                    to_drop.append(local_id)
                    continue
                # Stuck pending no DB
                if local_id not in seen_local and age > 60:
                    # DLL não enumera mais + DB ainda pending = orphan
                    try:
                        agent._db.execute(
                            "UPDATE profit_orders SET order_status=8, "
                            "error_message='watch_orphan_no_dll_record', "
                            "updated_at=NOW() "
                            "WHERE local_order_id=%s AND order_status IN (0,1,10)",
                            (local_id,),
                        )
                        log.warning(
                            "watch.order_orphaned local_id=%d age=%.1fs ticker=%s marked status=8",
                            local_id,
                            age,
                            info["ticker"],
                        )
                    except Exception as exc:
                        log.warning("watch.orphan_update_failed local=%d e=%s", local_id, exc)
                    to_drop.append(local_id)
                elif age > 300:
                    # 5min sem resolução — desiste
                    log.info(
                        "watch.order_timeout local_id=%d age=%.1fs DB status=%d remove",
                        local_id,
                        age,
                        cur_status,
                    )
                    to_drop.append(local_id)

            if to_drop:
                with agent._pending_lock:
                    for lid in to_drop:
                        agent._pending_orders.pop(lid, None)

            # Sleep adaptativo: fast poll quando ha order fresh (< fresh_age)
            # ainda pendente, slow poll caso contrario. Permite detectar
            # rejection silencioso de status=8 dentro de 1s ao inves de 5s.
            with agent._pending_lock:
                has_fresh = any(
                    (now - i["ts_sent"]) < _WATCH_FRESH_AGE_SEC
                    for i in agent._pending_orders.values()
                )
            time.sleep(_WATCH_FAST_POLL_SEC if has_fresh else _WATCH_SLOW_POLL_SEC)
        except Exception as exc:
            log.warning("watch_pending_orders error: %s", exc)
            time.sleep(_WATCH_SLOW_POLL_SEC * 2)
