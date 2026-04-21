"""
finanalytics_ai.interfaces.api.routes.var
------------------------------------------
Rotas de Value at Risk (VaR).

POST /api/v1/var/calculate  -- calcula VaR para uma carteira
GET  /api/v1/var/portfolio/{portfolio_id}  -- VaR da carteira do usuario
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
import structlog

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/var", tags=["VaR"])


def _get_service(request: Request) -> Any:
    svc = getattr(request.app.state, "var_service", None)
    if svc is None:
        raise HTTPException(503, "VaRService nao inicializado")
    return svc


class PositionInput(BaseModel):
    ticker: str = Field(..., description="Ticker do ativo (ex: PETR4)")
    quantity: float = Field(..., gt=0)
    average_price: float = Field(..., gt=0)


class VaRRequest(BaseModel):
    positions: list[PositionInput] = Field(..., min_length=1)
    confidence_level: float = Field(0.95, description="0.90, 0.95 ou 0.99")
    lookback_days: int = Field(252, ge=30, le=504)


@router.post("/calculate", summary="Calcula VaR para uma lista de posicoes")
async def calculate_var(body: VaRRequest, request: Request) -> dict[str, Any]:
    svc = _get_service(request)
    positions = [
        {"ticker": p.ticker, "quantity": p.quantity, "average_price": p.average_price}
        for p in body.positions
    ]
    try:
        result = await svc.calculate(
            positions=positions,
            confidence_level=body.confidence_level,
            lookback_days=body.lookback_days,
        )
        return result.to_dict()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("var.calculate.error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc


@router.get("/portfolio/{portfolio_id}", summary="VaR da carteira do usuario")
async def portfolio_var(
    portfolio_id: str,
    request: Request,
    confidence_level: float = Query(0.95),
    lookback_days: int = Query(252),
) -> dict[str, Any]:
    """
    Busca as posicoes da carteira e calcula o VaR automaticamente.
    Requer autenticacao (Bearer token).
    """
    svc = _get_service(request)

    # Busca posicoes via portfolio service
    try:
        portfolio_svc = getattr(request.app.state, "portfolio_service", None)
        if portfolio_svc is None:
            raise HTTPException(503, "PortfolioService nao disponivel")

        snapshot = await portfolio_svc.get_snapshot(portfolio_id)
        if not snapshot or not snapshot.positions:
            raise HTTPException(404, "Carteira nao encontrada ou sem posicoes")

        positions = [
            {
                "ticker": p.ticker,
                "quantity": float(p.quantity),
                "average_price": float(p.average_price),
            }
            for p in snapshot.positions
        ]

        result = await svc.calculate(
            positions=positions,
            confidence_level=confidence_level,
            lookback_days=lookback_days,
        )
        d = result.to_dict()
        d["portfolio_id"] = portfolio_id
        d["portfolio_name"] = getattr(snapshot, "name", "")
        return d

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("var.portfolio.error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc
