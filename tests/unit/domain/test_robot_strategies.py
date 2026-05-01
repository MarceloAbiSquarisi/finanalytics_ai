"""
Testes da TsmomMlOverlayStrategy (R2).

Cobertura: filtro de momentum 252d sobre sinal ML.

  - BUY ML + momentum positivo  -> trada (BUY com sizing)
  - BUY ML + momentum negativo  -> SKIP (tsmom_disagree)
  - SELL ML + momentum negativo -> trada (SELL com sizing)
  - SELL ML + momentum positivo -> SKIP (tsmom_disagree)
  - momentum sign zero (raro)   -> SKIP (neutro nao concorda)
  - bars insuficientes (<253)   -> SKIP (insufficient_bars_for_momentum)
  - ML signal HOLD/missing      -> HOLD (passa direto)
  - close zerado no lookback    -> SKIP (zero_close)

Mocks: substitui _fetch_signal e _fetch_bars via monkeypatch — strategy nao
toca rede. Foco e' a logica de overlay, nao o transport (testado separado
em test_auto_trader_dispatcher).
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from finanalytics_ai.domain.robot.strategies import TsmomMlOverlayStrategy


def _make_bars(closes: list[float]) -> list[dict[str, Any]]:
    """Bars sinteticas: high=close*1.01, low=close*0.99, volume=1000."""
    return [
        {
            "close": c,
            "high": c * 1.01,
            "low": c * 0.99,
            "open": c,
            "volume": 1000,
        }
        for c in closes
    ]


def _trending_up(start: float = 30.0, days: int = 260, daily_ret: float = 0.001) -> list[float]:
    """Closes trending up + ruido senoidal deterministico — momentum > 0 com vol > 0."""
    closes = [start]
    for i in range(days):
        noise = 0.005 * math.sin(i * 0.73)  # +-0.5% determinista
        closes.append(closes[-1] * (1.0 + daily_ret + noise))
    return closes


def _trending_down(start: float = 30.0, days: int = 260, daily_ret: float = -0.001) -> list[float]:
    closes = [start]
    for i in range(days):
        noise = 0.005 * math.sin(i * 0.73)
        closes.append(closes[-1] * (1.0 + daily_ret + noise))
    return closes


def _flat(start: float = 30.0, days: int = 260) -> list[float]:
    """Closes constantes — momentum exatamente zero."""
    return [start] * (days + 1)


@pytest.fixture
def strategy(monkeypatch) -> TsmomMlOverlayStrategy:
    s = TsmomMlOverlayStrategy(base_url="http://stub")
    return s


def _stub_signal(strategy: TsmomMlOverlayStrategy, signal: str | None, **extra) -> None:
    item = {"ticker": "PETR4", "signal": signal, **extra} if signal else None
    strategy._fetch_signal = lambda ticker: item  # type: ignore[method-assign]


def _stub_bars(strategy: TsmomMlOverlayStrategy, closes: list[float]) -> None:
    bars = _make_bars(closes)
    strategy._fetch_bars = lambda ticker, n, range_period="3mo": bars[-n:]  # type: ignore[method-assign]


def _ctx() -> dict[str, Any]:
    return {
        "capital_per_strategy": 50_000,
        "target_vol_annual": 0.15,
        "kelly_fraction": 0.25,
        "max_position_pct": 0.10,
        "atr_period": 14,
        "atr_sl_mult": 2.0,
        "atr_tp_mult": 3.0,
        "vol_lookback_days": 20,
        "momentum_lookback_days": 252,
    }


# ── Concordance ───────────────────────────────────────────────────────────────


class TestConcordance:
    def test_buy_with_positive_momentum_passes(self, strategy: TsmomMlOverlayStrategy) -> None:
        _stub_signal(strategy, "BUY", predicted_return_pct=0.05)
        _stub_bars(strategy, _trending_up())

        result = strategy.evaluate("PETR4", _ctx())

        assert result["action"] == "BUY"
        snap = result["payload"]["snapshot"]
        assert snap["momentum_252d_ret"] > 0
        assert snap["momentum_sign"] == 1
        assert "concordant" in result["payload"]["reason"]
        # sizing populado
        assert result["payload"]["quantity"] > 0
        assert result["payload"]["take_profit"] is not None
        assert result["payload"]["stop_loss"] is not None

    def test_sell_with_negative_momentum_passes(self, strategy: TsmomMlOverlayStrategy) -> None:
        _stub_signal(strategy, "SELL", predicted_return_pct=-0.05)
        _stub_bars(strategy, _trending_down())

        result = strategy.evaluate("PETR4", _ctx())

        assert result["action"] == "SELL"
        snap = result["payload"]["snapshot"]
        assert snap["momentum_252d_ret"] < 0
        assert snap["momentum_sign"] == -1

    def test_buy_with_negative_momentum_skips(self, strategy: TsmomMlOverlayStrategy) -> None:
        _stub_signal(strategy, "BUY")
        _stub_bars(strategy, _trending_down())

        result = strategy.evaluate("PETR4", _ctx())

        assert result["action"] == "SKIP"
        assert "tsmom_disagree" in result["payload"]["reason"]
        assert "ml=BUY" in result["payload"]["reason"]

    def test_sell_with_positive_momentum_skips(self, strategy: TsmomMlOverlayStrategy) -> None:
        _stub_signal(strategy, "SELL")
        _stub_bars(strategy, _trending_up())

        result = strategy.evaluate("PETR4", _ctx())

        assert result["action"] == "SKIP"
        assert "tsmom_disagree" in result["payload"]["reason"]
        assert "ml=SELL" in result["payload"]["reason"]

    def test_neutral_momentum_skips(self, strategy: TsmomMlOverlayStrategy) -> None:
        # Momentum exatamente 0 -> nao concorda nem com BUY nem com SELL
        _stub_signal(strategy, "BUY")
        _stub_bars(strategy, _flat())

        result = strategy.evaluate("PETR4", _ctx())

        assert result["action"] == "SKIP"
        assert "tsmom_disagree" in result["payload"]["reason"]


# ── Pass-through HOLD ─────────────────────────────────────────────────────────


class TestPassthrough:
    def test_ml_hold_returns_hold_without_bars_fetch(
        self, strategy: TsmomMlOverlayStrategy
    ) -> None:
        _stub_signal(strategy, "HOLD")
        # bars stub que falha se chamado
        called: dict[str, bool] = {"bars": False}

        def boom(*a, **k):
            called["bars"] = True
            return None

        strategy._fetch_bars = boom  # type: ignore[method-assign]

        result = strategy.evaluate("PETR4", _ctx())
        assert result["action"] == "HOLD"
        assert called["bars"] is False  # short-circuit antes de fetch

    def test_missing_signal_returns_skip(self, strategy: TsmomMlOverlayStrategy) -> None:
        _stub_signal(strategy, None)

        result = strategy.evaluate("PETR4", _ctx())
        assert result["action"] == "SKIP"
        assert "no_signal" in result["payload"]["reason"]


# ── Bars insuficientes / corrompidos ──────────────────────────────────────────


class TestBarsValidation:
    def test_too_few_bars_skips(self, strategy: TsmomMlOverlayStrategy) -> None:
        _stub_signal(strategy, "BUY")
        # 100 bars < 253 necessarios
        _stub_bars(strategy, _trending_up(days=99))

        result = strategy.evaluate("PETR4", _ctx())
        assert result["action"] == "SKIP"
        assert "insufficient_bars" in result["payload"]["reason"]

    def test_zero_close_in_lookback(self, strategy: TsmomMlOverlayStrategy) -> None:
        _stub_signal(strategy, "BUY")
        closes = _trending_up()
        # Zera close no ponto exato do lookback (-253)
        closes[-253] = 0.0
        _stub_bars(strategy, closes)

        result = strategy.evaluate("PETR4", _ctx())
        assert result["action"] == "SKIP"
        assert "zero_close" in result["payload"]["reason"]


# ── Custom lookback ───────────────────────────────────────────────────────────


class TestCustomLookback:
    def test_60d_lookback_works(self, strategy: TsmomMlOverlayStrategy) -> None:
        """Lookback configuravel — ex: 60d em vez de 252."""
        _stub_signal(strategy, "BUY")
        _stub_bars(strategy, _trending_up(days=70))

        ctx = _ctx()
        ctx["momentum_lookback_days"] = 60

        result = strategy.evaluate("PETR4", ctx)
        assert result["action"] == "BUY"
        assert result["payload"]["snapshot"]["momentum_lookback_days"] == 60
