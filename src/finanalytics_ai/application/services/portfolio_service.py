"""
PortfolioService — Casos de uso de portfólio.

Orquestra domínio + repository + dados de mercado.
Enriquece posições com cotações em tempo real via BRAPI.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
import structlog
from finanalytics_ai.application.commands.process_event import BuyAssetCommand, SellAssetCommand
from finanalytics_ai.domain.entities.portfolio import Portfolio
from finanalytics_ai.domain.ports.market_data import MarketDataProvider
from finanalytics_ai.domain.ports.portfolio_repo import PortfolioRepository
from finanalytics_ai.domain.value_objects.money import Money, Ticker, Quantity
from finanalytics_ai.exceptions import PortfolioNotFoundError

logger = structlog.get_logger(__name__)


@dataclass
class PositionSnapshot:
    ticker: str
    quantity: str
    average_price: str
    current_price: str
    total_cost: str
    current_value: str
    profit_loss: str
    profit_loss_pct: str
    asset_class: str


@dataclass
class PortfolioSnapshot:
    portfolio_id: str
    name: str
    user_id: str
    cash: str
    currency: str
    total_invested: str
    current_value: str
    total_profit_loss: str
    total_profit_loss_pct: str
    positions: list[PositionSnapshot]


class PortfolioService:
    def __init__(
        self,
        repo: PortfolioRepository,
        market_data: MarketDataProvider,
    ) -> None:
        self._repo = repo
        self._market_data = market_data

    async def create_portfolio(self, user_id: str, name: str, initial_cash: Decimal = Decimal("0")) -> Portfolio:
        portfolio = Portfolio(user_id=user_id, name=name)
        if initial_cash > Decimal("0"):
            portfolio.cash = Money.of(initial_cash)
        await self._repo.save(portfolio)
        logger.info("portfolio.created", portfolio_id=portfolio.portfolio_id, user_id=user_id)
        return portfolio

    async def buy(self, cmd: BuyAssetCommand) -> Portfolio:
        portfolio = await self._get_or_raise(cmd.portfolio_id)
        ticker = Ticker(cmd.ticker)
        quantity = Quantity.of(cmd.quantity)
        price = Money.of(cmd.price)
        portfolio.add_position(ticker, quantity, price)
        await self._repo.save(portfolio)
        logger.info("portfolio.buy", ticker=cmd.ticker, qty=str(cmd.quantity), price=str(cmd.price))
        return portfolio

    async def sell(self, cmd: SellAssetCommand) -> Portfolio:
        portfolio = await self._get_or_raise(cmd.portfolio_id)
        ticker = Ticker(cmd.ticker)
        quantity = Quantity.of(cmd.quantity)
        price = Money.of(cmd.price)
        portfolio.remove_position(ticker, quantity, price)
        await self._repo.save(portfolio)
        logger.info("portfolio.sell", ticker=cmd.ticker, qty=str(cmd.quantity), price=str(cmd.price))
        return portfolio

    async def get_snapshot(self, portfolio_id: str) -> PortfolioSnapshot:
        """
        Retorna snapshot enriquecido com cotações em tempo real.
        Cotações são buscadas em paralelo para performance.
        """
        import asyncio
        portfolio = await self._get_or_raise(portfolio_id)

        # Busca cotações em paralelo
        tickers = list(portfolio.positions.keys())
        prices: dict[str, Money] = {}
        if tickers:
            tasks = {t: self._market_data.get_quote(Ticker(t)) for t in tickers}
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            for ticker, result in zip(tasks.keys(), results):
                if isinstance(result, Money):
                    prices[ticker] = result
                else:
                    # fallback para preço médio se API falhar
                    prices[ticker] = portfolio.positions[ticker].average_price
                    logger.warning("quote.fallback", ticker=ticker, error=str(result))

        positions_snap: list[PositionSnapshot] = []
        total_current_value = Money.of("0")

        for sym, pos in portfolio.positions.items():
            current_price = prices.get(sym, pos.average_price)
            current_value = current_price * pos.quantity.value
            pl = pos.profit_loss(current_price)
            pl_pct = pos.profit_loss_pct(current_price)
            total_current_value = total_current_value + current_value
            positions_snap.append(PositionSnapshot(
                ticker=sym,
                quantity=str(pos.quantity.value),
                average_price=str(pos.average_price.amount),
                current_price=str(current_price.amount),
                total_cost=str(pos.total_cost.amount),
                current_value=str(current_value.amount),
                profit_loss=str(pl.amount),
                profit_loss_pct=f"{pl_pct:.2f}",
                asset_class=pos.asset_class,
            ))

        total_invested = portfolio.total_invested()
        total_pl = total_current_value - total_invested
        total_pl_pct = (
            (total_pl.amount / total_invested.amount * 100)
            if not total_invested.is_zero() else Decimal("0")
        )

        return PortfolioSnapshot(
            portfolio_id=portfolio.portfolio_id,
            name=portfolio.name,
            user_id=portfolio.user_id,
            cash=str(portfolio.cash.amount),
            currency=portfolio.currency.value,
            total_invested=str(total_invested.amount),
            current_value=str(total_current_value.amount),
            total_profit_loss=str(total_pl.amount),
            total_profit_loss_pct=f"{total_pl_pct:.2f}",
            positions=positions_snap,
        )

    async def list_portfolios(self, user_id: str) -> list[Portfolio]:
        return await self._repo.find_by_user(user_id)

    async def _get_or_raise(self, portfolio_id: str) -> Portfolio:
        p = await self._repo.find_by_id(portfolio_id)
        if not p:
            raise PortfolioNotFoundError(
                message=f"Portfólio não encontrado: {portfolio_id}",
                context={"portfolio_id": portfolio_id},
            )
        return p
