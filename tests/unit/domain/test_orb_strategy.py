"""
Testes da ORBStrategy (R4 — scaffold).

Hoje strategy retorna SKIP fixo com reason indicando que implementacao real
foi defer. Tests garantem:
  - SKIP devolvido pra qualquer ticker
  - reason marca scaffold status (nao falha silencioso)
  - ticker u-cased no snapshot
  - registry no auto_trader_worker reconhece "orb_winfut_di1"

Quando R4 for ativado (logica OR/ATR/DI1), tests aqui passam a cobrir os
caminhos BUY/SELL/SKIP reais.
"""

from __future__ import annotations

from typing import Any

from finanalytics_ai.domain.robot.strategies import ORBStrategy


def test_evaluate_returns_skip_for_winfut() -> None:
    strategy = ORBStrategy()
    config: dict[str, Any] = {
        "ticker": "WINFUT",
        "or_window_minutes": 30,
        "atr_stop_mult": 1.5,
        "di1_filter": True,
    }
    result = strategy.evaluate("WINFUT", config)

    assert result["action"] == "SKIP"
    assert "orb_strategy_not_implemented" in result["payload"]["reason"]
    assert "R4 defer" in result["payload"]["reason"]


def test_evaluate_uppercases_ticker_in_snapshot() -> None:
    strategy = ORBStrategy()
    result = strategy.evaluate("winfut", {})
    assert result["payload"]["snapshot"]["ticker"] == "WINFUT"


def test_evaluate_passes_config_through_to_snapshot() -> None:
    strategy = ORBStrategy()
    config = {"or_window_minutes": 45, "di1_filter": False}
    result = strategy.evaluate("WINFUT", config)
    assert result["payload"]["snapshot"]["config"] == config


def test_evaluate_marks_scaffold_status() -> None:
    strategy = ORBStrategy()
    result = strategy.evaluate("WINFUT", {})
    assert result["payload"]["snapshot"]["scaffold_status"] == "ready_for_implementation"


def test_strategy_registered_in_auto_trader() -> None:
    """Garante que ORBStrategy esta no STRATEGY_REGISTRY com chave esperada."""
    from finanalytics_ai.workers.auto_trader_worker import STRATEGY_REGISTRY

    assert "orb_winfut_di1" in STRATEGY_REGISTRY
    impl = STRATEGY_REGISTRY["orb_winfut_di1"]
    assert isinstance(impl, ORBStrategy)
    assert impl.name == "orb_winfut_di1"
