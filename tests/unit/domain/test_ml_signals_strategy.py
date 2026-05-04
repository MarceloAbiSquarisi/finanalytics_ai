"""
Testes da MLSignalsStrategy — foco nos campos NOVOS (04/mai):
  - `lot_size` do context é respeitado (default=100, override via config)
  - `is_daytrade` propaga do context para o payload final

Bugs do dia 04/mai que esses testes fecham (regression-prevention):
  - Strategy retornava qty=20 ignorando lote 100 da B3 (broker rejeitava
    silenciosamente "Risco Simulador: Quantidade da ordem deve ser
    multiplo do lote").
  - is_daytrade=true do robot_strategies.config_json foi a UI inicial mas
    a infra precisava propagar até o body do POST /order/send.

Mocks: stub _fetch_signal e _fetch_bars; foco e' o branch de sizing +
payload, nao a integracao HTTP (testada em test_auto_trader_dispatcher).
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from finanalytics_ai.domain.robot.strategies import MLSignalsStrategy


def _make_bars(closes: list[float]) -> list[dict[str, Any]]:
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


def _vol_window(start: float = 50.0, days: int = 60, daily_ret: float = 0.001) -> list[float]:
    """Bars com vol annual ~30% — capital=50k + max_pct=10% + price=50 produz
    qty cap=100 e lot=100 → exatamente 100 (alvo do teste lot_size).
    Maior amplitude de noise (+/-2%) garante realized_vol > 0."""
    closes = [start]
    for i in range(days):
        # Noise senoidal +-2% determinista para realized vol ~30% annual
        noise = 0.02 * math.sin(i * 0.73) + 0.01 * math.cos(i * 1.31)
        closes.append(max(0.01, closes[-1] * (1.0 + daily_ret + noise)))
    return closes


@pytest.fixture
def strategy() -> MLSignalsStrategy:
    return MLSignalsStrategy(base_url="http://stub")


def _stub_buy(strategy: MLSignalsStrategy) -> None:
    item = {"ticker": "PETR4", "signal": "BUY", "predicted_return_pct": 0.05}
    strategy._fetch_signal = lambda ticker: item  # type: ignore[method-assign]
    bars = _make_bars(_vol_window())
    strategy._fetch_bars = lambda ticker, n, range_period="3mo": bars[-n:]  # type: ignore[method-assign]


def _ctx(**overrides) -> dict[str, Any]:
    # Capital alto + max_pct generoso garante que o cap nao bloqueia
    # qty=100 (lote default). Seed real (50k+10%) e' marginal pra
    # PETR4 ~R$50, dependendo de vol exata. Test isola sizing-vs-lot.
    base = {
        "capital_per_strategy": 200_000,
        "target_vol_annual": 0.15,
        "kelly_fraction": 0.25,
        "max_position_pct": 0.30,
        "atr_period": 14,
        "atr_sl_mult": 2.0,
        "atr_tp_mult": 3.0,
        "vol_lookback_days": 20,
    }
    base.update(overrides)
    return base


# ── lot_size (bug raiz smoke 04/mai) ──────────────────────────────────────────


class TestLotSize:
    def test_default_lot_size_is_100(self, strategy: MLSignalsStrategy) -> None:
        """Sem `lot_size` no context, default e' 100 (B3 stocks)."""
        _stub_buy(strategy)
        result = strategy.evaluate("PETR4", _ctx())
        assert result["action"] == "BUY"
        qty = result["payload"]["quantity"]
        assert qty > 0, "sizing produced zero qty — vol/capital mismatch?"
        assert qty % 100 == 0, f"qty={qty} nao e' multiplo de 100 (lot default)"

    def test_lot_size_override_via_context(self, strategy: MLSignalsStrategy) -> None:
        """`lot_size: 200` no context arredonda qty para multiplo de 200."""
        _stub_buy(strategy)
        result = strategy.evaluate("PETR4", _ctx(lot_size=200))
        assert result["action"] == "BUY"
        qty = result["payload"]["quantity"]
        assert qty % 200 == 0, f"qty={qty} nao e' multiplo de lot_size=200"

    def test_lot_size_one_for_futures(self, strategy: MLSignalsStrategy) -> None:
        """Futuros (WINFUT/WDOFUT) tipicamente unitarios (lot_size=1)."""
        _stub_buy(strategy)
        # capital=10k pra produzir qty pequena (~1-3 lots de 1)
        result = strategy.evaluate(
            "WINFUT", _ctx(capital_per_strategy=10_000, lot_size=1)
        )
        assert result["action"] == "BUY"
        qty = result["payload"]["quantity"]
        # Com lot_size=1, qty pode ser qualquer inteiro >= 1.
        # Se fosse lot=100 com capital baixo, qty seria 0 (blocked).
        assert qty >= 1, f"qty={qty} bloqueada incorretamente"


# ── is_daytrade passthrough ───────────────────────────────────────────────────


class TestIsDaytrade:
    def test_default_is_daytrade_true(self, strategy: MLSignalsStrategy) -> None:
        """Default `is_daytrade=True` se ausente no context."""
        _stub_buy(strategy)
        result = strategy.evaluate("PETR4", _ctx())
        assert result["payload"]["is_daytrade"] is True

    def test_is_daytrade_false_propagates(self, strategy: MLSignalsStrategy) -> None:
        """`is_daytrade: false` no context propaga para o payload."""
        _stub_buy(strategy)
        result = strategy.evaluate("PETR4", _ctx(is_daytrade=False))
        assert result["payload"]["is_daytrade"] is False

    def test_is_daytrade_true_explicit(self, strategy: MLSignalsStrategy) -> None:
        _stub_buy(strategy)
        result = strategy.evaluate("PETR4", _ctx(is_daytrade=True))
        assert result["payload"]["is_daytrade"] is True


# ── Sanidade do payload completo (smoke) ──────────────────────────────────────


class TestPayloadShape:
    def test_buy_payload_has_required_keys(self, strategy: MLSignalsStrategy) -> None:
        _stub_buy(strategy)
        result = strategy.evaluate("PETR4", _ctx())
        payload = result["payload"]
        # Campos exigidos pelo dispatcher (auto_trader_dispatcher.send_order):
        for key in ("quantity", "price", "order_type", "is_daytrade"):
            assert key in payload, f"payload faltando '{key}'"
        # snapshot tem contexto pra audit
        snap = payload["snapshot"]
        for key in ("ticker", "ml_signal", "last_close", "atr", "qty", "tp", "sl"):
            assert key in snap, f"snapshot faltando '{key}'"

    def test_market_order_has_price_none(self, strategy: MLSignalsStrategy) -> None:
        """Market order: price=None, dispatcher converte pra -1 no body."""
        _stub_buy(strategy)
        result = strategy.evaluate("PETR4", _ctx())
        assert result["payload"]["price"] is None
        assert result["payload"]["order_type"] == "market"
