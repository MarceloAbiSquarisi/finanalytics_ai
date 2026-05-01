"""
Testes do dispatcher dual-leg (R3.2.B.2).

Cobertura:
  make_pair_cl_ord_id
    - format determinístico minuto + leg + action
    - hash fallback p/ pair_key gigante
  dispatch_pair_order
    - happy path: ambas legs OK -> ok=True com leg_a/leg_b populados
    - leg_a falha -> ok=False, naked_leg=None (nada foi enviado)
    - leg_a OK + leg_b falha -> ok=False, naked_leg='a' (RISCO)
    - cl_ord_ids gerados batem com action e pair_key

Mocks: httpx.AsyncClient via MockTransport (oficial).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from finanalytics_ai.workers.auto_trader_dispatcher import (
    dispatch_pair_order,
    make_pair_cl_ord_id,
)


def _utc(year=2026, month=5, day=1, hour=12, minute=35) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# ── make_pair_cl_ord_id ──────────────────────────────────────────────────────


class TestPairClOrdId:
    def test_format(self) -> None:
        s = make_pair_cl_ord_id(
            pair_key="CMIN3-VALE3", leg="a", action="OPEN_SHORT_SPREAD",
            computed_at=_utc(),
        )
        assert s.startswith("pairs:CMIN3-VALE3:a:OPEN_SHORT_SPREAD:2026-05-01T12:35")

    def test_different_legs_different_id(self) -> None:
        a = make_pair_cl_ord_id(
            pair_key="CMIN3-VALE3", leg="a", action="OPEN_SHORT_SPREAD",
            computed_at=_utc(),
        )
        b = make_pair_cl_ord_id(
            pair_key="CMIN3-VALE3", leg="b", action="OPEN_SHORT_SPREAD",
            computed_at=_utc(),
        )
        assert a != b

    def test_deterministic_per_minute(self) -> None:
        a = make_pair_cl_ord_id(
            pair_key="CMIN3-VALE3", leg="a", action="CLOSE", computed_at=_utc()
        )
        b = make_pair_cl_ord_id(
            pair_key="CMIN3-VALE3", leg="a", action="CLOSE", computed_at=_utc()
        )
        assert a == b

    def test_different_minute_different_id(self) -> None:
        a = make_pair_cl_ord_id(
            pair_key="CMIN3-VALE3", leg="a", action="CLOSE",
            computed_at=_utc(minute=10),
        )
        b = make_pair_cl_ord_id(
            pair_key="CMIN3-VALE3", leg="a", action="CLOSE",
            computed_at=_utc(minute=20),
        )
        assert a != b

    def test_hash_fallback(self) -> None:
        # pair_key longo demais p/ 64 chars
        long_key = "X" * 80
        s = make_pair_cl_ord_id(
            pair_key=long_key, leg="a", action="OPEN_LONG_SPREAD",
            computed_at=_utc(),
        )
        assert len(s) <= 64
        assert s.startswith("pairs:")


# ── dispatch_pair_order ──────────────────────────────────────────────────────


def _mock_transport_router(response_map: dict[str, dict[str, Any]] | None = None,
                           fail_tickers: set[str] | None = None,
                           captured: dict[str, Any] | None = None):
    """
    MockTransport configurável: retorna response baseado no ticker do body,
    ou levanta ConnectError se ticker em fail_tickers.
    """
    if captured is None:
        captured = {}
    captured.setdefault("calls", [])

    def handler(req: httpx.Request) -> httpx.Response:
        import json as _json

        body = _json.loads(req.read().decode() or "{}")
        captured["calls"].append({"url": str(req.url), "body": body})
        ticker = body.get("ticker", "")
        if fail_tickers and ticker in fail_tickers:
            raise httpx.ConnectError(f"agent rejected {ticker}", request=req)
        resp = (response_map or {}).get(ticker, {"ok": True, "local_order_id": 9999})
        return httpx.Response(200, json=resp)

    return httpx.MockTransport(handler), captured


@pytest.mark.asyncio
class TestDispatchPairOrder:
    async def test_both_legs_ok(self) -> None:
        captured: dict[str, Any] = {}
        transport, captured = _mock_transport_router(
            response_map={
                "CMIN3": {"local_order_id": 111},
                "VALE3": {"local_order_id": 222},
            },
            captured=captured,
        )
        original = httpx.AsyncClient

        def make_client(*args, **kwargs):
            kwargs["transport"] = transport
            return original(*args, **kwargs)

        with patch(
            "finanalytics_ai.workers.auto_trader_dispatcher.httpx.AsyncClient",
            side_effect=make_client,
        ):
            result = await dispatch_pair_order(
                base_url="http://api:8000",
                pair_key="CMIN3-VALE3",
                ticker_a="CMIN3",
                side_a="sell",
                quantity_a=100,
                ticker_b="VALE3",
                side_b="buy",
                quantity_b=20,
                action="OPEN_SHORT_SPREAD",
                computed_at=_utc(),
            )

        assert result["ok"] is True
        assert result["leg_a"]["local_order_id"] == 111
        assert result["leg_b"]["local_order_id"] == 222
        # Ambos cl_ord_ids encoded
        assert result["cl_a"].startswith("pairs:CMIN3-VALE3:a:OPEN_SHORT_SPREAD:")
        assert result["cl_b"].startswith("pairs:CMIN3-VALE3:b:OPEN_SHORT_SPREAD:")
        # 2 chamadas distintas
        assert len(captured["calls"]) == 2
        assert captured["calls"][0]["body"]["ticker"] == "CMIN3"
        assert captured["calls"][0]["body"]["order_side"] == "sell"
        assert captured["calls"][1]["body"]["ticker"] == "VALE3"
        assert captured["calls"][1]["body"]["order_side"] == "buy"

    async def test_leg_a_fails_no_naked(self) -> None:
        captured: dict[str, Any] = {}
        transport, captured = _mock_transport_router(
            fail_tickers={"CMIN3"},
            captured=captured,
        )
        original = httpx.AsyncClient

        def make_client(*args, **kwargs):
            kwargs["transport"] = transport
            return original(*args, **kwargs)

        with patch(
            "finanalytics_ai.workers.auto_trader_dispatcher.httpx.AsyncClient",
            side_effect=make_client,
        ):
            result = await dispatch_pair_order(
                base_url="http://api:8000",
                pair_key="CMIN3-VALE3",
                ticker_a="CMIN3",
                side_a="sell",
                quantity_a=100,
                ticker_b="VALE3",
                side_b="buy",
                quantity_b=20,
                action="OPEN_SHORT_SPREAD",
            )

        assert result["ok"] is False
        assert result["naked_leg"] is None  # nada enviado
        assert "leg_a_failed" in result["error"]
        # Apenas 1 chamada (leg A) — leg B nao foi tentada
        assert len(captured["calls"]) == 1

    async def test_leg_b_fails_naked_alert(self) -> None:
        """Caso CRITICO: leg A executou, leg B falhou -> naked leg risk."""
        captured: dict[str, Any] = {}
        transport, captured = _mock_transport_router(
            response_map={"CMIN3": {"local_order_id": 111}},
            fail_tickers={"VALE3"},
            captured=captured,
        )
        original = httpx.AsyncClient

        def make_client(*args, **kwargs):
            kwargs["transport"] = transport
            return original(*args, **kwargs)

        with patch(
            "finanalytics_ai.workers.auto_trader_dispatcher.httpx.AsyncClient",
            side_effect=make_client,
        ):
            result = await dispatch_pair_order(
                base_url="http://api:8000",
                pair_key="CMIN3-VALE3",
                ticker_a="CMIN3",
                side_a="sell",
                quantity_a=100,
                ticker_b="VALE3",
                side_b="buy",
                quantity_b=20,
                action="OPEN_SHORT_SPREAD",
            )

        assert result["ok"] is False
        assert result["naked_leg"] == "a"  # leg A executou, esta naked
        assert "leg_b_failed" in result["error"]
        assert result["leg_a"]["local_order_id"] == 111
        # 2 chamadas (ambas tentadas)
        assert len(captured["calls"]) == 2

    async def test_action_close_uses_close_in_cl_ord_id(self) -> None:
        captured: dict[str, Any] = {}
        transport, captured = _mock_transport_router(captured=captured)
        original = httpx.AsyncClient

        def make_client(*args, **kwargs):
            kwargs["transport"] = transport
            return original(*args, **kwargs)

        with patch(
            "finanalytics_ai.workers.auto_trader_dispatcher.httpx.AsyncClient",
            side_effect=make_client,
        ):
            result = await dispatch_pair_order(
                base_url="http://api:8000",
                pair_key="CMIN3-VALE3",
                ticker_a="CMIN3", side_a="buy", quantity_a=100,
                ticker_b="VALE3", side_b="sell", quantity_b=20,
                action="CLOSE",
                computed_at=_utc(),
            )

        assert result["ok"] is True
        assert ":CLOSE:" in result["cl_a"]
        assert ":CLOSE:" in result["cl_b"]
