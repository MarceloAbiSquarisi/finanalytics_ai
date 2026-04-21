"""
ProfitDLLMessageSource — adapter entre ProfitDLLClient e EventConsumerWorker.

Implementa AsyncIterator[dict] compativel com EventConsumerWorker (Sprint U6).
Cada tick recebido da DLL e convertido em um dict com o formato de evento
que o pipeline de processamento espera:

    {
        "event_id":   "<uuid>",
        "event_type": "price.update",
        "source":     "profit_dll",
        "data": {
            "ticker":    "PETR4",
            "exchange":  "B",
            "price":     38.50,
            "volume":    1000.0,
            "timestamp": "2025-01-01T10:00:00+00:00"
        }
    }

Decisao de design: a conversao acontece aqui, nao no client.py.
O client.py e responsavel apenas por receber dados da DLL e colocar
na fila interna. Esta classe e responsavel pela semantica de evento.

Stop gracioso: stop() sinaliza o iterador. O loop do worker para apos
a mensagem atual e aguarda a fila esvaziar.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
import uuid

import structlog

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger(__name__)

# Formato esperado pelo _deserialize_event do EventConsumerWorker
_EVENT_TYPE_PRICE_UPDATE = "price.update"
_SOURCE = "profit_dll"


class ProfitDLLMessageSource:
    """
    AsyncIterator[dict] que drena os ticks do ProfitDLLClient.

    profit_client: instancia de ProfitDLLClient ou NoOpProfitClient.
    O tipo e Any para evitar import circular e dependencia de plataforma
    (ProfitDLLClient so existe no Windows).

    poll_timeout: tempo em segundos para aguardar novos ticks antes de
    verificar se o iterador foi parado. Valor baixo = mais responsivo ao stop.
    """

    def __init__(
        self,
        profit_client: Any,
        *,
        poll_timeout: float = 1.0,
    ) -> None:
        self._client = profit_client
        self._poll_timeout = poll_timeout
        self._running = False

    async def stop(self) -> None:
        self._running = False
        logger.info("profit_dll_source.stopping")

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[dict[str, Any]]:
        self._running = True
        queue: asyncio.Queue[Any] | None = getattr(self._client, "_tick_queue", None)

        if queue is None:
            logger.error("profit_dll_source.no_queue", client_type=type(self._client).__name__)
            return

        logger.info("profit_dll_source.started")

        while self._running:
            try:
                tick = await asyncio.wait_for(queue.get(), timeout=self._poll_timeout)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            message = _tick_to_event(tick)
            if message is not None:
                yield message

        logger.info("profit_dll_source.stopped")


def _tick_to_event(tick: Any) -> dict[str, Any] | None:
    """
    Converte um tick do ProfitDLLClient em dict de evento.

    O tick pode ser um dataclass (ProfitDLLClient real) ou dict (NoOpProfitClient).
    Trata ambos os casos por duck typing.
    """
    try:
        if isinstance(tick, dict):
            ticker = tick.get("ticker", "")
            price = float(tick.get("price", 0))
            volume = float(tick.get("volume", 0))
            timestamp = tick.get("timestamp") or datetime.now(tz=UTC)
        else:
            # Dataclass do ProfitDLLClient
            ticker = str(getattr(tick, "ticker", "") or "")
            price = float(getattr(tick, "price", 0) or 0)
            volume = float(getattr(tick, "volume", 0) or 0)
            raw_ts = getattr(tick, "timestamp", None)
            if isinstance(raw_ts, datetime):
                timestamp = raw_ts
            else:
                timestamp = datetime.now(tz=UTC)

        if not ticker or price <= 0:
            logger.debug("profit_dll_source.tick_skipped", ticker=ticker, price=price)
            return None

        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)

        return {
            "event_id": str(uuid.uuid4()),
            "event_type": _EVENT_TYPE_PRICE_UPDATE,
            "source": _SOURCE,
            "data": {
                "ticker": ticker.upper(),
                "exchange": str(getattr(tick, "exchange", "B") or "B"),
                "price": price,
                "volume": volume,
                "timestamp": timestamp.isoformat(),
            },
        }

    except Exception as exc:
        logger.warning("profit_dll_source.tick_conversion_error", error=str(exc))
        return None
