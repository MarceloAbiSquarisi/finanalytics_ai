"""
Testes unitários para S15: _handle_ohlc_bar com OHLCBarRepository.

Cobre:
- Persistência quando repo está injetado e payload completo
- Idempotência: segunda chamada com mesma barra retorna False (duplicata)
- Payload incompleto: loga warning mas não falha
- Repo não configurado: handler continua sem erro
- Timestamp: ISO8601, epoch int, ausente (usa occurred_at)
- Timeframe: extraído do payload ou default "1m"
- Conformidade do Protocol: SQLOHLCBarRepository satisfaz OHLCBarRepository
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from finanalytics_ai.application.services.event_processor import EventProcessorService
from finanalytics_ai.domain.entities.event import EventType, MarketEvent, OHLCBar
from finanalytics_ai.domain.ports.ohlc_repository import OHLCBarRepository


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_ohlc_repo_mock(upsert_return: bool = True) -> AsyncMock:
    """Cria mock de OHLCBarRepository com upsert_bar e get_latest como AsyncMock."""
    from unittest.mock import AsyncMock as AM

    repo = AM()
    repo.upsert_bar = AM(return_value=upsert_return)
    repo.get_latest = AM(return_value=[])
    return repo


def _ohlc_event(payload: dict | None = None) -> MarketEvent:
    return MarketEvent(
        event_id="ohlc-001",
        event_type=EventType.OHLC_BAR_CLOSED,
        ticker="PETR4",
        payload=payload
        or {
            "open": "32.00",
            "high": "33.50",
            "low": "31.80",
            "close": "33.10",
            "volume": "1500000",
            "timeframe": "1d",
            "timestamp": "2024-01-15T18:00:00+00:00",
        },
        source="brapi",
    )


def _make_processor(
    mock_store: AsyncMock,
    mock_market_data: AsyncMock,
    ohlc_repo: OHLCBarRepository | None = None,
) -> EventProcessorService:
    return EventProcessorService(
        event_store=mock_store,
        market_data=mock_market_data,
        ohlc_repo=ohlc_repo,
    )


# ── Protocol conformance ────────────────────────────────────────────────────────


class TestOHLCBarRepositoryProtocol:
    def test_mock_satisfies_protocol(self) -> None:
        """AsyncMock com os métodos corretos satisfaz o Protocol."""
        mock_repo = _make_ohlc_repo_mock()
        assert isinstance(mock_repo, OHLCBarRepository)

    def test_sql_repo_satisfies_protocol(self) -> None:
        """SQLOHLCBarRepository satisfaz OHLCBarRepository sem herança."""
        from finanalytics_ai.infrastructure.database.repositories.ohlc_bar_repo import (
            SQLOHLCBarRepository,
        )

        mock_session = AsyncMock()
        repo = SQLOHLCBarRepository(mock_session)
        assert isinstance(repo, OHLCBarRepository)


# ── _handle_ohlc_bar tests ──────────────────────────────────────────────────────


class TestHandleOhlcBar:
    @pytest.mark.asyncio
    async def test_persists_bar_when_repo_configured(
        self,
        mock_event_store: AsyncMock,
        mock_market_data: AsyncMock,
    ) -> None:
        """Quando repo injetado e payload completo, barra é persistida."""
        mock_repo = _make_ohlc_repo_mock(upsert_return=True)

        processor = _make_processor(mock_event_store, mock_market_data, mock_repo)
        await processor._handle_ohlc_bar(_ohlc_event())

        mock_repo.upsert_bar.assert_awaited_once()
        bar: OHLCBar = mock_repo.upsert_bar.call_args[0][0]
        assert bar.ticker == "PETR4"
        assert bar.timeframe == "1d"
        assert bar.close == Decimal("33.10")
        assert bar.high == Decimal("33.50")
        assert bar.low == Decimal("31.80")
        assert bar.source == "brapi"

    @pytest.mark.asyncio
    async def test_parses_timestamp_iso8601(
        self,
        mock_event_store: AsyncMock,
        mock_market_data: AsyncMock,
    ) -> None:
        """Timestamp ISO8601 no payload é convertido corretamente."""
        mock_repo = _make_ohlc_repo_mock(upsert_return=True)

        processor = _make_processor(mock_event_store, mock_market_data, mock_repo)
        await processor._handle_ohlc_bar(_ohlc_event())

        bar: OHLCBar = mock_repo.upsert_bar.call_args[0][0]
        assert bar.timestamp == datetime(2024, 1, 15, 18, 0, 0, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_parses_timestamp_epoch(
        self,
        mock_event_store: AsyncMock,
        mock_market_data: AsyncMock,
    ) -> None:
        """Timestamp como epoch int é convertido para datetime UTC."""
        mock_repo = _make_ohlc_repo_mock(upsert_return=True)

        epoch = 1705338000  # 2024-01-15T18:00:00 UTC
        event = _ohlc_event(
            {
                "open": "32.00",
                "high": "33.50",
                "low": "31.80",
                "close": "33.10",
                "volume": "0",
                "timestamp": epoch,
            }
        )
        processor = _make_processor(mock_event_store, mock_market_data, mock_repo)
        await processor._handle_ohlc_bar(event)

        bar: OHLCBar = mock_repo.upsert_bar.call_args[0][0]
        assert bar.timestamp == datetime.fromtimestamp(epoch, tz=UTC)

    @pytest.mark.asyncio
    async def test_uses_occurred_at_when_no_timestamp(
        self,
        mock_event_store: AsyncMock,
        mock_market_data: AsyncMock,
    ) -> None:
        """Sem timestamp no payload, usa event.occurred_at."""
        mock_repo = _make_ohlc_repo_mock(upsert_return=True)

        event = _ohlc_event(
            {
                "open": "32.00",
                "high": "33.50",
                "low": "31.80",
                "close": "33.10",
            }
        )
        processor = _make_processor(mock_event_store, mock_market_data, mock_repo)
        await processor._handle_ohlc_bar(event)

        bar: OHLCBar = mock_repo.upsert_bar.call_args[0][0]
        assert bar.timestamp == event.occurred_at

    @pytest.mark.asyncio
    async def test_default_timeframe_is_1m(
        self,
        mock_event_store: AsyncMock,
        mock_market_data: AsyncMock,
    ) -> None:
        """Timeframe default é '1m' quando não especificado no payload."""
        mock_repo = _make_ohlc_repo_mock(upsert_return=True)

        event = _ohlc_event(
            {
                "open": "32.00",
                "high": "33.50",
                "low": "31.80",
                "close": "33.10",
            }
        )
        processor = _make_processor(mock_event_store, mock_market_data, mock_repo)
        await processor._handle_ohlc_bar(event)

        bar: OHLCBar = mock_repo.upsert_bar.call_args[0][0]
        assert bar.timeframe == "1m"

    @pytest.mark.asyncio
    async def test_no_repo_does_not_fail(
        self,
        mock_event_store: AsyncMock,
        mock_market_data: AsyncMock,
    ) -> None:
        """Sem repo injetado, handler completa sem erro."""
        processor = _make_processor(mock_event_store, mock_market_data, ohlc_repo=None)
        # Não deve levantar exceção
        await processor._handle_ohlc_bar(_ohlc_event())

    @pytest.mark.asyncio
    async def test_missing_close_skips_persistence(
        self,
        mock_event_store: AsyncMock,
        mock_market_data: AsyncMock,
    ) -> None:
        """Payload sem 'close' não persiste e não falha."""
        mock_repo = _make_ohlc_repo_mock()

        event = _ohlc_event({"open": "32.00", "high": "33.50", "low": "31.80"})
        processor = _make_processor(mock_event_store, mock_market_data, mock_repo)
        await processor._handle_ohlc_bar(event)

        mock_repo.upsert_bar.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_idempotency_duplicate_returns_false(
        self,
        mock_event_store: AsyncMock,
        mock_market_data: AsyncMock,
    ) -> None:
        """Segunda inserção da mesma barra retorna False (duplicata)."""
        mock_repo = _make_ohlc_repo_mock(upsert_return=False)  # duplicata

        processor = _make_processor(mock_event_store, mock_market_data, mock_repo)
        await processor._handle_ohlc_bar(_ohlc_event())

        # Deve ter chamado upsert mesmo sendo duplicata (repositório decide)
        mock_repo.upsert_bar.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_volume_zero_when_missing(
        self,
        mock_event_store: AsyncMock,
        mock_market_data: AsyncMock,
    ) -> None:
        """Volume ausente resulta em Decimal('0'), não None."""
        mock_repo = _make_ohlc_repo_mock(upsert_return=True)

        event = _ohlc_event(
            {
                "open": "32.00",
                "high": "33.50",
                "low": "31.80",
                "close": "33.10",
            }
        )
        processor = _make_processor(mock_event_store, mock_market_data, mock_repo)
        await processor._handle_ohlc_bar(event)

        bar: OHLCBar = mock_repo.upsert_bar.call_args[0][0]
        assert bar.volume == Decimal("0")

    @pytest.mark.asyncio
    async def test_full_event_processor_ohlc_flow(
        self,
        mock_event_store: AsyncMock,
        mock_market_data: AsyncMock,
    ) -> None:
        """Fluxo completo via processor.process() com OHLC_BAR_CLOSED."""
        from finanalytics_ai.application.commands.process_event import ProcessMarketEventCommand
        from finanalytics_ai.domain.entities.event import EventStatus

        mock_repo = _make_ohlc_repo_mock(upsert_return=True)
        mock_event_store.exists.return_value = False

        processor = _make_processor(mock_event_store, mock_market_data, mock_repo)
        command = ProcessMarketEventCommand(
            event_id="ohlc-full-001",
            event_type="ohlc_bar_closed",
            ticker="VALE3",
            payload={
                "open": "70.00",
                "high": "71.50",
                "low": "69.80",
                "close": "71.20",
                "volume": "2000000",
                "timeframe": "1h",
            },
            source="brapi",
        )

        result = await processor.process(command)

        assert result.status == EventStatus.PROCESSED
        mock_repo.upsert_bar.assert_awaited_once()
        bar: OHLCBar = mock_repo.upsert_bar.call_args[0][0]
        assert bar.ticker == "VALE3"
        assert bar.timeframe == "1h"
        assert bar.close == Decimal("71.20")
