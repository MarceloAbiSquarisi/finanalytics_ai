"""Port: PortfolioRepository — persistência de portfólios."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from finanalytics_ai.domain.entities.portfolio import Portfolio


@runtime_checkable
class PortfolioRepository(Protocol):
    async def save(self, portfolio: Portfolio) -> None: ...
    async def find_by_id(self, portfolio_id: str) -> Portfolio | None: ...
    async def find_by_user(
        self, user_id: str, include_inactive: bool = False
    ) -> list[Portfolio]: ...
    async def delete(self, portfolio_id: str) -> None: ...
    async def clear_default(self, user_id: str) -> None: ...
    async def has_active_holdings(self, portfolio_id: str) -> dict[str, int]: ...
