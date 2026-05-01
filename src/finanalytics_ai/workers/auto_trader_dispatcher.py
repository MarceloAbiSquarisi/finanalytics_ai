"""
Order dispatcher do auto_trader_worker (R1 Phase 2).

Responsabilidades:
  1. Persistir robot_orders_intent ANTES do envio (audit even on crash).
  2. Compor cl_ord_id deterministico para idempotencia no proxy.
  3. POST /api/v1/agent/order/send (proxy FastAPI -> profit_agent :8002).
  4. Anexar OCO via /api/v1/agent/order/oco quando TP+SL fornecidos.
  5. UPDATE robot_orders_intent + robot_signals_log com local_order_id e
     sent_at apos resposta DLL.
  6. Best-effort: erro de envio NAO derruba o worker; loga + persiste error_msg.

Convencao de cl_ord_id (deterministico):
  robot:<strategy_id>:<ticker>:<action>:<computed_at_minute_iso>
  ex: robot:1:PETR4:BUY:2026-05-01T12:35

  Mesmo signal disparando 2x dentro do mesmo minuto e idempotente — proxy
  usa cl_ord_id como chave logica e o profit_agent persiste em
  profit_orders.cl_ord_id (Alembic ts_0003).
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


# Base URL do proxy FastAPI (NAO o profit_agent direto). Use a API porque ela
# resolve `_account_*` injection automatico via AccountService.
API_BASE_URL = os.environ.get("AUTO_TRADER_API_URL", "http://api:8000")
ORDER_TIMEOUT_SEC = float(os.environ.get("AUTO_TRADER_ORDER_TIMEOUT", "10.0"))


def make_cl_ord_id(
    *, strategy_id: int, ticker: str, action: str, computed_at: datetime | None = None
) -> str:
    """
    Identificador deterministico — mesmo (strategy, ticker, action, minuto)
    produz o mesmo cl_ord_id. Truncado em 64 chars (limite tipico de DB).
    """
    ts = computed_at or datetime.now(UTC)
    minute = ts.replace(second=0, microsecond=0).isoformat()
    raw = f"robot:{strategy_id}:{ticker}:{action}:{minute}"
    if len(raw) <= 64:
        return raw
    # Fallback para hash se muito longo (defensivo)
    return "robot:" + hashlib.sha256(raw.encode()).hexdigest()[:48]


# ── DB helpers (mesma DSN que worker) ─────────────────────────────────────────


def _get_conn(dsn: str):
    import psycopg2

    return psycopg2.connect(dsn)


def insert_intent(
    *,
    dsn: str,
    signal_log_id: int,
    strategy_id: int,
    ticker: str,
    side: str,
    order_type: str,
    quantity: float,
    price: float | None,
    take_profit: float | None,
    stop_loss: float | None,
    cl_ord_id: str,
) -> int | None:
    """INSERT em robot_orders_intent. Retorna id ou None em erro."""
    try:
        with _get_conn(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO robot_orders_intent
                (signal_log_id, strategy_id, ticker, side, order_type,
                 quantity, price, take_profit, stop_loss, cl_ord_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    signal_log_id,
                    strategy_id,
                    ticker,
                    side,
                    order_type,
                    quantity,
                    price,
                    take_profit,
                    stop_loss,
                    cl_ord_id,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return row[0] if row else None
    except Exception as exc:
        logger.error("dispatcher.insert_intent_failed", error=str(exc))
        return None


def update_intent_sent(
    *,
    dsn: str,
    intent_id: int,
    local_order_id: int | None,
    error_msg: str | None,
) -> None:
    """UPDATE robot_orders_intent.sent_at + local_order_id ou error_msg."""
    try:
        with _get_conn(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE robot_orders_intent
                SET sent_at = NOW(), local_order_id = %s, error_msg = %s
                WHERE id = %s
                """,
                (local_order_id, error_msg, intent_id),
            )
            conn.commit()
    except Exception as exc:
        logger.error("dispatcher.update_intent_failed", error=str(exc))


def update_signal_log_sent(
    *, dsn: str, signal_log_id: int, local_order_id: int | None, sent: bool
) -> None:
    """UPDATE robot_signals_log.sent_to_dll + local_order_id."""
    try:
        with _get_conn(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE robot_signals_log
                SET sent_to_dll = %s, local_order_id = %s
                WHERE id = %s
                """,
                (sent, local_order_id, signal_log_id),
            )
            conn.commit()
    except Exception as exc:
        logger.error("dispatcher.update_signal_failed", error=str(exc))


# ── Dispatcher async ─────────────────────────────────────────────────────────


async def post_order(
    *,
    base_url: str,
    side: str,
    ticker: str,
    quantity: float,
    price: float | None,
    order_type: str,
    cl_ord_id: str,
    is_daytrade: bool = True,
    env: str = "simulation",
    timeout_sec: float = ORDER_TIMEOUT_SEC,
) -> dict[str, Any]:
    """
    POST /api/v1/agent/order/send com cl_ord_id + _source='auto_trader'.

    Retorna dict com a resposta do agent. Erro (HTTP != 200 ou exception)
    levanta httpx.HTTPError — caller trata.
    """
    body = {
        "env": env,
        "ticker": ticker,
        "exchange": "B",
        "order_side": side.lower(),
        "order_type": order_type,
        "quantity": quantity,
        "price": price if price is not None else -1,
        "is_daytrade": is_daytrade,
        # Handshake C5: source flag suprime hook de diary, cl_ord_id preserva
        # idempotencia. profit_agent persiste ambos em profit_orders.
        "_source": "auto_trader",
        "_client_order_id": cl_ord_id,
    }
    url = f"{base_url.rstrip('/')}/api/v1/agent/order/send"
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        r = await client.post(url, json=body)
        r.raise_for_status()
        return r.json()


async def post_oco(
    *,
    base_url: str,
    ticker: str,
    quantity: float,
    take_profit: float,
    stop_loss: float,
    side: str = "sell",
    is_daytrade: bool = True,
    env: str = "simulation",
    timeout_sec: float = ORDER_TIMEOUT_SEC,
) -> dict[str, Any]:
    """
    POST /api/v1/agent/order/oco — TP (limit) + SL (stop-limit) atrelados.

    side='sell' (default) saida de posicao long. Stop-limit = stop_loss * 0.99
    (1% buffer p/ aumentar fill no triggering, padrao do dashboard).
    """
    stop_limit = stop_loss * (0.99 if side.lower() == "sell" else 1.01)
    body = {
        "env": env,
        "ticker": ticker,
        "exchange": "B",
        "quantity": quantity,
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "stop_limit": stop_limit,
        "order_side": side.lower(),
        "is_daytrade": is_daytrade,
    }
    url = f"{base_url.rstrip('/')}/api/v1/agent/order/oco"
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        r = await client.post(url, json=body)
        r.raise_for_status()
        return r.json()


async def dispatch_order(
    *,
    dsn: str,
    base_url: str,
    signal_log_id: int,
    strategy_id: int,
    ticker: str,
    side: str,  # 'buy'|'sell'
    quantity: float,
    price: float | None = None,
    order_type: str = "market",
    take_profit: float | None = None,
    stop_loss: float | None = None,
    is_daytrade: bool = True,
    env: str = "simulation",
    computed_at: datetime | None = None,
) -> dict[str, Any]:
    """
    Pipeline completo: persist intent -> POST send -> (optional) POST OCO ->
    UPDATE intent + signal_log com local_order_id.

    Retorna dict {ok, intent_id, local_order_id, cl_ord_id, error?}.
    Erro nao levanta — best-effort (worker continua).
    """
    cl_ord_id = make_cl_ord_id(
        strategy_id=strategy_id, ticker=ticker, action=side.upper(), computed_at=computed_at
    )

    intent_id = insert_intent(
        dsn=dsn,
        signal_log_id=signal_log_id,
        strategy_id=strategy_id,
        ticker=ticker,
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
        take_profit=take_profit,
        stop_loss=stop_loss,
        cl_ord_id=cl_ord_id,
    )
    if intent_id is None:
        return {"ok": False, "error": "insert_intent_failed", "cl_ord_id": cl_ord_id}

    log = logger.bind(intent_id=intent_id, ticker=ticker, side=side, cl_ord_id=cl_ord_id)

    # 1. Send order
    try:
        send_resp = await post_order(
            base_url=base_url,
            side=side,
            ticker=ticker,
            quantity=quantity,
            price=price,
            order_type=order_type,
            cl_ord_id=cl_ord_id,
            is_daytrade=is_daytrade,
            env=env,
        )
    except Exception as exc:
        log.error("dispatcher.send_failed", error=str(exc))
        update_intent_sent(dsn=dsn, intent_id=intent_id, local_order_id=None, error_msg=str(exc))
        update_signal_log_sent(
            dsn=dsn, signal_log_id=signal_log_id, local_order_id=None, sent=False
        )
        return {"ok": False, "error": str(exc), "intent_id": intent_id, "cl_ord_id": cl_ord_id}

    local_order_id = send_resp.get("local_order_id") or send_resp.get("local_id")
    log.info("dispatcher.sent", local_order_id=local_order_id)

    update_intent_sent(dsn=dsn, intent_id=intent_id, local_order_id=local_order_id, error_msg=None)
    update_signal_log_sent(
        dsn=dsn, signal_log_id=signal_log_id, local_order_id=local_order_id, sent=True
    )

    # 2. Anexar OCO se TP + SL fornecidos (somente p/ entry BUY long; SHORT
    #    futuro vai precisar logica reversa — defer R1.P3).
    if take_profit and stop_loss and side.lower() == "buy":
        try:
            await post_oco(
                base_url=base_url,
                ticker=ticker,
                quantity=quantity,
                take_profit=take_profit,
                stop_loss=stop_loss,
                side="sell",  # OCO atrela um SELL TP/SL para fechar a posicao long
                is_daytrade=is_daytrade,
                env=env,
            )
            log.info("dispatcher.oco_attached", tp=take_profit, sl=stop_loss)
        except Exception as exc:  # noqa: BLE001
            # OCO falhar nao zera a entry — apenas log + alert
            log.warning("dispatcher.oco_failed", error=str(exc))

    return {
        "ok": True,
        "intent_id": intent_id,
        "local_order_id": local_order_id,
        "cl_ord_id": cl_ord_id,
        "send_response": send_resp,
    }


# ── Pair dispatcher (R3.2.B.2) ───────────────────────────────────────────────


def make_pair_cl_ord_id(
    *,
    pair_key: str,
    leg: str,
    action: str,
    computed_at: datetime | None = None,
) -> str:
    """
    Identificador deterministico p/ legs de um pair trade.

    Format: pairs:{pair_key}:{leg}:{action}:{minuto_iso}
    Ex: pairs:CMIN3-VALE3:a:OPEN:2026-05-01T12:35

    leg = 'a' ou 'b'. Mesma minuto -> mesmo cl_ord_id (idempotencia).
    """
    ts = computed_at or datetime.now(UTC)
    minute = ts.replace(second=0, microsecond=0).isoformat()
    raw = f"pairs:{pair_key}:{leg}:{action}:{minute}"
    if len(raw) <= 64:
        return raw
    return "pairs:" + hashlib.sha256(raw.encode()).hexdigest()[:48]


async def dispatch_pair_order(
    *,
    base_url: str,
    pair_key: str,  # 'CMIN3-VALE3'
    ticker_a: str,
    side_a: str,  # 'buy'|'sell'
    quantity_a: float,
    ticker_b: str,
    side_b: str,
    quantity_b: float,
    action: str,  # 'OPEN_SHORT'|'OPEN_LONG'|'CLOSE'|'STOP'
    is_daytrade: bool = True,
    env: str = "simulation",
    timeout_sec: float = ORDER_TIMEOUT_SEC,
    computed_at: datetime | None = None,
) -> dict[str, Any]:
    """
    Dispara 2 ordens de mercado simultaneas (long A + short B ou inverso).

    SEM intent persistence aqui — pairs flow tem proprio audit. Strategy
    no worker deve registrar em robot_signals_log antes de chamar; legs
    persistem em profit_orders normalmente via callback DLL (cl_ord_id
    permite reconcile depois).

    Retorna {ok, leg_a, leg_b, naked_leg?}.
      ok=True quando AMBAS as legs aceitas pelo proxy.
      ok=False com naked_leg='a'|'b' indica leg fillable -> alerta caller.

    Naked leg risk handling: se leg_b falha apos leg_a sucesso, NAO
    tentamos rollback automatico (cancel pode falhar tambem -> caos).
    Caller deve emitir alert (Pushover) p/ revisao manual.
    """
    cl_a = make_pair_cl_ord_id(pair_key=pair_key, leg="a", action=action, computed_at=computed_at)
    cl_b = make_pair_cl_ord_id(pair_key=pair_key, leg="b", action=action, computed_at=computed_at)

    log = logger.bind(pair_key=pair_key, action=action, cl_a=cl_a, cl_b=cl_b)

    # 1. Leg A
    try:
        resp_a = await post_order(
            base_url=base_url,
            side=side_a,
            ticker=ticker_a,
            quantity=quantity_a,
            price=None,
            order_type="market",
            cl_ord_id=cl_a,
            is_daytrade=is_daytrade,
            env=env,
            timeout_sec=timeout_sec,
        )
    except Exception as exc:
        log.error("pair_dispatch.leg_a_failed", error=str(exc))
        return {
            "ok": False,
            "naked_leg": None,  # nenhuma leg foi enviada -> sem risco naked
            "error": f"leg_a_failed: {exc}",
            "cl_a": cl_a,
            "cl_b": cl_b,
        }

    log.info("pair_dispatch.leg_a_ok", local_order_id=resp_a.get("local_order_id"))

    # 2. Leg B
    try:
        resp_b = await post_order(
            base_url=base_url,
            side=side_b,
            ticker=ticker_b,
            quantity=quantity_b,
            price=None,
            order_type="market",
            cl_ord_id=cl_b,
            is_daytrade=is_daytrade,
            env=env,
            timeout_sec=timeout_sec,
        )
    except Exception as exc:
        log.error("pair_dispatch.leg_b_failed_NAKED", error=str(exc))
        return {
            "ok": False,
            "naked_leg": "a",  # leg A executou, leg B falhou -> NAKED
            "error": f"leg_b_failed: {exc}",
            "cl_a": cl_a,
            "cl_b": cl_b,
            "leg_a": resp_a,
        }

    log.info("pair_dispatch.both_legs_ok", local_order_id=resp_b.get("local_order_id"))
    return {
        "ok": True,
        "leg_a": resp_a,
        "leg_b": resp_b,
        "cl_a": cl_a,
        "cl_b": cl_b,
    }
