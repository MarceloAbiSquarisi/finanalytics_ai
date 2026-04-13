"""
agent.py — Proxy FastAPI → profit_agent (:8002)

Resolve o bloqueio do Kaspersky: o browser só acessa :8000 (FastAPI),
que repassa internamente para :8002 (profit_agent).

Endpoints expostos em /api/v1/agent/...
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)

AGENT_URL = os.getenv("PROFIT_AGENT_URL", "http://host.docker.internal:8002")
TIMEOUT   = httpx.Timeout(30.0, connect=5.0)

router = APIRouter(prefix="/api/v1/agent")


# ── Helper ────────────────────────────────────────────────────────────────────

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
    return await _get("/orders", {"env": env, "limit": limit})


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
    return await _post("/order/send", body)


@router.post("/order/cancel", tags=["Agent"])
async def agent_cancel_order(body: dict):
    """Cancela ordem pelo local_order_id."""
    return await _post("/order/cancel", body)


@router.post("/order/cancel_all", tags=["Agent"])
async def agent_cancel_all_orders(body: dict):
    """Cancela todas as ordens abertas."""
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
    return await _post("/order/change", body)


@router.post("/order/zero_position", tags=["Agent"])
async def agent_zero_position(body: dict):
    """Zera posição de um ativo (SendZeroPositionV2)."""
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
    return await _get(f"/position/{ticker.upper()}", {
        "exchange": exchange, "env": env, "type": type
    })


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


