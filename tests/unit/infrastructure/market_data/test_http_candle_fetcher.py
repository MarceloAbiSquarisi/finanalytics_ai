"""
Testes do HttpCandleFetcher (R3 polish 01/mai).

Cobertura:
- Bars com 'close' válidos -> retorna últimos N closes
- Bars com 'close' None ou ausente -> filtra
- response.bars pode ser 'bars' OR 'candles' (fallback)
- HTTP error -> None silently
- Network error -> None silently
- Empty bars -> None
- Timeout customizado / range_period customizado refletidos na request

Mocks: httpx.Client via MockTransport. Sync fetcher.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from finanalytics_ai.infrastructure.market_data.http_candle_fetcher import (
    HttpCandleFetcher,
)


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def _patched_client(transport):
    """Patch httpx.Client pra usar transport mockado."""
    original = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    return patch(
        "finanalytics_ai.infrastructure.market_data.http_candle_fetcher.httpx.Client",
        side_effect=factory,
    )


# ── Happy paths ──────────────────────────────────────────────────────────────


class TestHappyPath:
    def test_returns_last_n_closes(self) -> None:
        bars = [{"close": float(i), "time": i} for i in range(1, 11)]
        captured: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["url"] = str(req.url)
            return httpx.Response(200, json={"bars": bars})

        with _patched_client(_mock_transport(handler)):
            f = HttpCandleFetcher("http://api:8000")
            closes = f.fetch_closes("PETR4", n=3)

        assert closes == [8.0, 9.0, 10.0]
        assert "/api/v1/marketdata/candles/PETR4" in captured["url"]
        assert "range_period=1y" in captured["url"]

    def test_n_larger_than_bars_returns_all(self) -> None:
        bars = [{"close": 10.0}, {"close": 11.0}]

        def handler(req):
            return httpx.Response(200, json={"bars": bars})

        with _patched_client(_mock_transport(handler)):
            f = HttpCandleFetcher("http://api:8000")
            closes = f.fetch_closes("PETR4", n=100)
        assert closes == [10.0, 11.0]

    def test_falls_back_to_candles_key(self) -> None:
        """Endpoint pode retornar 'candles' em vez de 'bars'."""

        def handler(req):
            return httpx.Response(200, json={"candles": [{"close": 50.0}]})

        with _patched_client(_mock_transport(handler)):
            f = HttpCandleFetcher("http://api:8000")
            closes = f.fetch_closes("PETR4", n=1)
        assert closes == [50.0]

    def test_filters_null_close_bars(self) -> None:
        """Bars sem close ou close=None são filtrados."""
        bars = [
            {"close": 10.0},
            {"close": None},  # filtrado
            {"open": 1, "high": 2},  # sem close — filtrado
            {"close": 11.0},
        ]

        def handler(req):
            return httpx.Response(200, json={"bars": bars})

        with _patched_client(_mock_transport(handler)):
            f = HttpCandleFetcher("http://api:8000")
            closes = f.fetch_closes("PETR4", n=10)
        assert closes == [10.0, 11.0]


# ── Failure paths ────────────────────────────────────────────────────────────


class TestFailures:
    def test_http_error_returns_none(self) -> None:
        def handler(req):
            return httpx.Response(503, json={"error": "down"})

        with _patched_client(_mock_transport(handler)):
            f = HttpCandleFetcher("http://api:8000")
            closes = f.fetch_closes("PETR4", n=5)
        assert closes is None

    def test_network_error_returns_none(self) -> None:
        def handler(req):
            raise httpx.ConnectError("network unreachable", request=req)

        with _patched_client(_mock_transport(handler)):
            f = HttpCandleFetcher("http://api:8000")
            closes = f.fetch_closes("PETR4", n=5)
        assert closes is None

    def test_empty_bars_returns_none(self) -> None:
        def handler(req):
            return httpx.Response(200, json={"bars": []})

        with _patched_client(_mock_transport(handler)):
            f = HttpCandleFetcher("http://api:8000")
            closes = f.fetch_closes("PETR4", n=5)
        assert closes is None

    def test_invalid_json_returns_none(self) -> None:
        def handler(req):
            return httpx.Response(200, content=b"not json")

        with _patched_client(_mock_transport(handler)):
            f = HttpCandleFetcher("http://api:8000")
            closes = f.fetch_closes("PETR4", n=5)
        assert closes is None


# ── Customization ────────────────────────────────────────────────────────────


class TestCustom:
    def test_custom_range_period(self) -> None:
        captured: dict = {}

        def handler(req):
            captured["url"] = str(req.url)
            return httpx.Response(200, json={"bars": [{"close": 1.0}]})

        with _patched_client(_mock_transport(handler)):
            f = HttpCandleFetcher("http://api:8000", range_period="3mo")
            f.fetch_closes("PETR4", n=1)

        assert "range_period=3mo" in captured["url"]

    def test_base_url_trailing_slash_normalized(self) -> None:
        captured: dict = {}

        def handler(req):
            captured["url"] = str(req.url)
            return httpx.Response(200, json={"bars": [{"close": 1.0}]})

        with _patched_client(_mock_transport(handler)):
            f = HttpCandleFetcher("http://api:8000/")  # trailing slash
            f.fetch_closes("PETR4", n=1)
        # No double slash
        assert "//api/v1" not in captured["url"]
