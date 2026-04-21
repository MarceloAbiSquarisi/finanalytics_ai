"""
PortfolioService — Casos de uso de portfólio.

v2: suporte a múltiplas carteiras por usuário.
  Novos casos de uso: update_portfolio, delete_portfolio,
  set_default, compare_portfolios.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog

from finanalytics_ai.domain.entities.portfolio import Portfolio
from finanalytics_ai.domain.value_objects.money import Money, Quantity, Ticker
from finanalytics_ai.exceptions import PortfolioNotFoundError

if TYPE_CHECKING:
    from finanalytics_ai.application.commands.process_event import BuyAssetCommand, SellAssetCommand
    from finanalytics_ai.domain.ports.market_data import MarketDataProvider
    from finanalytics_ai.domain.ports.portfolio_repo import PortfolioRepository

logger = structlog.get_logger(__name__)

_MAX_PORTFOLIOS_PER_USER = 20  # guardrail contra abuso


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
    description: str
    benchmark: str
    is_default: bool
    user_id: str
    cash: str
    currency: str
    total_invested: str
    current_value: str
    total_profit_loss: str
    total_profit_loss_pct: str
    positions: list[PositionSnapshot]


@dataclass
class PortfolioComparison:
    """Resultado da comparação entre múltiplas carteiras."""

    portfolios: list[dict[str, str]]
    # Cada dict: portfolio_id, name, benchmark, total_invested,
    #            current_value, total_profit_loss_pct


class PortfolioService:
    def __init__(
        self,
        repo: PortfolioRepository,
        market_data: MarketDataProvider,
    ) -> None:
        self._repo = repo
        self._market_data = market_data

    async def create_portfolio(
        self,
        user_id: str,
        name: str,
        initial_cash: Decimal = Decimal("0"),
        description: str = "",
        benchmark: str = "",
    ) -> Portfolio:
        # Guardrail: limite de carteiras por usuário
        existing = await self._repo.find_by_user(user_id)
        if len(existing) >= _MAX_PORTFOLIOS_PER_USER:
            raise ValueError(f"Limite de {_MAX_PORTFOLIOS_PER_USER} carteiras atingido.")

        # Primeira carteira do usuário é sempre default
        is_default = len(existing) == 0

        portfolio = Portfolio(
            user_id=user_id,
            name=name,
            description=description,
            benchmark=benchmark.upper().strip() if benchmark else "",
            is_default=is_default,
        )
        if initial_cash > Decimal("0"):
            portfolio.cash = Money.of(initial_cash)
        await self._repo.save(portfolio)
        logger.info(
            "portfolio.created",
            portfolio_id=portfolio.portfolio_id,
            user_id=user_id,
            is_default=is_default,
        )
        return portfolio

    async def update_portfolio(
        self,
        portfolio_id: str,
        user_id: str,
        name: str | None = None,
        description: str | None = None,
        benchmark: str | None = None,
    ) -> Portfolio:
        portfolio = await self._get_and_assert_owner(portfolio_id, user_id)
        old_name = portfolio.name
        portfolio.update_metadata(name=name, description=description, benchmark=benchmark)
        # Rename audit: registra ANTES do save para que ambos os writes
        # caiam no mesmo ciclo de transacao da session.
        if name is not None and portfolio.name != old_name:
            await self._repo.record_name_change(
                portfolio_id=portfolio_id,
                old_name=old_name,
                new_name=portfolio.name,
                changed_by=user_id,
            )
            logger.info(
                "portfolio.renamed",
                portfolio_id=portfolio_id,
                old_name=old_name,
                new_name=portfolio.name,
                user_id=user_id,
            )
        await self._repo.save(portfolio)
        logger.info("portfolio.updated", portfolio_id=portfolio_id)
        return portfolio

    async def get_name_history(
        self, portfolio_id: str, user_id: str
    ) -> list[dict[str, str | None]]:
        """Retorna historico de renames do portfolio. Valida ownership."""
        await self._get_and_assert_owner(portfolio_id, user_id)
        return await self._repo.name_history(portfolio_id)

    async def deactivate_portfolio(self, portfolio_id: str, user_id: str) -> Portfolio:
        """
        Soft-delete: marca portfolio como inativo. Preserva historico/FKs.

        Restricoes:
        - Recusa (409) se houver holdings com saldo > 0 em qualquer
          tabela dependente (positions, crypto_holdings, rf_holdings,
          other_assets). Trades historicas NAO bloqueiam.
        - Se for o default, promove o portfolio ativo mais antigo restante
          como novo default. Se nao houver outro ativo, mantem flag
          (usuario fica sem default ate criar/reativar outro).
        """
        from fastapi import HTTPException, status

        portfolio = await self._get_and_assert_owner(portfolio_id, user_id)
        if not portfolio.is_active:
            return portfolio  # idempotente

        # Validacao de saldo
        holdings = await self._repo.has_active_holdings(portfolio_id)
        if holdings:
            detail = ", ".join(f"{k}={v}" for k, v in sorted(holdings.items()))
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Portfolio possui aplicacoes com saldo > 0 ({detail}). "
                    "Zere as posicoes antes de desativar."
                ),
            )

        # Promove novo default se necessario
        if portfolio.is_default:
            others_active = [
                p
                for p in await self._repo.find_by_user(user_id, include_inactive=False)
                if p.portfolio_id != portfolio_id
            ]
            if others_active:
                oldest = min(others_active, key=lambda p: p.created_at)
                oldest.is_default = True
                await self._repo.save(oldest)
            portfolio.is_default = False

        portfolio.is_active = False
        await self._repo.save(portfolio)
        logger.info("portfolio.deactivated", portfolio_id=portfolio_id, user_id=user_id)
        return portfolio

    async def reactivate_portfolio(self, portfolio_id: str, user_id: str) -> Portfolio:
        """Reativa um portfolio inativo. Idempotente; nao toca em is_default."""
        portfolio = await self._get_and_assert_owner(portfolio_id, user_id)
        if portfolio.is_active:
            return portfolio
        portfolio.is_active = True
        await self._repo.save(portfolio)
        logger.info("portfolio.reactivated", portfolio_id=portfolio_id, user_id=user_id)
        return portfolio

    async def set_default(self, portfolio_id: str, user_id: str) -> Portfolio:
        """Define uma carteira como default. Remove o flag das demais."""
        portfolio = await self._get_and_assert_owner(portfolio_id, user_id)
        if portfolio.is_default:
            return portfolio  # já é default, noop
        # Remove default de todas as carteiras do usuário atomicamente
        await self._repo.clear_default(user_id)
        portfolio.is_default = True
        await self._repo.save(portfolio)
        logger.info("portfolio.set_default", portfolio_id=portfolio_id, user_id=user_id)
        return portfolio

    async def compare_portfolios(self, portfolio_ids: list[str], user_id: str) -> PortfolioComparison:
        """
        Compara performance entre carteiras do usuário.
        Busca cotações em paralelo e retorna resumo comparativo.
        """
        import asyncio

        if len(portfolio_ids) < 2:
            raise ValueError("Informe ao menos 2 carteiras para comparar.")
        if len(portfolio_ids) > 10:
            raise ValueError("Máximo de 10 carteiras por comparação.")

        portfolios = []
        for pid in portfolio_ids:
            portfolios.append(await self._get_and_assert_owner(pid, user_id))

        # Coletar todos os tickers únicos para buscar cotações em paralelo
        all_tickers = {sym for p in portfolios for sym in p.positions}
        prices: dict[str, Money] = {}
        if all_tickers:
            tasks = {t: self._market_data.get_quote(Ticker(t)) for t in all_tickers}
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            for ticker, result in zip(tasks.keys(), results, strict=False):
                if isinstance(result, Money):
                    prices[ticker] = result

        comparison = []
        for p in portfolios:
            total_invested = p.total_invested()
            current_value = Money.of("0", p.currency)
            for sym, pos in p.positions.items():
                current_price = prices.get(sym, pos.average_price)
                current_value = current_value + (current_price * pos.quantity.value)

            total_pl = current_value - total_invested
            pl_pct = (
                (total_pl.amount / total_invested.amount * 100)
                if not total_invested.is_zero()
                else Decimal("0")
            )
            comparison.append(
                {
                    "portfolio_id": p.portfolio_id,
                    "name": p.name,
                    "benchmark": p.benchmark,
                    "is_default": str(p.is_default),
                    "total_invested": str(total_invested.amount),
                    "current_value": str(current_value.amount),
                    "total_profit_loss": str(total_pl.amount),
                    "total_profit_loss_pct": f"{pl_pct:.2f}",
                    "position_count": str(p.position_count()),
                    "cash": str(p.cash.amount),
                }
            )

        return PortfolioComparison(portfolios=comparison)

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
        import asyncio

        portfolio = await self._get_or_raise(portfolio_id)
        tickers = list(portfolio.positions.keys())
        prices: dict[str, Money] = {}
        if tickers:
            tasks = {t: self._market_data.get_quote(Ticker(t)) for t in tickers}
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            for ticker, result in zip(tasks.keys(), results, strict=False):
                if isinstance(result, Money):
                    prices[ticker] = result
                else:
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
            positions_snap.append(
                PositionSnapshot(
                    ticker=sym,
                    quantity=str(pos.quantity.value),
                    average_price=str(pos.average_price.amount),
                    current_price=str(current_price.amount),
                    total_cost=str(pos.total_cost.amount),
                    current_value=str(current_value.amount),
                    profit_loss=str(pl.amount),
                    profit_loss_pct=f"{pl_pct:.2f}",
                    asset_class=pos.asset_class,
                )
            )

        total_invested = portfolio.total_invested()
        total_pl = total_current_value - total_invested
        total_pl_pct = (
            (total_pl.amount / total_invested.amount * 100) if not total_invested.is_zero() else Decimal("0")
        )

        return PortfolioSnapshot(
            portfolio_id=portfolio.portfolio_id,
            name=portfolio.name,
            description=portfolio.description,
            benchmark=portfolio.benchmark,
            is_default=portfolio.is_default,
            user_id=portfolio.user_id,
            cash=str(portfolio.cash.amount),
            currency=portfolio.currency.value,
            total_invested=str(total_invested.amount),
            current_value=str(total_current_value.amount),
            total_profit_loss=str(total_pl.amount),
            total_profit_loss_pct=f"{total_pl_pct:.2f}",
            positions=positions_snap,
        )

    async def list_portfolios(
        self, user_id: str, include_inactive: bool = False
    ) -> list[Portfolio]:
        return await self._repo.find_by_user(user_id, include_inactive=include_inactive)

    async def _get_or_raise(self, portfolio_id: str) -> Portfolio:
        p = await self._repo.find_by_id(portfolio_id)
        if not p:
            raise PortfolioNotFoundError(
                message=f"Portfólio não encontrado: {portfolio_id}",
                context={"portfolio_id": portfolio_id},
            )
        return p

    async def _get_and_assert_owner(self, portfolio_id: str, user_id: str) -> Portfolio:
        """Busca portfólio e verifica ownership em uma operação."""
        from fastapi import HTTPException, status

        p = await self._get_or_raise(portfolio_id)
        if p.user_id != user_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Portfólio não pertence a este usuário.")
        return p
