"""
tests/unit/infrastructure/test_market_data_client.py
──────────────────────────────────────────────────────
Testes para:
  - _to_yahoo_ticker(): normalização de tickers B3 → Yahoo
  - _normalize_volume(): limpeza de NaN/None
  - CompositeMarketDataClient: lógica de fallback
  - YahooFinanceClient.is_healthy(): sem yfinance instalado

Todos os testes são offline (sem rede). Yahoo e BRAPI são mockados.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── _to_yahoo_ticker ──────────────────────────────────────────────────────────


class TestToYahooTicker:
    """Normalização de ticker da B3 para formato Yahoo Finance."""

    def _norm(self, ticker: str) -> str:
        from finanalytics_ai.infrastructure.adapters.yahoo_client import _to_yahoo_ticker

        return _to_yahoo_ticker(ticker)

    def test_b3_stock_gets_sa_suffix(self) -> None:
        assert self._norm("PETR4") == "PETR4.SA"

    def test_b3_bank_gets_sa_suffix(self) -> None:
        assert self._norm("ITUB4") == "ITUB4.SA"

    def test_b3_etf_gets_sa_suffix(self) -> None:
        assert self._norm("BOVA11") == "BOVA11.SA"

    def test_b3_fii_gets_sa_suffix(self) -> None:
        assert self._norm("XPLG11") == "XPLG11.SA"

    def test_already_has_suffix_is_idempotent(self) -> None:
        assert self._norm("PETR4.SA") == "PETR4.SA"

    def test_uppercase_normalization(self) -> None:
        assert self._norm("petr4") == "PETR4.SA"

    def test_us_stock_no_digit_no_suffix(self) -> None:
        # AAPL não tem dígito no final → mantém como está
        assert self._norm("AAPL") == "AAPL"

    def test_us_stock_msft(self) -> None:
        assert self._norm("MSFT") == "MSFT"

    def test_with_whitespace_stripped(self) -> None:
        assert self._norm(" VALE3 ") == "VALE3.SA"

    def test_b3_preferred_stock(self) -> None:
        assert self._norm("WEGE3") == "WEGE3.SA"

    def test_b3_unit_stock(self) -> None:
        assert self._norm("SAPR11") == "SAPR11.SA"


# ── _normalize_volume ─────────────────────────────────────────────────────────


class TestNormalizeVolume:
    def _norm(self, v):
        from finanalytics_ai.infrastructure.adapters.yahoo_client import _normalize_volume

        return _normalize_volume(v)

    def test_int(self) -> None:
        assert self._norm(1_000_000) == 1_000_000

    def test_float(self) -> None:
        assert self._norm(500.0) == 500

    def test_none(self) -> None:
        assert self._norm(None) == 0

    def test_nan(self) -> None:
        import math

        assert self._norm(float("nan")) == 0

    def test_zero(self) -> None:
        assert self._norm(0) == 0

    def test_string_int(self) -> None:
        assert self._norm("12000") == 12000


# ── CompositeMarketDataClient ─────────────────────────────────────────────────


def _make_composite(brapi_bars=None, yahoo_bars=None, brapi_raises=False, yahoo_raises=False):
    """Helper: cria composite com mocks configurados."""
    from finanalytics_ai.infrastructure.adapters.market_data_client import CompositeMarketDataClient

    brapi = MagicMock()
    yahoo = MagicMock()

    if brapi_raises:
        brapi.get_ohlc_bars = AsyncMock(side_effect=Exception("BRAPI down"))
    else:
        brapi.get_ohlc_bars = AsyncMock(return_value=brapi_bars or [])

    if yahoo_raises:
        yahoo.get_ohlc_bars = AsyncMock(side_effect=Exception("Yahoo down"))
    else:
        yahoo.get_ohlc_bars = AsyncMock(return_value=yahoo_bars or [])

    brapi.is_healthy = AsyncMock(return_value=not brapi_raises)
    yahoo.is_healthy = AsyncMock(return_value=not yahoo_raises)
    brapi.close = AsyncMock()
    yahoo.close = AsyncMock()

    return CompositeMarketDataClient(brapi, yahoo)


def _make_bars(n: int) -> list[dict]:
    return [
        {"time": i, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 1000} for i in range(n)
    ]


class TestCompositeMarketDataClient:
    @pytest.mark.asyncio
    async def test_brapi_sufficient_no_yahoo_call(self) -> None:
        """BRAPI com dados suficientes → Yahoo não é chamado."""
        bars = _make_bars(50)
        c = _make_composite(brapi_bars=bars)
        result = await c.get_ohlc_bars("PETR4", "1y")
        assert result == bars
        c._yahoo.get_ohlc_bars.assert_not_called()

    @pytest.mark.asyncio
    async def test_brapi_insufficient_triggers_yahoo(self) -> None:
        """BRAPI com < 30 barras → Yahoo é acionado."""
        brapi_bars = _make_bars(5)
        yahoo_bars = _make_bars(250)
        c = _make_composite(brapi_bars=brapi_bars, yahoo_bars=yahoo_bars)
        result = await c.get_ohlc_bars("VALE3", "1y")
        assert result == yahoo_bars
        c._yahoo.get_ohlc_bars.assert_called_once()

    @pytest.mark.asyncio
    async def test_brapi_empty_triggers_yahoo(self) -> None:
        """BRAPI retorna lista vazia → usa Yahoo."""
        yahoo_bars = _make_bars(200)
        c = _make_composite(brapi_bars=[], yahoo_bars=yahoo_bars)
        result = await c.get_ohlc_bars("ITUB4", "1y")
        assert result == yahoo_bars

    @pytest.mark.asyncio
    async def test_brapi_raises_triggers_yahoo(self) -> None:
        """BRAPI lança exceção → usa Yahoo."""
        yahoo_bars = _make_bars(180)
        c = _make_composite(brapi_raises=True, yahoo_bars=yahoo_bars)
        result = await c.get_ohlc_bars("BBDC4", "1y")
        assert result == yahoo_bars

    @pytest.mark.asyncio
    async def test_both_fail_returns_empty(self) -> None:
        """BRAPI e Yahoo falham → retorna lista vazia."""
        c = _make_composite(brapi_raises=True, yahoo_raises=True)
        result = await c.get_ohlc_bars("PETR4", "1y")
        assert result == []

    @pytest.mark.asyncio
    async def test_long_range_goes_directly_to_yahoo(self) -> None:
        """Ranges 5y/10y/max → Yahoo direto, BRAPI não é chamado."""
        yahoo_bars = _make_bars(1300)
        c = _make_composite(yahoo_bars=yahoo_bars)
        result = await c.get_ohlc_bars("PETR4", "5y")
        assert result == yahoo_bars
        c._brapi.get_ohlc_bars.assert_not_called()

    @pytest.mark.asyncio
    async def test_10y_range_yahoo_primary(self) -> None:
        yahoo_bars = _make_bars(2500)
        c = _make_composite(yahoo_bars=yahoo_bars)
        result = await c.get_ohlc_bars("VALE3", "10y")
        assert len(result) == 2500
        c._brapi.get_ohlc_bars.assert_not_called()

    @pytest.mark.asyncio
    async def test_max_range_yahoo_primary(self) -> None:
        yahoo_bars = _make_bars(3000)
        c = _make_composite(yahoo_bars=yahoo_bars)
        result = await c.get_ohlc_bars("WEGE3", "max")
        assert len(result) == 3000

    @pytest.mark.asyncio
    async def test_long_range_yahoo_fails_brapi_fallback(self) -> None:
        """5y Yahoo falha → tenta BRAPI com 2y como fallback."""
        brapi_bars = _make_bars(500)
        c = _make_composite(brapi_bars=brapi_bars, yahoo_raises=True)
        result = await c.get_ohlc_bars("PETR4", "5y")
        assert result == brapi_bars
        # BRAPI deve ter sido chamado com "2y"
        c._brapi.get_ohlc_bars.assert_called_once()
        call_kwargs = c._brapi.get_ohlc_bars.call_args
        assert "2y" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_brapi_exactly_threshold_no_fallback(self) -> None:
        """BRAPI com exatamente MIN_BARS (30) → sem fallback."""
        from finanalytics_ai.infrastructure.adapters.market_data_client import MIN_BARS_THRESHOLD

        bars = _make_bars(MIN_BARS_THRESHOLD)
        c = _make_composite(brapi_bars=bars)
        result = await c.get_ohlc_bars("RENT3", "1y")
        assert result == bars
        c._yahoo.get_ohlc_bars.assert_not_called()

    @pytest.mark.asyncio
    async def test_brapi_below_threshold_triggers_fallback(self) -> None:
        """BRAPI com MIN_BARS - 1 barras → fallback acionado."""
        from finanalytics_ai.infrastructure.adapters.market_data_client import MIN_BARS_THRESHOLD

        brapi_bars = _make_bars(MIN_BARS_THRESHOLD - 1)
        yahoo_bars = _make_bars(250)
        c = _make_composite(brapi_bars=brapi_bars, yahoo_bars=yahoo_bars)
        result = await c.get_ohlc_bars("PETR4", "1y")
        assert result == yahoo_bars

    @pytest.mark.asyncio
    async def test_get_quote_delegates_to_brapi(self) -> None:
        """get_quote sempre usa BRAPI (cotação em tempo real)."""
        from finanalytics_ai.domain.value_objects.money import Money, Ticker
        from finanalytics_ai.infrastructure.adapters.market_data_client import CompositeMarketDataClient

        brapi = MagicMock()
        brapi.get_quote = AsyncMock(return_value=Money.of("38.50"))
        brapi.close = AsyncMock()
        yahoo = MagicMock()
        yahoo.close = AsyncMock()
        c = CompositeMarketDataClient(brapi, yahoo)
        ticker = Ticker("PETR4")
        result = await c.get_quote(ticker)
        assert result == Money.of("38.50")
        brapi.get_quote.assert_called_once_with(ticker)

    @pytest.mark.asyncio
    async def test_is_healthy_true_if_either_available(self) -> None:
        """is_healthy retorna True se pelo menos um provider está up."""
        from finanalytics_ai.infrastructure.adapters.market_data_client import CompositeMarketDataClient

        brapi = MagicMock()
        brapi.is_healthy = AsyncMock(return_value=False)
        brapi.close = AsyncMock()
        yahoo = MagicMock()
        yahoo.is_healthy = AsyncMock(return_value=True)
        yahoo.close = AsyncMock()
        c = CompositeMarketDataClient(brapi, yahoo)
        assert await c.is_healthy() is True

    @pytest.mark.asyncio
    async def test_is_healthy_false_if_both_down(self) -> None:
        from finanalytics_ai.infrastructure.adapters.market_data_client import CompositeMarketDataClient

        brapi = MagicMock()
        brapi.is_healthy = AsyncMock(return_value=False)
        brapi.close = AsyncMock()
        yahoo = MagicMock()
        yahoo.is_healthy = AsyncMock(return_value=False)
        yahoo.close = AsyncMock()
        c = CompositeMarketDataClient(brapi, yahoo)
        assert await c.is_healthy() is False

    @pytest.mark.asyncio
    async def test_close_calls_both(self) -> None:
        c = _make_composite()
        await c.close()
        c._brapi.close.assert_called_once()
        c._yahoo.close.assert_called_once()


# ── YAHOO_PREFERRED_RANGES ────────────────────────────────────────────────────


class TestYahooPreferredRanges:
    def test_5y_in_preferred(self) -> None:
        from finanalytics_ai.infrastructure.adapters.market_data_client import YAHOO_PREFERRED_RANGES

        assert "5y" in YAHOO_PREFERRED_RANGES

    def test_10y_in_preferred(self) -> None:
        from finanalytics_ai.infrastructure.adapters.market_data_client import YAHOO_PREFERRED_RANGES

        assert "10y" in YAHOO_PREFERRED_RANGES

    def test_max_in_preferred(self) -> None:
        from finanalytics_ai.infrastructure.adapters.market_data_client import YAHOO_PREFERRED_RANGES

        assert "max" in YAHOO_PREFERRED_RANGES

    def test_1y_not_in_preferred(self) -> None:
        from finanalytics_ai.infrastructure.adapters.market_data_client import YAHOO_PREFERRED_RANGES

        assert "1y" not in YAHOO_PREFERRED_RANGES

    def test_2y_not_in_preferred(self) -> None:
        from finanalytics_ai.infrastructure.adapters.market_data_client import YAHOO_PREFERRED_RANGES

        assert "2y" not in YAHOO_PREFERRED_RANGES
