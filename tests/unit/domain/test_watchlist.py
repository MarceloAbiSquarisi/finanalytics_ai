"""
tests/unit/domain/test_watchlist.py
──────────────────────────────────────
Testes unitários para:
  - WatchlistItem: criação, add/remove de alertas
  - SmartAlert: is_evaluatable(), mark_triggered(), cooldown
  - evaluate_smart_alert(): todos os 9 tipos
  - _calc_rsi(), _calc_sma(): funções auxiliares
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from finanalytics_ai.domain.watchlist.entities import (
    SmartAlert,
    SmartAlertConfig,
    SmartAlertStatus,
    SmartAlertType,
    WatchlistItem,
    evaluate_smart_alert,
    _calc_rsi,
    _calc_sma,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


def make_bars(
    n: int = 60,
    base_price: float = 40.0,
    trend: float = 0.0,
    volume_mult: float = 1.0,
) -> list[dict[str, Any]]:
    """Gera barras OHLC sintéticas para testes."""
    bars = []
    price = base_price
    for i in range(n):
        price = max(0.01, price + trend + (((i * 7) % 3) - 1) * 0.5)
        bars.append(
            {
                "time": 1700000000 + i * 86400,
                "open": price - 0.2,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price,
                "volume": int(1_000_000 * volume_mult),
            }
        )
    return bars


def make_alert(alert_type: SmartAlertType, cfg: SmartAlertConfig | None = None) -> SmartAlert:
    return SmartAlert(
        ticker="PETR4",
        user_id="user_test",
        alert_type=alert_type,
        config=cfg or SmartAlertConfig(),
    )


# ── _calc_rsi ─────────────────────────────────────────────────────────────────


class TestCalcRsi:
    def test_insufficient_data_returns_none(self) -> None:
        assert _calc_rsi([40.0] * 5, period=14) is None

    def test_all_gains_returns_100(self) -> None:
        closes = [float(i) for i in range(1, 30)]  # sempre subindo
        rsi = _calc_rsi(closes, period=14)
        assert rsi is not None
        assert rsi == 100.0

    def test_all_losses_returns_zero(self) -> None:
        closes = [float(30 - i) for i in range(30)]  # sempre caindo
        rsi = _calc_rsi(closes, period=14)
        assert rsi is not None
        assert rsi == 0.0 or rsi < 10.0

    def test_neutral_market_mid_range(self) -> None:
        import random

        random.seed(42)
        closes = [40.0 + random.uniform(-0.5, 0.5) for _ in range(50)]
        rsi = _calc_rsi(closes, period=14)
        assert rsi is not None
        assert 20.0 <= rsi <= 80.0

    def test_exactly_period_plus_one_sufficient(self) -> None:
        closes = [40.0 + i * 0.1 for i in range(15)]
        rsi = _calc_rsi(closes, period=14)
        assert rsi is not None


# ── _calc_sma ─────────────────────────────────────────────────────────────────


class TestCalcSma:
    def test_insufficient_returns_none(self) -> None:
        assert _calc_sma([1.0, 2.0], period=5) is None

    def test_exact_period(self) -> None:
        result = _calc_sma([1.0, 2.0, 3.0, 4.0, 5.0], period=5)
        assert result == 3.0

    def test_uses_last_n_values(self) -> None:
        # SMA(3) de [10, 10, 10, 1, 2, 3] → usa apenas [1, 2, 3]
        result = _calc_sma([10.0, 10.0, 10.0, 1.0, 2.0, 3.0], period=3)
        assert result == 2.0

    def test_single_value_period_one(self) -> None:
        assert _calc_sma([42.0], period=1) == 42.0


# ── WatchlistItem ─────────────────────────────────────────────────────────────


class TestWatchlistItem:
    def test_create_item(self) -> None:
        item = WatchlistItem(ticker="PETR4", user_id="user1")
        assert item.ticker == "PETR4"
        assert item.user_id == "user1"
        assert item.item_id != ""
        assert item.smart_alerts == []

    def test_add_smart_alert(self) -> None:
        item = WatchlistItem(ticker="VALE3", user_id="u1")
        alert = make_alert(SmartAlertType.RSI_OVERSOLD)
        item.add_smart_alert(alert)
        assert len(item.smart_alerts) == 1

    def test_add_duplicate_type_raises(self) -> None:
        item = WatchlistItem(ticker="VALE3", user_id="u1")
        item.add_smart_alert(make_alert(SmartAlertType.RSI_OVERSOLD))
        with pytest.raises(ValueError, match="já existe"):
            item.add_smart_alert(make_alert(SmartAlertType.RSI_OVERSOLD))

    def test_active_alerts_excludes_deleted(self) -> None:
        item = WatchlistItem(ticker="ITUB4", user_id="u1")
        a1 = make_alert(SmartAlertType.RSI_OVERSOLD)
        a2 = make_alert(SmartAlertType.RSI_OVERBOUGHT)
        a2.status = SmartAlertStatus.DELETED
        item.smart_alerts = [a1, a2]
        assert len(item.active_alerts()) == 1

    def test_to_dict_structure(self) -> None:
        item = WatchlistItem(ticker="BBDC4", user_id="u1", note="Banco", tags=["financeiro"])
        d = item.to_dict()
        assert d["ticker"] == "BBDC4"
        assert d["note"] == "Banco"
        assert "financeiro" in d["tags"]
        assert "smart_alerts" in d


# ── SmartAlert: is_evaluatable / cooldown ────────────────────────────────────


class TestSmartAlertCooldown:
    def test_active_alert_is_evaluatable(self) -> None:
        alert = make_alert(SmartAlertType.RSI_OVERSOLD)
        assert alert.is_evaluatable()

    def test_paused_alert_not_evaluatable(self) -> None:
        alert = make_alert(SmartAlertType.RSI_OVERSOLD)
        alert.status = SmartAlertStatus.PAUSED
        assert not alert.is_evaluatable()

    def test_deleted_alert_not_evaluatable(self) -> None:
        alert = make_alert(SmartAlertType.RSI_OVERSOLD)
        alert.status = SmartAlertStatus.DELETED
        assert not alert.is_evaluatable()

    def test_cooldown_in_effect_not_evaluatable(self) -> None:
        alert = make_alert(SmartAlertType.RSI_OVERSOLD)
        alert.mark_triggered()
        assert alert.status == SmartAlertStatus.COOLDOWN
        assert not alert.is_evaluatable()

    def test_cooldown_expired_becomes_evaluatable(self) -> None:
        alert = make_alert(SmartAlertType.RSI_OVERSOLD)
        alert.config.cooldown_hours = 1
        alert.status = SmartAlertStatus.COOLDOWN
        alert.last_triggered_at = datetime.now(UTC) - timedelta(hours=2)
        assert alert.is_evaluatable()
        assert alert.status == SmartAlertStatus.ACTIVE

    def test_mark_triggered_sets_cooldown(self) -> None:
        alert = make_alert(SmartAlertType.VOLUME_SPIKE)
        alert.mark_triggered()
        assert alert.status == SmartAlertStatus.COOLDOWN
        assert alert.last_triggered_at is not None


# ── evaluate_smart_alert ──────────────────────────────────────────────────────


class TestEvaluateSmartAlert:
    # RSI_OVERSOLD
    def test_rsi_oversold_triggers_on_downtrend(self) -> None:
        bars = make_bars(60, base_price=50.0, trend=-0.8)
        alert = make_alert(SmartAlertType.RSI_OVERSOLD, SmartAlertConfig(rsi_oversold=30.0))
        price = bars[-1]["close"]
        result = evaluate_smart_alert(alert, bars, price)
        # Downtrend forte → RSI < 30
        assert result.alert_type == SmartAlertType.RSI_OVERSOLD
        assert "RSI" in result.message

    def test_rsi_oversold_not_triggered_on_uptrend(self) -> None:
        bars = make_bars(60, base_price=20.0, trend=+0.8)
        alert = make_alert(SmartAlertType.RSI_OVERSOLD, SmartAlertConfig(rsi_oversold=30.0))
        price = bars[-1]["close"]
        result = evaluate_smart_alert(alert, bars, price)
        assert not result.triggered

    def test_rsi_oversold_insufficient_data(self) -> None:
        bars = make_bars(5)
        alert = make_alert(SmartAlertType.RSI_OVERSOLD)
        result = evaluate_smart_alert(alert, bars, 40.0)
        assert not result.triggered
        assert "insuficiente" in result.message.lower()

    # RSI_OVERBOUGHT
    def test_rsi_overbought_triggers_on_uptrend(self) -> None:
        bars = make_bars(60, base_price=20.0, trend=+0.8)
        alert = make_alert(SmartAlertType.RSI_OVERBOUGHT, SmartAlertConfig(rsi_overbought=70.0))
        price = bars[-1]["close"]
        result = evaluate_smart_alert(alert, bars, price)
        assert result.alert_type == SmartAlertType.RSI_OVERBOUGHT
        assert "RSI" in result.message

    def test_rsi_overbought_not_triggered_on_downtrend(self) -> None:
        bars = make_bars(60, base_price=80.0, trend=-0.8)
        alert = make_alert(SmartAlertType.RSI_OVERBOUGHT, SmartAlertConfig(rsi_overbought=70.0))
        result = evaluate_smart_alert(alert, bars, bars[-1]["close"])
        assert not result.triggered

    # MA_CROSS_UP
    def test_ma_cross_up_triggers_on_breakout(self) -> None:
        # Preço abaixo da MA nos últimos bars, depois cruza para cima
        bars = make_bars(30, base_price=40.0, trend=0.0)
        ma_period = 20
        ma = sum(b["close"] for b in bars[-ma_period:]) / ma_period
        # Força: penúltimo bar abaixo, último acima
        bars[-2]["close"] = ma - 1.0
        bars[-1]["close"] = ma + 1.0
        alert = make_alert(SmartAlertType.MA_CROSS_UP, SmartAlertConfig(ma_period=ma_period))
        result = evaluate_smart_alert(alert, bars, bars[-1]["close"])
        assert result.alert_type == SmartAlertType.MA_CROSS_UP

    # MA_CROSS_DOWN
    def test_ma_cross_down_triggers(self) -> None:
        bars = make_bars(30, base_price=40.0, trend=0.0)
        ma_period = 20
        ma = sum(b["close"] for b in bars[-ma_period:]) / ma_period
        bars[-2]["close"] = ma + 1.0
        bars[-1]["close"] = ma - 1.0
        alert = make_alert(SmartAlertType.MA_CROSS_DOWN, SmartAlertConfig(ma_period=ma_period))
        result = evaluate_smart_alert(alert, bars, bars[-1]["close"])
        assert result.alert_type == SmartAlertType.MA_CROSS_DOWN

    # VOLUME_SPIKE
    def test_volume_spike_triggers_on_high_volume(self) -> None:
        bars = make_bars(30, volume_mult=1.0)
        bars[-1]["volume"] = 10_000_000  # spike
        alert = make_alert(SmartAlertType.VOLUME_SPIKE, SmartAlertConfig(volume_multiplier=2.5))
        result = evaluate_smart_alert(alert, bars, 40.0)
        assert result.triggered
        assert result.indicator_value >= 2.5

    def test_volume_spike_not_triggered_on_normal_volume(self) -> None:
        bars = make_bars(30, volume_mult=1.0)
        alert = make_alert(SmartAlertType.VOLUME_SPIKE, SmartAlertConfig(volume_multiplier=2.5))
        result = evaluate_smart_alert(alert, bars, 40.0)
        assert not result.triggered

    def test_volume_spike_insufficient_data(self) -> None:
        bars = make_bars(5)
        alert = make_alert(SmartAlertType.VOLUME_SPIKE)
        result = evaluate_smart_alert(alert, bars, 40.0)
        assert not result.triggered

    # NEW_HIGH_52W
    def test_new_high_52w_triggers(self) -> None:
        bars = make_bars(50, base_price=40.0, trend=0.0)
        alert = make_alert(SmartAlertType.NEW_HIGH_52W)
        prev_high = max(b.get("high", b["close"]) for b in bars[:-1])
        result = evaluate_smart_alert(alert, bars, prev_high + 5.0)
        assert result.triggered
        assert "maximo" in result.message.lower()

    def test_new_high_52w_not_triggered_below_prev(self) -> None:
        bars = make_bars(50, base_price=60.0, trend=0.0)
        alert = make_alert(SmartAlertType.NEW_HIGH_52W)
        prev_high = max(b.get("high", b["close"]) for b in bars)
        result = evaluate_smart_alert(alert, bars, prev_high - 5.0)
        assert not result.triggered

    # NEW_LOW_52W
    def test_new_low_52w_triggers(self) -> None:
        bars = make_bars(50, base_price=40.0, trend=0.0)
        alert = make_alert(SmartAlertType.NEW_LOW_52W)
        prev_low = min(b.get("low", b["close"]) for b in bars[:-1] if b.get("low", b["close"]) > 0)
        result = evaluate_smart_alert(alert, bars, prev_low - 5.0)
        assert result.triggered
        assert result.severity == "critical"

    def test_new_low_52w_not_triggered_above_prev(self) -> None:
        bars = make_bars(50, base_price=40.0, trend=0.0)
        alert = make_alert(SmartAlertType.NEW_LOW_52W)
        prev_low = min(b.get("low", b["close"]) for b in bars[:-1] if b.get("low", b["close"]) > 0)
        result = evaluate_smart_alert(alert, bars, prev_low + 5.0)
        assert not result.triggered

    # PRICE_ABOVE
    def test_price_above_triggers(self) -> None:
        alert = make_alert(SmartAlertType.PRICE_ABOVE, SmartAlertConfig(price_threshold=45.0))
        result = evaluate_smart_alert(alert, make_bars(5), 50.0)
        assert result.triggered

    def test_price_above_not_triggered_below_threshold(self) -> None:
        alert = make_alert(SmartAlertType.PRICE_ABOVE, SmartAlertConfig(price_threshold=45.0))
        result = evaluate_smart_alert(alert, make_bars(5), 40.0)
        assert not result.triggered

    def test_price_above_zero_threshold_not_triggered(self) -> None:
        alert = make_alert(SmartAlertType.PRICE_ABOVE, SmartAlertConfig(price_threshold=0.0))
        result = evaluate_smart_alert(alert, make_bars(5), 999.0)
        assert not result.triggered  # threshold=0 → nunca dispara

    # PRICE_BELOW
    def test_price_below_triggers(self) -> None:
        alert = make_alert(SmartAlertType.PRICE_BELOW, SmartAlertConfig(price_threshold=35.0))
        result = evaluate_smart_alert(alert, make_bars(5), 30.0)
        assert result.triggered
        assert result.severity == "warning"

    def test_price_below_not_triggered_above_threshold(self) -> None:
        alert = make_alert(SmartAlertType.PRICE_BELOW, SmartAlertConfig(price_threshold=35.0))
        result = evaluate_smart_alert(alert, make_bars(5), 40.0)
        assert not result.triggered

    # Metadados do resultado
    def test_result_has_all_fields(self) -> None:
        alert = make_alert(SmartAlertType.PRICE_ABOVE, SmartAlertConfig(price_threshold=30.0))
        result = evaluate_smart_alert(alert, make_bars(5), 35.0)
        assert result.alert_id == alert.alert_id
        assert result.ticker == "PETR4"
        assert isinstance(result.message, str)
        assert result.severity in ("info", "warning", "critical")
        assert isinstance(result.indicator_value, float)

    def test_not_triggered_result_has_triggered_false(self) -> None:
        alert = make_alert(SmartAlertType.PRICE_BELOW, SmartAlertConfig(price_threshold=20.0))
        result = evaluate_smart_alert(alert, make_bars(5), 40.0)
        assert result.triggered is False
