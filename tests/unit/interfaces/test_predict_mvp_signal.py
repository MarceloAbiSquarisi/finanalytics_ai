"""Testes unitarios — derivacao de signal de predict_mvp."""

from __future__ import annotations

import pytest

from finanalytics_ai.interfaces.api.routes.predict_mvp import _signal_from_prediction


@pytest.mark.parametrize(
    "pred, cfg, expected",
    [
        # th_buy=0.3, th_sell=0, horizon=21 -> scaled: 0.01428, 0.0
        (0.02, {"th_buy": 0.3, "th_sell": 0.0, "horizon_days": 21}, "BUY"),
        (0.01, {"th_buy": 0.3, "th_sell": 0.0, "horizon_days": 21}, "HOLD"),
        (-0.001, {"th_buy": 0.3, "th_sell": 0.0, "horizon_days": 21}, "SELL"),
        # th_buy=0 (exatamente na borda): BUY (inclusivo)
        (0.0, {"th_buy": 0.0, "th_sell": -0.1, "horizon_days": 21}, "BUY"),
        (-0.0048, {"th_buy": 0.0, "th_sell": -0.1, "horizon_days": 21}, "SELL"),
        # horizon=1 sem escala
        (0.5, {"th_buy": 0.3, "th_sell": -0.3, "horizon_days": 1}, "BUY"),
        (-0.5, {"th_buy": 0.3, "th_sell": -0.3, "horizon_days": 1}, "SELL"),
    ],
)
def test_signal_boundary_conditions(pred: float, cfg: dict, expected: str):
    sig, method = _signal_from_prediction(pred, cfg)
    assert sig == expected
    assert method == "scaled_linear_1d"


def test_signal_method_is_constant():
    sig, method = _signal_from_prediction(0.0, {"th_buy": 0.1, "th_sell": -0.1, "horizon_days": 5})
    assert method == "scaled_linear_1d"


def test_horizon_zero_guard():
    # horizon=0 nao deve dividir por zero; aceita como se fosse 1
    sig, _ = _signal_from_prediction(0.5, {"th_buy": 0.3, "th_sell": -0.3, "horizon_days": 0})
    assert sig == "BUY"


# ─── /signals batch ────────────────────────────────────────────────────────


def test_signals_response_aggregates_correctly():
    from finanalytics_ai.interfaces.api.routes.predict_mvp import SignalItem, SignalsResponse

    items = [
        SignalItem(ticker="A", signal="BUY", th_buy=0.0, th_sell=-0.1, horizon_days=21),
        SignalItem(ticker="B", signal="SELL", th_buy=0.0, th_sell=-0.1, horizon_days=21),
        SignalItem(ticker="C", signal="HOLD", th_buy=0.0, th_sell=-0.1, horizon_days=21),
        SignalItem(ticker="D", signal=None, error="no_model"),
    ]
    resp = SignalsResponse(count=len(items), buy=1, sell=1, hold=1, errors=1, items=items)
    assert resp.count == 4
    assert resp.buy + resp.sell + resp.hold + resp.errors == resp.count
