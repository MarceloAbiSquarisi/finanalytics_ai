"""
Fila de eventos assíncrona.

Design decision: abstração com Protocol permite trocar de in-memory (dev/test)
para Redis/RabbitMQ em produção sem alterar o código da aplicação.
"""
from __future__ import annotations
import asyncio
from typing import Protocol, runtime_checkable
from finanalytics_ai.domain.entities.event import MarketEvent


@runtime_checkable
class EventQueue(Protocol):
    async def enqueue(self, event: MarketEvent) -> None: ...
    async def dequeue(self) -> MarketEvent: ...
    async def size(self) -> int: ...


class InMemoryEventQueue:
    """
    Fila in-memory baseada em asyncio.Queue.
    Ideal para desenvolvimento, testes e deploys single-process.
    
    Trade-off: sem persistência — eventos perdidos em restart.
    Para produção, use RedisEventQueue ou RabbitMQEventQueue.
    """

    def __init__(self, maxsize: int = 10_000) -> None:
        self._queue: asyncio.Queue[MarketEvent] = asyncio.Queue(maxsize=maxsize)

    async def enqueue(self, event: MarketEvent) -> None:
        await self._queue.put(event)

    async def dequeue(self) -> MarketEvent:
        return await self._queue.get()

    async def size(self) -> int:
        return self._queue.qsize()

    def task_done(self) -> None:
        self._queue.task_done()
