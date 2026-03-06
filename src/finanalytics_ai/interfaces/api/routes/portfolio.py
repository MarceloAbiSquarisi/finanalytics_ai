"""
Rotas REST para gestão de portfólio.

Endpoints:
  POST   /portfolios                  — Cria portfólio
  GET    /portfolios/{id}             — Snapshot com cotações em tempo real
  GET    /portfolios?user_id=...      — Lista portfólios do usuário
  POST   /portfolios/{id}/buy         — Registra compra
  POST   /portfolios/{id}/sell        — Registra venda
  POST   /portfolios/{id}/deposit     — Adiciona caixa
"""
from __future__ import annotations
from decimal import Decimal
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from finanalytics_ai.application.commands.process_event import BuyAssetCommand, SellAssetCommand
from finanalytics_ai.application.services.portfolio_service import PortfolioService, PortfolioSnapshot
from finanalytics_ai.interfaces.api.dependencies import get_portfolio_service

router = APIRouter()


class CreatePortfolioRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1, max_length=200)
    initial_cash: Decimal = Field(default=Decimal("0"), ge=0)


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
    user_id: str
    message: str = "ok"


@router.post("", status_code=201, response_model=PortfolioResponse)
async def create_portfolio(
    body: CreatePortfolioRequest,
    svc: PortfolioService = Depends(get_portfolio_service),
) -> PortfolioResponse:
    p = await svc.create_portfolio(body.user_id, body.name, body.initial_cash)
    return PortfolioResponse(portfolio_id=p.portfolio_id, name=p.name, user_id=p.user_id)


@router.get("", response_model=list[dict])
async def list_portfolios(
    user_id: str = Query(...),
    svc: PortfolioService = Depends(get_portfolio_service),
) -> list[dict]:
    portfolios = await svc.list_portfolios(user_id)
    return [
        {"portfolio_id": p.portfolio_id, "name": p.name,
         "cash": str(p.cash.amount), "positions": p.position_count()}
        for p in portfolios
    ]


@router.get("/{portfolio_id}", response_model=PortfolioSnapshot)
async def get_portfolio(
    portfolio_id: str,
    svc: PortfolioService = Depends(get_portfolio_service),
) -> PortfolioSnapshot:
    return await svc.get_snapshot(portfolio_id)


@router.post("/{portfolio_id}/buy", response_model=PortfolioResponse)
async def buy_asset(
    portfolio_id: str,
    body: TradeRequest,
    svc: PortfolioService = Depends(get_portfolio_service),
) -> PortfolioResponse:
    cmd = BuyAssetCommand(
        portfolio_id=portfolio_id,
        ticker=body.ticker,
        quantity=body.quantity,
        price=body.price,
        broker=body.broker,
    )
    p = await svc.buy(cmd)
    return PortfolioResponse(portfolio_id=p.portfolio_id, name=p.name,
                              user_id=p.user_id, message="Compra registrada")


@router.post("/{portfolio_id}/sell", response_model=PortfolioResponse)
async def sell_asset(
    portfolio_id: str,
    body: TradeRequest,
    svc: PortfolioService = Depends(get_portfolio_service),
) -> PortfolioResponse:
    cmd = SellAssetCommand(
        portfolio_id=portfolio_id,
        ticker=body.ticker,
        quantity=body.quantity,
        price=body.price,
        broker=body.broker,
    )
    p = await svc.sell(cmd)
    return PortfolioResponse(portfolio_id=p.portfolio_id, name=p.name,
                              user_id=p.user_id, message="Venda registrada")


@router.post("/{portfolio_id}/deposit", response_model=PortfolioResponse)
async def deposit(
    portfolio_id: str,
    body: DepositRequest,
    svc: PortfolioService = Depends(get_portfolio_service),
) -> PortfolioResponse:
    from finanalytics_ai.domain.ports.portfolio_repo import PortfolioRepository
    from finanalytics_ai.domain.value_objects.money import Money
    from finanalytics_ai.interfaces.api.dependencies import get_db_session, get_brapi_client
    p = await svc._get_or_raise(portfolio_id)
    p.cash = p.cash + Money.of(body.amount)
    await svc._repo.save(p)
    return PortfolioResponse(portfolio_id=p.portfolio_id, name=p.name,
                              user_id=p.user_id, message=f"Depósito de R$ {body.amount} realizado")
