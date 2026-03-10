"""Port: EventStore — persistência e consulta de eventos."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from finanalytics_ai.domain.entities.event import EventStatus, MarketEvent


@runtime_checkable
class EventStore(Protocol):
    async def save(self, event: MarketEvent) -> None: ...
    async def find_by_id(self, event_id: str) -> MarketEvent | None: ...
    async def exists(self, event_id: str) -> bool: ...
    async def update_status(self, event_id: str, status: EventStatus, error: str = "") -> None: ...
    async def find_pending(self, limit: int = 100) -> list[MarketEvent]: ...
