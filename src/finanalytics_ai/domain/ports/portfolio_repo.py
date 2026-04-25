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
    async def has_active_holdings(self, portfolio_id: str) -> dict[str, int]: ...
    async def link_to_account(self, portfolio_id: str, account_id: str, user_id: str) -> bool: ...
    async def record_name_change(
        self,
        portfolio_id: str,
        old_name: str,
        new_name: str,
        changed_by: str | None,
    ) -> None: ...
    async def name_history(self, portfolio_id: str) -> list[dict[str, str | None]]: ...
