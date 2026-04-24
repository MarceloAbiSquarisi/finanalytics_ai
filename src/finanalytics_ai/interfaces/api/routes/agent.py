"""
agent.py — Proxy FastAPI → profit_agent (:8002)

Resolve o bloqueio do Kaspersky: o browser só acessa :8000 (FastAPI),
que repassa internamente para :8002 (profit_agent).

Endpoints expostos em /api/v1/agent/...
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
import httpx
import structlog

from finanalytics_ai.domain.auth.entities import User
from finanalytics_ai.interfaces.api.dependencies import require_sudo

logger = structlog.get_logger(__name__)

AGENT_URL = os.getenv("PROFIT_AGENT_URL", "http://host.docker.internal:8002")
TIMEOUT = httpx.Timeout(30.0, connect=5.0)

router = APIRouter(prefix="/api/v1/agent")


# ── Helper ────────────────────────────────────────────────────────────────────


async def _inject_account(body: dict) -> dict:
    """Resolve conta DLL ativa e injeta credenciais no body do profit_agent.

    Unificacao U2 (24/abr): usa investment_accounts.is_dll_active=TRUE
    (antes era trading_accounts). Modelo agora e 1 so:
      - dll_account_type='simulator' -> NAO injeta creds (usa PROFIT_SIM_* do .env)
      - dll_account_type='real' -> injeta broker/account/password salvos

    Se nenhuma conta tiver is_dll_active=TRUE, profit_agent cai no
    fallback do .env (PROFIT_SIM_* em env=simulation).

    Guard C3 (24/abr): se conta ativa e 'real' e real_operations_allowed=FALSE,
    RECUSA a ordem com HTTP 403. Evita acidente de rodar estrategia em conta
    real sem liberacao explicita do ADMIN.
    """
    try:
        from finanalytics_ai.infrastructure.database.repositories.wallet_repo import WalletRepository
        account = await WalletRepository().get_dll_active()
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent.inject_account.failed", error=str(exc))
        return body

    if not account:
        return body  # sem conta DLL ativa -> fallback .env

    dll_type = account.get("dll_account_type")
    if dll_type == "simulator":
        body.setdefault("env", "simulation")
        if not body.get("user_account_id") or body["user_account_id"] == "sem_conta":
            body["user_account_id"] = account["id"]
        return body

    if dll_type == "real" and account.get("dll_broker_id"):
        # Guard: conta real exige real_operations_allowed=TRUE (ADMIN libera)
        if not account.get("real_operations_allowed"):
            logger.warning(
                "agent.real_operations_blocked",
                account_id=account.get("id"),
                apelido=account.get("apelido"),
            )
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "real_operations_not_allowed",
                    "message": (
                        "Conta DLL ativa e 'real' mas esta sem permissao para "
                        "operacoes reais. Peca para um ADMIN liberar via "
                        "PATCH /api/v1/wallet/accounts/{id}/real-operations."
                    ),
                    "account_id": account.get("id"),
                    "account_label": account.get("apelido") or account.get("institution_name"),
                },
            )
        body["_account_broker_id"] = account["dll_broker_id"]
        body["_account_id"] = account["dll_account_id"] or ""
        body["_routing_password"] = account.get("dll_routing_password") or ""
        body["_sub_account_id"] = account.get("dll_sub_account_id") or ""
        body.setdefault("env", "production")
        if not body.get("user_account_id") or body["user_account_id"] == "sem_conta":
            body["user_account_id"] = account["id"]
    return body


async def _get(path: str, params: dict | None = None) -> Any:
    """GET assíncrono para o profit_agent."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(f"{AGENT_URL}{path}", params=params)
            return r.json()
    except httpx.ConnectError:
        raise HTTPException(502, "profit_agent indisponivel (porta 8002)")
    except Exception as e:
        logger.warning("agent.proxy.get_error", path=path, error=str(e))
        raise HTTPException(502, str(e))


async def _post(path: str, body: dict | None = None) -> Any:
    """POST assíncrono para o profit_agent."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(f"{AGENT_URL}{path}", json=body or {})
            return r.json()
    except httpx.ConnectError:
        raise HTTPException(502, "profit_agent indisponivel (porta 8002)")
    except Exception as e:
        logger.warning("agent.proxy.post_error", path=path, error=str(e))
        raise HTTPException(502, str(e))


# ── Status / Health ───────────────────────────────────────────────────────────


@router.get("/health", tags=["Agent"])
async def agent_health():
    """Health check do profit_agent."""
    return await _get("/health")


@router.get("/status", tags=["Agent"])
async def agent_status():
    """Status completo do profit_agent (conexão, ticks, ordens, queue)."""
    return await _get("/status")


# ── Quotes ────────────────────────────────────────────────────────────────────


@router.get("/quotes", tags=["Agent"])
async def agent_quotes():
    """Cotações em tempo real de todos os tickers subscritos."""
    return await _get("/quotes")


# ── Ordens ───────────────────────────────────────────────────────────────────


@router.get("/orders", tags=["Agent"])
async def agent_orders(
    env: str = Query("simulation", description="simulation | production"),
    limit: int = Query(100, ge=1, le=500),
):
    """Lista ordens com filtros opcionais."""
    return await _get("/orders", {"limit": limit})


@router.post("/order/send", tags=["Agent"])
async def agent_send_order(body: dict):
    """
    Envia ordem limite ou a mercado.

    ```json
    {
      "env": "simulation",
      "order_type": "limit|market|stop",
      "order_side": "buy|sell",
      "ticker": "PETR4",
      "exchange": "B",
      "quantity": 100,
      "price": 49.50,
      "stop_price": -1,
      "user_account_id": "marcelo",
      "portfolio_id": "carteira_principal",
      "is_daytrade": true,
      "strategy_id": "manual"
    }
    ```
    """
    body = await _inject_account(body)
    return await _post("/order/send", body)


@router.post("/order/cancel", tags=["Agent"])
async def agent_cancel_order(body: dict):
    """Cancela ordem pelo local_order_id."""
    body = await _inject_account(body)
    return await _post("/order/cancel", body)


@router.post("/order/cancel_all", tags=["Agent"])
async def agent_cancel_all_orders(body: dict):
    """Cancela todas as ordens abertas."""
    body = await _inject_account(body)
    return await _post("/order/cancel_all", body)


@router.post("/order/change", tags=["Agent"])
async def agent_change_order(body: dict):
    """
    Altera ordem existente (SendChangeOrderV2).

    ```json
    {
      "env": "simulation",
      "local_order_id": 12345,
      "price": 50.00,
      "stop_price": -1,
      "quantity": 100
    }
    ```
    """
    body = await _inject_account(body)
    return await _post("/order/change", body)


@router.post("/order/zero_position", tags=["Agent"])
async def agent_zero_position(body: dict):
    """Zera posição de um ativo (SendZeroPositionV2)."""
    body = await _inject_account(body)
    return await _post("/order/zero_position", body)


# ── OCO ───────────────────────────────────────────────────────────────────────


@router.post("/order/oco", tags=["Agent"])
async def agent_send_oco(body: dict):
    """
    Envia par OCO (One Cancels Other).
    Take Profit (limit) + Stop Loss (stop-limit).
    Auto-cancelamento via monitor thread no agent.

    ```json
    {
      "env": "simulation",
      "ticker": "PETR4",
      "exchange": "B",
      "quantity": 100,
      "take_profit": 52.00,
      "stop_loss": 47.00,
      "stop_limit": 46.50,
      "order_side": "sell",
      "is_daytrade": true,
      "strategy_id": "oco1"
    }
    ```
    """
    body = await _inject_account(body)
    return await _post("/order/oco", body)


@router.get("/oco/status/{tp_id}", tags=["Agent"])
async def agent_oco_status(
    tp_id: int,
    env: str = Query("simulation"),
):
    """
    Status do par OCO.
    Retorna: ativo | take_profit_executado | stop_loss_executado | encerrado
    """
    return await _get(f"/oco/status/{tp_id}", {"env": env})


# ── Posições ──────────────────────────────────────────────────────────────────


@router.get("/positions", tags=["Agent"])
async def agent_positions(
    env: str = Query("simulation"),
):
    """Posição líquida calculada via banco (ordens executadas)."""
    return await _get("/positions", {"env": env})


@router.get("/positions/dll", tags=["Agent"])
async def agent_positions_dll(
    env: str = Query("simulation"),
):
    """
    Todas as ordens via EnumerateAllOrders (DLL).
    Reconcilia status no banco automaticamente.
    """
    return await _get("/positions/dll", {"env": env})


@router.get("/positions/assets", tags=["Agent"])
async def agent_position_assets(
    env: str = Query("simulation"),
):
    """Lista ativos com posição aberta via EnumerateAllPositionAssets (DLL)."""
    return await _get("/positions/assets", {"env": env})


@router.get("/position/{ticker}", tags=["Agent"])
async def agent_position_ticker(
    ticker: str,
    exchange: str = Query("B"),
    env: str = Query("simulation"),
    type: int = Query(1, description="1=DayTrade, 2=Consolidated, 0=sem filtro"),
):
    """
    Posição detalhada para ticker via GetPositionV2 (DLL).
    Retorna: open_qty, open_avg_price, open_side, daily_buy/sell_qty, etc.
    """
    return await _get(
        f"/position/{ticker.upper()}", {"exchange": exchange, "env": env, "type": type}
    )


# ── Histórico e coleta ────────────────────────────────────────────────────────


@router.post("/collect_history", tags=["Agent"])
async def agent_collect_history(body: dict):
    """Dispara coleta histórica de ticks para um ticker/dia."""
    return await _post("/collect_history", body)


@router.get("/ticks/{ticker}", tags=["Agent"])
async def agent_ticks(
    ticker: str,
    limit: int = Query(100, ge=1, le=1000),
):
    """Últimos ticks em memória do agent para o ticker."""
    return await _get(f"/ticks/{ticker.upper()}", {"limit": limit})


# ── Tickers CRUD (realtime + backfill) ────────────────────────────────────────


@router.get("/tickers", tags=["Agent"])
async def agent_tickers():
    return await _get("/tickers")


@router.get("/tickers/active", tags=["Agent"])
async def agent_tickers_active():
    return await _get("/tickers/active")


@router.post("/tickers/add", tags=["Agent"])
async def agent_tickers_add(body: dict):
    return await _post("/tickers/add", body)


@router.post("/tickers/remove", tags=["Agent"])
async def agent_tickers_remove(body: dict):
    return await _post("/tickers/remove", body)


@router.post("/tickers/toggle", tags=["Agent"])
async def agent_tickers_toggle(body: dict):
    return await _post("/tickers/toggle", body)


@router.get("/history/tickers", tags=["Agent"])
async def agent_history_tickers():
    return await _get("/history/tickers")


@router.post("/history/tickers/add", tags=["Agent"])
async def agent_history_tickers_add(body: dict):
    return await _post("/history/tickers/add", body)


@router.post("/history/tickers/toggle", tags=["Agent"])
async def agent_history_tickers_toggle(body: dict):
    return await _post("/history/tickers/toggle", body)


# ── Controle (requer sudo) ────────────────────────────────────────────────────


@router.post("/restart", tags=["Agent"])
async def agent_restart(user: User = Depends(require_sudo)):
    """
    Reinicia o profit_agent no host Windows. Requer X-Sudo-Token valido
    (5min, obtido via POST /api/v1/auth/sudo apos re-autenticar senha).
    Com NSSM instalado, o watchdog recria o processo em 2-5s.
    """
    logger.warning("agent.restart.requested", user=user.email)
    return await _post("/restart", {})
