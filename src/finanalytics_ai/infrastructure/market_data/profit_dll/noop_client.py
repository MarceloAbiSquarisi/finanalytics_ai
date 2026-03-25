"""
infrastructure/market_data/profit_dll/noop_client.py

Stub NoOp do ProfitDLLClient para uso em Linux/Mac, testes e Docker.
Mesma interface que ProfitDLLClient — zero dependências Windows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Awaitable
import asyncio

from finanalytics_ai.observability.logging import get_logger

log = get_logger(__name__)


@dataclass
class ConnectionState:
    login_connected: bool = False
    market_connected: bool = False
    market_login_valid: bool = False

    @property
    def ready(self) -> bool:
        return False


class PriceTick:
    pass


class DailyBar:
    pass


class NoOpProfitClient:
    """
    Cliente NoOp — interface idêntica ao ProfitDLLClient.
    Usado em Docker, Linux e testes unitários.
    """

    def __init__(self, **kwargs) -> None:
        self._state = ConnectionState()
        self._on_tick_handlers = []
        self._on_daily_handlers = []

    async def start(self, loop=None) -> None:
        log.info("profit_dll.noop.started")

    async def wait_connected(self, timeout: float = 30.0) -> bool:
        log.warning("profit_dll.noop — sem conexão real (DLL Windows)")
        return False

    async def subscribe_tickers(self, tickers, exchange="B") -> None:
        log.info("profit_dll.noop.subscribe", tickers=tickers)

    async def unsubscribe_tickers(self, tickers, exchange="B") -> None:
        pass

    def add_tick_handler(self, handler) -> None:
        self._on_tick_handlers.append(handler)

    def add_daily_handler(self, handler) -> None:
        self._on_daily_handlers.append(handler)

    async def stop(self) -> None:
        log.info("profit_dll.noop.stopped")

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def subscribed_tickers(self) -> set:
        return set()
