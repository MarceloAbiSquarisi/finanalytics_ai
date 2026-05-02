"""
Testes unitários para EventProcessorService.

Usa mocks para EventStore e MarketDataProvider —
nenhum I/O real é feito nestes testes.
"""

from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, patch
from finanalytics_ai.application.commands.process_event import ProcessMarketEventCommand
from finanalytics_ai.application.services.event_processor import EventProcessorService
from finanalytics_ai.domain.entities.event import EventStatus, MarketEvent, EventType
from finanalytics_ai.exceptions import DuplicateEventError, EventProcessingError


@pytest.fixture
def processor(mock_event_store: AsyncMock, mock_market_data: AsyncMock) -> EventProcessorService:
    return EventProcessorService(
        event_store=mock_event_store,
        market_data=mock_market_data,
        max_retry_attempts=2,
    )


@pytest.fixture
def price_update_command() -> ProcessMarketEventCommand:
    return ProcessMarketEventCommand(
        event_id="evt-001",
        event_type="price_update",
        ticker="PETR4",
        payload={"price": "32.50"},
        source="brapi",
    )


class TestEventProcessorService:
    @pytest.mark.asyncio
    async def test_processes_new_event(
        self,
        processor: EventProcessorService,
        mock_event_store: AsyncMock,
        price_update_command: ProcessMarketEventCommand,
    ) -> None:
        mock_event_store.exists.return_value = False
        result = await processor.process(price_update_command)
        assert result.status == EventStatus.PROCESSED
        mock_event_store.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_duplicate_event(
        self,
        processor: EventProcessorService,
        mock_event_store: AsyncMock,
        price_update_command: ProcessMarketEventCommand,
        sample_market_event: MarketEvent,
    ) -> None:
        mock_event_store.exists.return_value = True
        mock_event_store.find_by_id.return_value = sample_market_event
        result = await processor.process(price_update_command)
        assert result.event_id == sample_market_event.event_id
        mock_event_store.save.assert_not_called()

    @pytest.mark.asyncio
    async def test_idempotency_event_id_is_key(
        self,
        processor: EventProcessorService,
        mock_event_store: AsyncMock,
        price_update_command: ProcessMarketEventCommand,
        sample_market_event: MarketEvent,
    ) -> None:
        """Mesmo event_id processado duas vezes deve retornar o mesmo resultado."""
        mock_event_store.exists.return_value = False
        first = await processor.process(price_update_command)

        mock_event_store.exists.return_value = True
        mock_event_store.find_by_id.return_value = first
        second = await processor.process(price_update_command)

        assert first.event_id == second.event_id
