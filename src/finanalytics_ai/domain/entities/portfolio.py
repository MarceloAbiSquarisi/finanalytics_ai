"""
Entidade Portfolio e Position.

Design decision: Portfolio é a aggregate root. Position representa
a posição do investidor em um ativo específico. O Portfolio é responsável
por manter a consistência (invariantes) das posições.
"""
from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from finanalytics_ai.domain.value_objects.money import Money, Ticker, Currency, Quantity
from finanalytics_ai.exceptions import InsufficientFundsError, PortfolioNotFoundError


@dataclass
class Position:
    """Posição em um ativo dentro de um portfólio."""
    ticker: Ticker
    quantity: Quantity
    average_price: Money
    asset_class: str = "stock"

    @property
    def total_cost(self) -> Money:
        return self.average_price * self.quantity.value

    def update_with_purchase(self, qty: Quantity, price: Money) -> "Position":
        """
        Calcula novo preço médio após compra (FIFO simplificado).
        PM = (qtd_atual * pm_atual + qtd_nova * preço_novo) / qtd_total
        """
        new_qty = self.quantity + qty
        new_cost = self.total_cost + (price * qty.value)
        new_avg = Money(new_cost.amount / new_qty.value, self.average_price.currency)
        return Position(
            ticker=self.ticker,
            quantity=new_qty,
            average_price=new_avg,
            asset_class=self.asset_class,
        )

    def profit_loss(self, current_price: Money) -> Money:
        return (current_price * self.quantity.value) - self.total_cost

    def profit_loss_pct(self, current_price: Money) -> Decimal:
        if self.total_cost.is_zero():
            return Decimal("0")
        pl = self.profit_loss(current_price)
        return (pl.amount / self.total_cost.amount) * Decimal("100")


@dataclass
class Portfolio:
    """
    Aggregate Root: representa o portfólio de um investidor.
    
    Todas as operações de compra/venda passam por aqui para
    manter os invariantes do domínio.
    """
    portfolio_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = ""
    name: str = "Portfólio Principal"
    currency: Currency = Currency.BRL
    positions: dict[str, Position] = field(default_factory=dict)
    cash: Money = field(default_factory=lambda: Money.of("0"))
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def add_position(self, ticker: Ticker, quantity: Quantity, price: Money) -> None:
        """Registra compra. Deduz do caixa e atualiza/cria posição."""
        cost = price * quantity.value
        if cost > self.cash:
            raise InsufficientFundsError(
                message=f"Saldo insuficiente: necessário {cost}, disponível {self.cash}",
                context={"required": str(cost), "available": str(self.cash)},
            )
        existing = self.positions.get(ticker.symbol)
        if existing:
            self.positions[ticker.symbol] = existing.update_with_purchase(quantity, price)
        else:
            self.positions[ticker.symbol] = Position(ticker=ticker, quantity=quantity, average_price=price)
        self.cash = self.cash - cost
        self.updated_at = datetime.utcnow()

    def remove_position(self, ticker: Ticker, quantity: Quantity, price: Money) -> Money:
        """Registra venda. Retorna o valor líquido creditado."""
        position = self.positions.get(ticker.symbol)
        if not position:
            raise PortfolioNotFoundError(
                message=f"Posição não encontrada: {ticker}",
                context={"ticker": str(ticker)},
            )
        proceeds = price * quantity.value
        new_qty_value = position.quantity.value - quantity.value
        if new_qty_value < Decimal("0"):
            raise InsufficientFundsError(
                message=f"Quantidade insuficiente para venda: tem {position.quantity}, tentou {quantity}",
            )
        if new_qty_value == Decimal("0"):
            del self.positions[ticker.symbol]
        else:
            self.positions[ticker.symbol] = Position(
                ticker=ticker,
                quantity=Quantity(new_qty_value),
                average_price=position.average_price,
                asset_class=position.asset_class,
            )
        self.cash = self.cash + proceeds
        self.updated_at = datetime.utcnow()
        return proceeds

    def total_invested(self) -> Money:
        result = Money.of("0", self.currency)
        for pos in self.positions.values():
            result = result + pos.total_cost
        return result

    def position_count(self) -> int:
        return len(self.positions)
