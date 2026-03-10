"""
Rotas REST para gestão de portfólio — v2: múltiplas carteiras.

Endpoints novos:
  PATCH  /portfolios/{id}            — editar nome/descrição/benchmark
  DELETE /portfolios/{id}            — deletar carteira
  POST   /portfolios/{id}/set-default — definir como carteira padrão
  GET    /portfolios/compare          — comparar N carteiras
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from finanalytics_ai.application.commands.process_event import BuyAssetCommand, SellAssetCommand
from finanalytics_ai.application.services.portfolio_service import (
    PortfolioComparison,
    PortfolioService,
    PortfolioSnapshot,
)
from finanalytics_ai.interfaces.api.dependencies import get_current_user, get_portfolio_service

if TYPE_CHECKING:
    from finanalytics_ai.domain.auth.entities import User

router = APIRouter()


# ── Request / Response models ─────────────────────────────────────────────────


class CreatePortfolioRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=500)
    benchmark: str = Field(default="", max_length=20, description="Ex: IBOV, CDI, IPCA")
    initial_cash: Decimal = Field(default=Decimal("0"), ge=0)


class UpdatePortfolioRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    benchmark: str | None = Field(default=None, max_length=20)


class TradeRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10)
    quantity: Decimal = Field(..., gt=0)
    price: Decimal = Field(..., gt=0)
    broker: str = "manual"


class DepositRequest(BaseModel):
    amount: Decimal = Field(..., gt=0)


class PortfolioResponse(BaseModel):
    portfolio_id: str
    name: str
    description: str
    benchmark: str
    is_default: bool
    user_id: str
    message: str = "ok"


class PortfolioSummary(BaseModel):
    portfolio_id: str
    name: str
    description: str
    benchmark: str
    is_default: bool
    cash: str
    positions: int


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("", status_code=201, response_model=PortfolioResponse)
async def create_portfolio(
    body: CreatePortfolioRequest,
    current_user: User = Depends(get_current_user),
    svc: PortfolioService = Depends(get_portfolio_service),
) -> PortfolioResponse:
    p = await svc.create_portfolio(
        user_id=current_user.user_id,
        name=body.name,
        initial_cash=body.initial_cash,
        description=body.description,
        benchmark=body.benchmark,
    )
    return PortfolioResponse(
        portfolio_id=p.portfolio_id,
        name=p.name,
        description=p.description,
        benchmark=p.benchmark,
        is_default=p.is_default,
        user_id=p.user_id,
    )


@router.get("", response_model=list[PortfolioSummary])
async def list_portfolios(
    current_user: User = Depends(get_current_user),
    svc: PortfolioService = Depends(get_portfolio_service),
) -> list[PortfolioSummary]:
    portfolios = await svc.list_portfolios(current_user.user_id)
    return [
        PortfolioSummary(
            portfolio_id=p.portfolio_id,
            name=p.name,
            description=p.description,
            benchmark=p.benchmark,
            is_default=p.is_default,
            cash=str(p.cash.amount),
            positions=p.position_count(),
        )
        for p in portfolios
    ]


@router.get("/compare", response_model=PortfolioComparison)
async def compare_portfolios(
    ids: list[str] = Query(..., description="IDs das carteiras a comparar (mín. 2, máx. 10)"),
    current_user: User = Depends(get_current_user),
    svc: PortfolioService = Depends(get_portfolio_service),
) -> PortfolioComparison:
    """Compara performance entre carteiras do usuário autenticado."""
    try:
        return await svc.compare_portfolios(ids, current_user.user_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e


@router.get("/{portfolio_id}", response_model=PortfolioSnapshot)
async def get_portfolio(
    portfolio_id: str,
    current_user: User = Depends(get_current_user),
    svc: PortfolioService = Depends(get_portfolio_service),
) -> PortfolioSnapshot:
    snapshot = await svc.get_snapshot(portfolio_id)
    if snapshot.user_id != current_user.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Portfólio não pertence a este usuário.")
    return snapshot


@router.patch("/{portfolio_id}", response_model=PortfolioResponse)
async def update_portfolio(
    portfolio_id: str,
    body: UpdatePortfolioRequest,
    current_user: User = Depends(get_current_user),
    svc: PortfolioService = Depends(get_portfolio_service),
) -> PortfolioResponse:
    """Atualiza nome, descrição e/ou benchmark. Apenas campos enviados são alterados."""
    try:
        p = await svc.update_portfolio(
            portfolio_id=portfolio_id,
            user_id=current_user.user_id,
            name=body.name,
            description=body.description,
            benchmark=body.benchmark,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    return PortfolioResponse(
        portfolio_id=p.portfolio_id,
        name=p.name,
        description=p.description,
        benchmark=p.benchmark,
        is_default=p.is_default,
        user_id=p.user_id,
        message="Portfólio atualizado",
    )


@router.delete("/{portfolio_id}", status_code=204)
async def delete_portfolio(
    portfolio_id: str,
    current_user: User = Depends(get_current_user),
    svc: PortfolioService = Depends(get_portfolio_service),
) -> None:
    """
    Deleta a carteira. Se for a default, promove automaticamente
    a carteira mais antiga como nova default.
    """
    await svc.delete_portfolio(portfolio_id, current_user.user_id)


@router.post("/{portfolio_id}/set-default", response_model=PortfolioResponse)
async def set_default(
    portfolio_id: str,
    current_user: User = Depends(get_current_user),
    svc: PortfolioService = Depends(get_portfolio_service),
) -> PortfolioResponse:
    """Define esta carteira como a carteira padrão do usuário."""
    p = await svc.set_default(portfolio_id, current_user.user_id)
    return PortfolioResponse(
        portfolio_id=p.portfolio_id,
        name=p.name,
        description=p.description,
        benchmark=p.benchmark,
        is_default=p.is_default,
        user_id=p.user_id,
        message="Carteira definida como padrão",
    )


@router.post("/{portfolio_id}/buy", response_model=PortfolioResponse)
async def buy_asset(
    portfolio_id: str,
    body: TradeRequest,
    current_user: User = Depends(get_current_user),
    svc: PortfolioService = Depends(get_portfolio_service),
) -> PortfolioResponse:
    await svc._get_and_assert_owner(portfolio_id, current_user.user_id)
    cmd = BuyAssetCommand(
        portfolio_id=portfolio_id,
        ticker=body.ticker,
        quantity=body.quantity,
        price=body.price,
        broker=body.broker,
    )
    p = await svc.buy(cmd)
    return PortfolioResponse(
        portfolio_id=p.portfolio_id,
        name=p.name,
        description=p.description,
        benchmark=p.benchmark,
        is_default=p.is_default,
        user_id=p.user_id,
        message="Compra registrada",
    )


@router.post("/{portfolio_id}/sell", response_model=PortfolioResponse)
async def sell_asset(
    portfolio_id: str,
    body: TradeRequest,
    current_user: User = Depends(get_current_user),
    svc: PortfolioService = Depends(get_portfolio_service),
) -> PortfolioResponse:
    await svc._get_and_assert_owner(portfolio_id, current_user.user_id)
    cmd = SellAssetCommand(
        portfolio_id=portfolio_id,
        ticker=body.ticker,
        quantity=body.quantity,
        price=body.price,
        broker=body.broker,
    )
    p = await svc.sell(cmd)
    return PortfolioResponse(
        portfolio_id=p.portfolio_id,
        name=p.name,
        description=p.description,
        benchmark=p.benchmark,
        is_default=p.is_default,
        user_id=p.user_id,
        message="Venda registrada",
    )


@router.post("/{portfolio_id}/deposit", response_model=PortfolioResponse)
async def deposit(
    portfolio_id: str,
    body: DepositRequest,
    current_user: User = Depends(get_current_user),
    svc: PortfolioService = Depends(get_portfolio_service),
) -> PortfolioResponse:
    await svc._get_and_assert_owner(portfolio_id, current_user.user_id)
    from finanalytics_ai.domain.value_objects.money import Money

    p = await svc._get_or_raise(portfolio_id)
    p.cash = p.cash + Money.of(body.amount)
    await svc._repo.save(p)
    return PortfolioResponse(
        portfolio_id=p.portfolio_id,
        name=p.name,
        description=p.description,
        benchmark=p.benchmark,
        is_default=p.is_default,
        user_id=p.user_id,
        message=f"Depósito de R$ {body.amount} realizado",
    )
