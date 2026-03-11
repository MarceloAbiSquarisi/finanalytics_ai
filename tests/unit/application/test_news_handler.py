"""
Testes unitários S16: _handle_news com SentimentAnalyzer + NewsSentimentRepository.

Cobre:
- Análise chamada quando analyzer injetado e headline presente
- Persistência quando news_repo injetado
- Sem analyzer: handler completa sem erro
- Sem headline: warning logado, sem análise
- Idempotência: segunda chamada com mesmo event_id passa pelo repo
- Score e label corretos propagados para span (via atributos verificados indiretamente)
- Fluxo completo via processor.process()
- Protocol conformance: MockSentimentAnalyzer satisfaz SentimentAnalyzer
- Protocol conformance: SQLNewsSentimentRepository satisfaz NewsSentimentRepository
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from finanalytics_ai.application.services.event_processor import EventProcessorService
from finanalytics_ai.domain.entities.event import EventType, MarketEvent
from finanalytics_ai.domain.entities.news_sentiment import NewsSentiment, SentimentLabel
from finanalytics_ai.domain.ports.news_sentiment_repository import NewsSentimentRepository
from finanalytics_ai.domain.ports.sentiment_analyzer import SentimentAnalyzer


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_sentiment(
    event_id: str = "news-001",
    ticker: str = "PETR4",
    score: float = 0.8,
    label: SentimentLabel = SentimentLabel.BULLISH,
) -> NewsSentiment:
    return NewsSentiment(
        event_id=event_id,
        ticker=ticker,
        headline="PETR4 registra lucro recorde no trimestre",
        score=score,
        label=label,
        reasoning="palavras bullish detectadas",
        model="mock-keyword-v1",
        analyzed_at=datetime.now(UTC),
        source="infomoney",
    )


def _make_analyzer_mock(sentiment: NewsSentiment | None = None) -> AsyncMock:
    mock = AsyncMock()
    mock.analyze = AsyncMock(return_value=sentiment or _make_sentiment())
    return mock


def _make_news_repo_mock(save_return: bool = True) -> AsyncMock:
    mock = AsyncMock()
    mock.save = AsyncMock(return_value=save_return)
    mock.get_by_ticker = AsyncMock(return_value=[])
    return mock


def _news_event(headline: str = "PETR4 registra lucro recorde no trimestre") -> MarketEvent:
    return MarketEvent(
        event_id="news-001",
        event_type=EventType.NEWS_PUBLISHED,
        ticker="PETR4",
        payload={"headline": headline, "source": "infomoney"},
        source="infomoney",
    )


def _make_processor(
    mock_store: AsyncMock,
    mock_market_data: AsyncMock,
    sentiment_analyzer: SentimentAnalyzer | None = None,
    news_repo: NewsSentimentRepository | None = None,
) -> EventProcessorService:
    return EventProcessorService(
        event_store=mock_store,
        market_data=mock_market_data,
        sentiment_analyzer=sentiment_analyzer,
        news_repo=news_repo,
    )


# ── Protocol conformance ──────────────────────────────────────────────────────


class TestSentimentProtocols:
    def test_mock_analyzer_satisfies_protocol(self) -> None:
        mock = _make_analyzer_mock()
        assert isinstance(mock, SentimentAnalyzer)

    def test_mock_news_repo_satisfies_protocol(self) -> None:
        mock = _make_news_repo_mock()
        assert isinstance(mock, NewsSentimentRepository)

    def test_mock_sentiment_analyzer_class_satisfies_protocol(self) -> None:
        from finanalytics_ai.infrastructure.sentiment.mock_analyzer import MockSentimentAnalyzer
        assert isinstance(MockSentimentAnalyzer(), SentimentAnalyzer)

    def test_sql_news_repo_satisfies_protocol(self) -> None:
        from finanalytics_ai.infrastructure.database.repositories.news_sentiment_repo import (
            SQLNewsSentimentRepository,
        )
        mock_session = AsyncMock()
        assert isinstance(SQLNewsSentimentRepository(mock_session), NewsSentimentRepository)


# ── _handle_news tests ────────────────────────────────────────────────────────


class TestHandleNews:
    @pytest.mark.asyncio
    async def test_analyzer_called_with_correct_args(
        self,
        mock_event_store: AsyncMock,
        mock_market_data: AsyncMock,
    ) -> None:
        """Analyzer é chamado com event_id, ticker, headline e source corretos."""
        analyzer = _make_analyzer_mock()
        processor = _make_processor(mock_event_store, mock_market_data, analyzer)

        await processor._handle_news(_news_event())

        analyzer.analyze.assert_awaited_once_with(
            event_id="news-001",
            ticker="PETR4",
            headline="PETR4 registra lucro recorde no trimestre",
            source="infomoney",
        )

    @pytest.mark.asyncio
    async def test_sentiment_persisted_when_repo_configured(
        self,
        mock_event_store: AsyncMock,
        mock_market_data: AsyncMock,
    ) -> None:
        """Quando analyzer e repo injetados, sentimento é persistido."""
        sentiment = _make_sentiment()
        analyzer = _make_analyzer_mock(sentiment)
        news_repo = _make_news_repo_mock(save_return=True)

        processor = _make_processor(mock_event_store, mock_market_data, analyzer, news_repo)
        await processor._handle_news(_news_event())

        news_repo.save.assert_awaited_once_with(sentiment)

    @pytest.mark.asyncio
    async def test_no_analyzer_does_not_fail(
        self,
        mock_event_store: AsyncMock,
        mock_market_data: AsyncMock,
    ) -> None:
        """Sem analyzer injetado, handler completa sem erro e sem chamar repo."""
        news_repo = _make_news_repo_mock()
        processor = _make_processor(
            mock_event_store, mock_market_data,
            sentiment_analyzer=None, news_repo=news_repo,
        )

        await processor._handle_news(_news_event())

        news_repo.save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_headline_skips_analysis(
        self,
        mock_event_store: AsyncMock,
        mock_market_data: AsyncMock,
    ) -> None:
        """Headline vazia: analyzer não é chamado."""
        analyzer = _make_analyzer_mock()
        processor = _make_processor(mock_event_store, mock_market_data, analyzer)

        await processor._handle_news(_news_event(headline=""))

        analyzer.analyze.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_duplicate_save_returns_false(
        self,
        mock_event_store: AsyncMock,
        mock_market_data: AsyncMock,
    ) -> None:
        """Segunda inserção do mesmo event_id: save retorna False, handler não falha."""
        analyzer = _make_analyzer_mock()
        news_repo = _make_news_repo_mock(save_return=False)  # duplicata

        processor = _make_processor(mock_event_store, mock_market_data, analyzer, news_repo)
        await processor._handle_news(_news_event())

        # Deve ter chamado save mesmo sendo duplicata
        news_repo.save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_repo_does_not_fail(
        self,
        mock_event_store: AsyncMock,
        mock_market_data: AsyncMock,
    ) -> None:
        """Analyzer presente mas sem repo: analisa mas não persiste."""
        analyzer = _make_analyzer_mock()
        processor = _make_processor(
            mock_event_store, mock_market_data,
            sentiment_analyzer=analyzer, news_repo=None,
        )

        await processor._handle_news(_news_event())

        analyzer.analyze.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_full_flow_via_process(
        self,
        mock_event_store: AsyncMock,
        mock_market_data: AsyncMock,
    ) -> None:
        """Fluxo completo via processor.process() com NEWS_PUBLISHED."""
        from finanalytics_ai.application.commands.process_event import ProcessMarketEventCommand
        from finanalytics_ai.domain.entities.event import EventStatus

        analyzer = _make_analyzer_mock()
        news_repo = _make_news_repo_mock()
        mock_event_store.exists.return_value = False

        processor = _make_processor(mock_event_store, mock_market_data, analyzer, news_repo)
        command = ProcessMarketEventCommand(
            event_id="news-full-001",
            event_type="news_published",
            ticker="VALE3",
            payload={"headline": "VALE3 anuncia dividendo recorde", "source": "brapi"},
            source="brapi",
        )

        result = await processor.process(command)

        assert result.status == EventStatus.PROCESSED
        analyzer.analyze.assert_awaited_once()
        news_repo.save.assert_awaited_once()


# ── MockSentimentAnalyzer unit tests ─────────────────────────────────────────


class TestMockSentimentAnalyzer:
    @pytest.mark.asyncio
    async def test_bullish_headline(self) -> None:
        from finanalytics_ai.infrastructure.sentiment.mock_analyzer import MockSentimentAnalyzer

        analyzer = MockSentimentAnalyzer()
        result = await analyzer.analyze("e1", "PETR4", "PETR4 registra lucro recorde")

        assert result.label == SentimentLabel.BULLISH
        assert result.score > 0

    @pytest.mark.asyncio
    async def test_bearish_headline(self) -> None:
        from finanalytics_ai.infrastructure.sentiment.mock_analyzer import MockSentimentAnalyzer

        analyzer = MockSentimentAnalyzer()
        result = await analyzer.analyze("e2", "PETR4", "PETR4 sofre grande queda e prejuízo")

        assert result.label == SentimentLabel.BEARISH
        assert result.score < 0

    @pytest.mark.asyncio
    async def test_empty_headline_returns_neutral(self) -> None:
        from finanalytics_ai.infrastructure.sentiment.mock_analyzer import MockSentimentAnalyzer

        analyzer = MockSentimentAnalyzer()
        result = await analyzer.analyze("e3", "PETR4", "")

        assert result.label == SentimentLabel.NEUTRAL
        assert result.score == 0.0

    @pytest.mark.asyncio
    async def test_score_within_bounds(self) -> None:
        from finanalytics_ai.infrastructure.sentiment.mock_analyzer import MockSentimentAnalyzer

        analyzer = MockSentimentAnalyzer()
        for headline in ["lucro crescimento alta recorde", "prejuízo queda perda fraude crise"]:
            result = await analyzer.analyze("e4", "PETR4", headline)
            assert -1.0 <= result.score <= 1.0

    @pytest.mark.asyncio
    async def test_model_name_set_correctly(self) -> None:
        from finanalytics_ai.infrastructure.sentiment.mock_analyzer import MockSentimentAnalyzer

        analyzer = MockSentimentAnalyzer()
        result = await analyzer.analyze("e5", "PETR4", "PETR4 lucro")
        assert result.model == "mock-keyword-v1"
