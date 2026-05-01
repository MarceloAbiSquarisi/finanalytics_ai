"""Smoke test do C5 — supressao do hook diary para ordens do trading-engine.

Cenario: ordens enviadas via :8002/order/send com '_source': 'trading_engine'
sao persistidas em profit_orders.source='trading_engine' e NAO disparam
_maybe_dispatch_diary (que postaria em /diario/from_fill duplicando entry
no unified trade_journal — o engine mantem journal proprio).

Ver: docs/c5_finanalyticsai_implementation_patch.md no repo trading-engine.
"""
from __future__ import annotations

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

# profit_agent.py faz ctypes.WINFUNCTYPE/WinDLL no top-level (Windows-only).
# Em Linux/CI mockamos ctypes ANTES do import — _maybe_dispatch_diary e puro
# Python e nao toca DLL, entao o mocking e suficiente para os 2 testes abaixo.
if sys.platform != "win32":
    import ctypes

    if not hasattr(ctypes, "WinDLL"):
        ctypes.WinDLL = lambda path: MagicMock()  # type: ignore[attr-defined]
    if not hasattr(ctypes, "windll"):
        ctypes.windll = MagicMock()  # type: ignore[attr-defined]


@pytest.fixture
def fake_agent_with_db():
    """ProfitAgent com _db mockado retornando uma row de profit_orders."""
    from finanalytics_ai.workers.profit_agent import ProfitAgent

    agent = ProfitAgent.__new__(ProfitAgent)  # bypass __init__
    agent._diary_notified = set()
    agent._diary_user_id = "user-demo"
    agent._diary_url = "http://localhost:8000/api/v1/diario/from_fill"
    agent._tf_by_local_id = {}

    db = MagicMock()
    cursor = MagicMock()
    db._conn.cursor.return_value = cursor
    agent._db = db
    return agent, cursor


def test_dispatch_diary_runs_for_manual_origin(fake_agent_with_db):
    """source=NULL ('manual') -> hook dispara normalmente."""
    agent, cursor = fake_agent_with_db
    cursor.fetchone.return_value = ("WINFUT", 1, None)

    with patch("finanalytics_ai.workers.profit_agent.threading.Thread") as mock_thread:
        agent._maybe_dispatch_diary({
            "order_status": 2,
            "local_order_id": 12345,
            "avg_price": 130000.0,
            "traded_qty": 1,
        })

    mock_thread.assert_called_once()
    assert 12345 in agent._diary_notified


def test_dispatch_diary_suppressed_for_engine_origin(fake_agent_with_db, caplog):
    """source='trading_engine' -> hook NAO dispara, log de supressao emitido."""
    caplog.set_level(logging.INFO, logger="profit_agent")

    agent, cursor = fake_agent_with_db
    cursor.fetchone.return_value = ("WINFUT", 1, "trading_engine")

    with patch("finanalytics_ai.workers.profit_agent.threading.Thread") as mock_thread:
        agent._maybe_dispatch_diary({
            "order_status": 2,
            "local_order_id": 99999,
            "avg_price": 130000.0,
            "traded_qty": 1,
        })

    mock_thread.assert_not_called()
    assert 99999 in agent._diary_notified  # marcado, nao re-tentara
    assert any(
        "diary.suppressed_engine_origin" in r.getMessage()
        for r in caplog.records
    )
