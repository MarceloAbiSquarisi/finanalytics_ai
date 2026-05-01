"""
Testes do auto_trader_dispatcher (R1.P2).

Cobertura:
  make_cl_ord_id          — deterministico por minuto, < 64 chars
  post_order              — body conforme contrato C5
  post_oco                — stop_limit calculado
  dispatch_order          — pipeline ok happy path + send_failed branch

Mocks: httpx.AsyncClient via httpx.MockTransport (oficial).
DB: mocks de psycopg2 para isolar do TimescaleDB.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from finanalytics_ai.workers.auto_trader_dispatcher import (
    dispatch_order,
    make_cl_ord_id,
    post_oco,
    post_order,
)


def _utc(year=2026, month=5, day=1, hour=12, minute=35) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# ── make_cl_ord_id ────────────────────────────────────────────────────────────


class TestClOrdId:
    def test_deterministic_per_minute(self) -> None:
        a = make_cl_ord_id(strategy_id=1, ticker="PETR4", action="BUY", computed_at=_utc())
        b = make_cl_ord_id(strategy_id=1, ticker="PETR4", action="BUY", computed_at=_utc())
        assert a == b

    def test_different_minute_different_id(self) -> None:
        a = make_cl_ord_id(strategy_id=1, ticker="PETR4", action="BUY", computed_at=_utc(minute=10))
        b = make_cl_ord_id(strategy_id=1, ticker="PETR4", action="BUY", computed_at=_utc(minute=20))
        assert a != b

    def test_different_action_different_id(self) -> None:
        a = make_cl_ord_id(strategy_id=1, ticker="PETR4", action="BUY", computed_at=_utc())
        b = make_cl_ord_id(strategy_id=1, ticker="PETR4", action="SELL", computed_at=_utc())
        assert a != b

    def test_format_readable(self) -> None:
        s = make_cl_ord_id(strategy_id=42, ticker="PETR4", action="BUY", computed_at=_utc())
        assert s.startswith("robot:42:PETR4:BUY:2026-05-01T12:35")

    def test_hash_fallback_for_long(self) -> None:
        # Ticker artificialmente longo -> raw > 64 chars -> hash
        long_ticker = "X" * 80
        s = make_cl_ord_id(strategy_id=999, ticker=long_ticker, action="BUY", computed_at=_utc())
        assert len(s) <= 64
        assert s.startswith("robot:")


# ── post_order ────────────────────────────────────────────────────────────────


def _mock_transport(captured: dict[str, Any], responder=None):
    """
    httpx.MockTransport handler que captura request + retorna resposta.
    responder(url, body) -> dict (default {"ok": True, "local_order_id": 12345}).
    """
    import json as _json

    def handler(req: httpx.Request) -> httpx.Response:
        captured.setdefault("calls", []).append(
            {"url": str(req.url), "body": _json.loads(req.read().decode() or "{}")}
        )
        if responder is not None:
            data = responder(str(req.url), captured["calls"][-1]["body"])
        else:
            data = {"ok": True, "local_order_id": 12345}
        if isinstance(data, Exception):
            raise data
        return httpx.Response(200, json=data)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
class TestPostOrder:
    async def test_body_includes_c5_handshake(self) -> None:
        captured: dict[str, Any] = {}

        # Patch AsyncClient para injetar o MockTransport
        original = httpx.AsyncClient

        def make_client(*args, **kwargs):
            kwargs["transport"] = _mock_transport(captured)
            return original(*args, **kwargs)

        with patch(
            "finanalytics_ai.workers.auto_trader_dispatcher.httpx.AsyncClient",
            side_effect=make_client,
        ):
            resp = await post_order(
                base_url="http://api:8000",
                side="buy",
                ticker="PETR4",
                quantity=100,
                price=None,
                order_type="market",
                cl_ord_id="robot:1:PETR4:BUY:2026-05-01T12:35",
            )

        assert resp["local_order_id"] == 12345
        call = captured["calls"][0]
        body = call["body"]
        assert body["_source"] == "auto_trader"
        assert body["_client_order_id"] == "robot:1:PETR4:BUY:2026-05-01T12:35"
        assert body["ticker"] == "PETR4"
        assert body["order_side"] == "buy"
        assert body["order_type"] == "market"
        assert body["quantity"] == 100
        assert body["price"] == -1  # market order
        assert "/api/v1/agent/order/send" in call["url"]


# ── post_oco ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestPostOCO:
    async def test_stop_limit_buffer_for_sell(self) -> None:
        captured: dict[str, Any] = {}
        original = httpx.AsyncClient

        def make_client(*args, **kwargs):
            kwargs["transport"] = _mock_transport(
                captured, responder=lambda u, b: {"oco_id": "abc"}
            )
            return original(*args, **kwargs)

        with patch(
            "finanalytics_ai.workers.auto_trader_dispatcher.httpx.AsyncClient",
            side_effect=make_client,
        ):
            await post_oco(
                base_url="http://api:8000",
                ticker="PETR4",
                quantity=100,
                take_profit=33.0,
                stop_loss=28.0,
                side="sell",
            )

        body = captured["calls"][0]["body"]
        assert body["take_profit"] == 33.0
        assert body["stop_loss"] == 28.0
        assert body["stop_limit"] == pytest.approx(28.0 * 0.99)
        assert body["order_side"] == "sell"


# ── dispatch_order pipeline ───────────────────────────────────────────────────


@pytest.mark.asyncio
class TestDispatchOrder:
    @patch("finanalytics_ai.workers.auto_trader_dispatcher.update_signal_log_sent")
    @patch("finanalytics_ai.workers.auto_trader_dispatcher.update_intent_sent")
    @patch("finanalytics_ai.workers.auto_trader_dispatcher.insert_intent")
    async def test_happy_path_buy_with_oco(
        self, m_insert, m_update_intent, m_update_signal
    ) -> None:
        m_insert.return_value = 999  # intent_id

        captured: dict[str, Any] = {}
        original = httpx.AsyncClient

        def responder(url, body):
            return {"local_order_id": 5555} if "send" in url else {"oco_id": "xyz"}

        def make_client(*args, **kwargs):
            kwargs["transport"] = _mock_transport(captured, responder=responder)
            return original(*args, **kwargs)

        with patch(
            "finanalytics_ai.workers.auto_trader_dispatcher.httpx.AsyncClient",
            side_effect=make_client,
        ):
            result = await dispatch_order(
                dsn="postgres://stub",
                base_url="http://api:8000",
                signal_log_id=42,
                strategy_id=1,
                ticker="PETR4",
                side="buy",
                quantity=100,
                take_profit=33.0,
                stop_loss=28.0,
                computed_at=_utc(),
            )

        assert result["ok"] is True
        assert result["intent_id"] == 999
        assert result["local_order_id"] == 5555

        m_insert.assert_called_once()
        m_update_intent.assert_called_once()
        m_update_signal.assert_called_once_with(
            dsn="postgres://stub", signal_log_id=42, local_order_id=5555, sent=True
        )
        calls = captured["calls"]
        assert len(calls) == 2
        assert "/order/send" in calls[0]["url"]
        assert "/order/oco" in calls[1]["url"]

    @patch("finanalytics_ai.workers.auto_trader_dispatcher.update_signal_log_sent")
    @patch("finanalytics_ai.workers.auto_trader_dispatcher.update_intent_sent")
    @patch("finanalytics_ai.workers.auto_trader_dispatcher.insert_intent")
    async def test_send_failure_persists_error(
        self, m_insert, m_update_intent, m_update_signal
    ) -> None:
        m_insert.return_value = 1000

        original = httpx.AsyncClient

        def make_client(*args, **kwargs):
            def boom(req: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("agent offline", request=req)

            kwargs["transport"] = httpx.MockTransport(boom)
            return original(*args, **kwargs)

        with patch(
            "finanalytics_ai.workers.auto_trader_dispatcher.httpx.AsyncClient",
            side_effect=make_client,
        ):
            result = await dispatch_order(
                dsn="postgres://stub",
                base_url="http://api:8000",
                signal_log_id=42,
                strategy_id=1,
                ticker="PETR4",
                side="buy",
                quantity=100,
            )

        assert result["ok"] is False
        assert "agent offline" in result["error"]
        # Intent UPDATE com error_msg, signal_log com sent=False
        m_update_intent.assert_called_once()
        kwargs = m_update_intent.call_args.kwargs
        assert kwargs["local_order_id"] is None
        assert "agent offline" in kwargs["error_msg"]
        m_update_signal.assert_called_once_with(
            dsn="postgres://stub", signal_log_id=42, local_order_id=None, sent=False
        )

    @patch("finanalytics_ai.workers.auto_trader_dispatcher.insert_intent")
    async def test_intent_insert_failure_aborts(self, m_insert) -> None:
        m_insert.return_value = None  # DB falhou

        result = await dispatch_order(
            dsn="postgres://stub",
            base_url="http://api:8000",
            signal_log_id=42,
            strategy_id=1,
            ticker="PETR4",
            side="buy",
            quantity=100,
        )

        assert result["ok"] is False
        assert result["error"] == "insert_intent_failed"

    @patch("finanalytics_ai.workers.auto_trader_dispatcher.update_signal_log_sent")
    @patch("finanalytics_ai.workers.auto_trader_dispatcher.update_intent_sent")
    @patch("finanalytics_ai.workers.auto_trader_dispatcher.insert_intent")
    async def test_no_oco_when_no_tp_sl(self, m_insert, m_update_intent, m_update_signal) -> None:
        """Sem TP+SL nao chama OCO endpoint."""
        m_insert.return_value = 200
        captured: dict[str, Any] = {}
        original = httpx.AsyncClient

        def make_client(*args, **kwargs):
            kwargs["transport"] = _mock_transport(
                captured, responder=lambda u, b: {"local_order_id": 1}
            )
            return original(*args, **kwargs)

        with patch(
            "finanalytics_ai.workers.auto_trader_dispatcher.httpx.AsyncClient",
            side_effect=make_client,
        ):
            await dispatch_order(
                dsn="postgres://stub",
                base_url="http://api:8000",
                signal_log_id=1,
                strategy_id=1,
                ticker="PETR4",
                side="buy",
                quantity=100,
                # Sem TP/SL
            )

        urls = [c["url"] for c in captured["calls"]]
        assert len(urls) == 1
        assert "/order/send" in urls[0]
