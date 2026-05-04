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
    update_intent_sent,
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

    def test_hash_fallback_is_deterministic(self) -> None:
        """Mesmo input longo deve produzir mesmo hash (idempotencia).

        Sentinel: se hashlib mudar ou implementacao mudar pra incluir
        timestamp/UUID, idempotencia quebra e o proxy passa a ver
        pares duplicados em vez do mesmo cl_ord_id reutilizado.
        """
        long_ticker = "X" * 80
        a = make_cl_ord_id(
            strategy_id=999, ticker=long_ticker, action="BUY", computed_at=_utc()
        )
        b = make_cl_ord_id(
            strategy_id=999, ticker=long_ticker, action="BUY", computed_at=_utc()
        )
        assert a == b

    def test_different_strategy_id_different_id(self) -> None:
        """strategy_id e' parte da chave — ml_signals (id=2) vs
        tsmom_ml_overlay (id=3) NUNCA podem colidir."""
        a = make_cl_ord_id(strategy_id=2, ticker="PETR4", action="BUY", computed_at=_utc())
        b = make_cl_ord_id(strategy_id=3, ticker="PETR4", action="BUY", computed_at=_utc())
        assert a != b

    def test_seconds_dropped_from_minute(self) -> None:
        """Ordens disparadas em segundos diferentes do MESMO minuto compartilham
        cl_ord_id (idempotencia explicita do C5 handshake)."""
        a = make_cl_ord_id(
            strategy_id=1,
            ticker="PETR4",
            action="BUY",
            computed_at=datetime(2026, 5, 1, 12, 35, 0, tzinfo=UTC),
        )
        b = make_cl_ord_id(
            strategy_id=1,
            ticker="PETR4",
            action="BUY",
            computed_at=datetime(2026, 5, 1, 12, 35, 59, tzinfo=UTC),
        )
        assert a == b, "Segundos no mesmo minuto deveriam produzir mesmo cl_ord_id"

    def test_microseconds_dropped(self) -> None:
        """Microsegundos sao descartados pelo replace(second=0, microsecond=0)."""
        a = make_cl_ord_id(
            strategy_id=1,
            ticker="PETR4",
            action="BUY",
            computed_at=datetime(2026, 5, 1, 12, 35, 0, 123456, tzinfo=UTC),
        )
        b = make_cl_ord_id(
            strategy_id=1,
            ticker="PETR4",
            action="BUY",
            computed_at=datetime(2026, 5, 1, 12, 35, 0, 999999, tzinfo=UTC),
        )
        assert a == b


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
            if "send" in url:
                return {"ok": True, "local_order_id": 5555}
            return {"oco_id": "xyz"}

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
                captured, responder=lambda u, b: {"ok": True, "local_order_id": 1}
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


# ── P0 #2 (smoke 04/mai 16:47): persistencia de local_order_id ────────────────


@pytest.mark.asyncio
class TestLocalOrderIdPersistence:
    """Bug raiz: agent retorna HTTP 200 mas body com ok=False (rejeicao
    logica), ou body com ok=True mas sem local_order_id. Antes do fix,
    dispatcher tratava como sucesso e setava intent.local_order_id=NULL
    silenciosamente. Agora propaga erro explicito para error_msg."""

    @patch("finanalytics_ai.workers.auto_trader_dispatcher.update_signal_log_sent")
    @patch("finanalytics_ai.workers.auto_trader_dispatcher.update_intent_sent")
    @patch("finanalytics_ai.workers.auto_trader_dispatcher.insert_intent")
    async def test_agent_ok_false_treated_as_send_failure(
        self, m_insert, m_update_intent, m_update_signal
    ) -> None:
        m_insert.return_value = 11
        original = httpx.AsyncClient

        def responder(url, body):
            # Cenario do bug: agent rejeita logicamente (qty fora do lote, etc)
            return {"ok": False, "error": "qty=20 nao e' multiplo do lote=100"}

        captured: dict[str, Any] = {}

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
                signal_log_id=11,
                strategy_id=1,
                ticker="PETR4",
                side="buy",
                quantity=20,
            )

        assert result["ok"] is False
        # Erro do agent deve aparecer em result["error"] e em error_msg do intent
        assert "agent_send_rejected" in result["error"]
        assert "lote=100" in result["error"]
        kwargs = m_update_intent.call_args.kwargs
        assert kwargs["local_order_id"] is None
        assert "agent_send_rejected" in kwargs["error_msg"]
        m_update_signal.assert_called_once_with(
            dsn="postgres://stub", signal_log_id=11, local_order_id=None, sent=False
        )

    @patch("finanalytics_ai.workers.auto_trader_dispatcher.update_signal_log_sent")
    @patch("finanalytics_ai.workers.auto_trader_dispatcher.update_intent_sent")
    @patch("finanalytics_ai.workers.auto_trader_dispatcher.insert_intent")
    async def test_agent_ok_true_without_local_order_id_marks_error(
        self, m_insert, m_update_intent, m_update_signal
    ) -> None:
        # Bug defensivo: agent diz ok=True mas esquece local_order_id.
        # Antes setava NULL silencioso; agora marca error_msg explicito.
        m_insert.return_value = 12
        original = httpx.AsyncClient
        captured: dict[str, Any] = {}

        def make_client(*args, **kwargs):
            kwargs["transport"] = _mock_transport(
                captured, responder=lambda u, b: {"ok": True}  # sem local_order_id
            )
            return original(*args, **kwargs)

        with patch(
            "finanalytics_ai.workers.auto_trader_dispatcher.httpx.AsyncClient",
            side_effect=make_client,
        ):
            result = await dispatch_order(
                dsn="postgres://stub",
                base_url="http://api:8000",
                signal_log_id=12,
                strategy_id=1,
                ticker="PETR4",
                side="buy",
                quantity=100,
            )

        assert result["ok"] is False
        assert result["error"] == "send_response_missing_local_order_id"
        kwargs = m_update_intent.call_args.kwargs
        assert kwargs["local_order_id"] is None
        assert kwargs["error_msg"] == "send_response_missing_local_order_id"
        m_update_signal.assert_called_once_with(
            dsn="postgres://stub", signal_log_id=12, local_order_id=None, sent=False
        )

    @patch("finanalytics_ai.workers.auto_trader_dispatcher.update_signal_log_sent")
    @patch("finanalytics_ai.workers.auto_trader_dispatcher.update_intent_sent")
    @patch("finanalytics_ai.workers.auto_trader_dispatcher.insert_intent")
    async def test_local_order_id_zero_not_treated_as_falsy(
        self, m_insert, m_update_intent, m_update_signal
    ) -> None:
        # Bug latente: codigo antigo `local_order_id or local_id` perdia
        # o valor 0 (Python falsy). Agora usa `is None` check explicito.
        m_insert.return_value = 13
        original = httpx.AsyncClient
        captured: dict[str, Any] = {}

        def make_client(*args, **kwargs):
            kwargs["transport"] = _mock_transport(
                captured, responder=lambda u, b: {"ok": True, "local_order_id": 0}
            )
            return original(*args, **kwargs)

        with patch(
            "finanalytics_ai.workers.auto_trader_dispatcher.httpx.AsyncClient",
            side_effect=make_client,
        ):
            result = await dispatch_order(
                dsn="postgres://stub",
                base_url="http://api:8000",
                signal_log_id=13,
                strategy_id=1,
                ticker="PETR4",
                side="buy",
                quantity=100,
            )

        # local_order_id=0 e' valido — deve persistir, nao virar None
        assert result["ok"] is True
        assert result["local_order_id"] == 0
        kwargs = m_update_intent.call_args.kwargs
        assert kwargs["local_order_id"] == 0
        assert kwargs["error_msg"] is None


# ── update_intent_sent: retorna bool indicando sucesso ────────────────────────


class TestUpdateIntentSent:
    """update_intent_sent agora retorna bool. False = warning para caller
    detectar intent_id inexistente ou DB drop."""

    def test_returns_true_when_row_updated(self) -> None:
        from unittest.mock import MagicMock

        mock_cur = MagicMock()
        mock_cur.rowcount = 1
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        with patch(
            "finanalytics_ai.workers.auto_trader_dispatcher._get_conn",
            return_value=mock_conn,
        ):
            mock_conn.__enter__.return_value = mock_conn
            ok = update_intent_sent(
                dsn="stub", intent_id=999, local_order_id=12345, error_msg=None
            )

        assert ok is True

    def test_returns_false_when_no_rows_updated(self) -> None:
        from unittest.mock import MagicMock

        mock_cur = MagicMock()
        mock_cur.rowcount = 0  # intent_id inexistente
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        with patch(
            "finanalytics_ai.workers.auto_trader_dispatcher._get_conn",
            return_value=mock_conn,
        ):
            mock_conn.__enter__.return_value = mock_conn
            ok = update_intent_sent(
                dsn="stub", intent_id=99999, local_order_id=12345, error_msg=None
            )

        assert ok is False

    def test_returns_false_on_db_exception(self) -> None:
        with patch(
            "finanalytics_ai.workers.auto_trader_dispatcher._get_conn",
            side_effect=ConnectionError("DB drop"),
        ):
            ok = update_intent_sent(
                dsn="stub", intent_id=1, local_order_id=12345, error_msg=None
            )

        assert ok is False


# ── P0 #3 (smoke 04/mai): OCO bilateral (BUY+SELL) ────────────────────────────


@pytest.mark.asyncio
class TestOcoBilateral:
    """Bug raiz: dispatcher so anexava OCO quando entry side=='buy'.
    SELL (short entry) ficava nu; ml_signals SELL = exposure ilimitada
    se preco subir. Fix: anexar OCO para ambos os sides com lado inverso."""

    @patch("finanalytics_ai.workers.auto_trader_dispatcher.update_signal_log_sent")
    @patch("finanalytics_ai.workers.auto_trader_dispatcher.update_intent_sent")
    @patch("finanalytics_ai.workers.auto_trader_dispatcher.insert_intent")
    async def test_sell_entry_attaches_buy_oco(
        self, m_insert, m_update_intent, m_update_signal
    ) -> None:
        m_insert.return_value = 21
        original = httpx.AsyncClient
        captured: dict[str, Any] = {}

        def responder(url, body):
            return {"ok": True, "local_order_id": 6001} if "send" in url else {"oco_id": "z1"}

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
                signal_log_id=21,
                strategy_id=1,
                ticker="PETR4",
                side="sell",
                quantity=100,
                # ATR levels para SELL: TP abaixo entry, SL acima entry
                take_profit=27.0,
                stop_loss=33.0,
                computed_at=_utc(),
            )

        assert result["ok"] is True
        # Deve ter chamado /order/send + /order/oco
        urls = [c["url"] for c in captured["calls"]]
        assert any("/order/send" in u for u in urls)
        assert any("/order/oco" in u for u in urls)
        # OCO deve ser BUY (lado inverso do SELL)
        oco_call = next(c for c in captured["calls"] if "/order/oco" in c["url"])
        assert oco_call["body"]["order_side"] == "buy"
        assert oco_call["body"]["take_profit"] == 27.0
        assert oco_call["body"]["stop_loss"] == 33.0
        # stop_limit em BUY OCO = stop_loss * 1.01 (compra acima do trigger)
        assert oco_call["body"]["stop_limit"] == pytest.approx(33.0 * 1.01)

    @patch("finanalytics_ai.workers.auto_trader_dispatcher.update_signal_log_sent")
    @patch("finanalytics_ai.workers.auto_trader_dispatcher.update_intent_sent")
    @patch("finanalytics_ai.workers.auto_trader_dispatcher.insert_intent")
    async def test_oco_skipped_when_only_tp_no_sl(
        self, m_insert, m_update_intent, m_update_signal
    ) -> None:
        # Estrategia retornou tp mas nao sl (ATR=0 caso degenerado).
        # Sem SL, OCO nao tem como proteger — caller decide nao criar.
        m_insert.return_value = 22
        original = httpx.AsyncClient
        captured: dict[str, Any] = {}

        def make_client(*args, **kwargs):
            kwargs["transport"] = _mock_transport(
                captured, responder=lambda u, b: {"ok": True, "local_order_id": 6002}
            )
            return original(*args, **kwargs)

        with patch(
            "finanalytics_ai.workers.auto_trader_dispatcher.httpx.AsyncClient",
            side_effect=make_client,
        ):
            await dispatch_order(
                dsn="postgres://stub",
                base_url="http://api:8000",
                signal_log_id=22,
                strategy_id=1,
                ticker="PETR4",
                side="sell",
                quantity=100,
                take_profit=27.0,
                stop_loss=None,
            )

        urls = [c["url"] for c in captured["calls"]]
        assert any("/order/send" in u for u in urls)
        assert not any("/order/oco" in u for u in urls)

    @patch("finanalytics_ai.workers.auto_trader_dispatcher.update_signal_log_sent")
    @patch("finanalytics_ai.workers.auto_trader_dispatcher.update_intent_sent")
    @patch("finanalytics_ai.workers.auto_trader_dispatcher.insert_intent")
    async def test_oco_failure_does_not_zero_entry(
        self, m_insert, m_update_intent, m_update_signal
    ) -> None:
        # Bug regress: OCO falhar NAO deve voltar ok=False — entry ja
        # executou. Caller deve receber alert mas worker continua.
        m_insert.return_value = 23
        original = httpx.AsyncClient

        def responder(url, body):
            if "/order/send" in url:
                return {"ok": True, "local_order_id": 6003}
            # OCO endpoint fails
            raise httpx.ConnectError("oco endpoint down", request=None)

        captured: dict[str, Any] = {}

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
                signal_log_id=23,
                strategy_id=1,
                ticker="PETR4",
                side="sell",
                quantity=100,
                take_profit=27.0,
                stop_loss=33.0,
            )

        # Entry sucesso, OCO falhou — result.ok=True (entry e' o que importa)
        assert result["ok"] is True
        assert result["local_order_id"] == 6003
