"""
Testes do Risk Engine (R1.P2).

Cobertura:
  realized_vol_daily      — std dev simples; vazio/1-elem -> 0
  annualize_vol           — sqrt(252)
  position_size_vol_target
    - capital/price 0    -> blocked
    - vol 0              -> blocked (missing_realized_vol)
    - qty < lot          -> blocked
    - cap por max_pos_pct atua
    - capital_at_risk = qty * |sl_distance|
    - lot_size 5 (futuros) arredonda para baixo
  compute_atr             — Wilder smoothing; len < period+1 -> 0
  compute_atr_levels
    - BUY: SL = entry-N*ATR, TP = entry+N*ATR
    - SELL invertido
    - ATR 0 -> (None, None)
  check_max_positions     — current >= limit bloqueia
  check_circuit_breaker   — pnl_pct <= threshold bloqueia
"""

from __future__ import annotations

import math

import pytest

from finanalytics_ai.domain.robot.risk import (
    DEFAULT_TARGET_VOL,
    annualize_vol,
    check_circuit_breaker,
    check_max_positions,
    compute_atr,
    compute_atr_levels,
    position_size_vol_target,
    realized_vol_daily,
)


# ── realized_vol_daily / annualize_vol ────────────────────────────────────────


class TestVolEstimation:
    def test_empty_returns_zero(self) -> None:
        assert realized_vol_daily([]) == 0.0

    def test_single_return_zero(self) -> None:
        assert realized_vol_daily([0.01]) == 0.0

    def test_constant_returns_zero(self) -> None:
        # Variancia de retornos iguais e 0
        assert realized_vol_daily([0.01, 0.01, 0.01, 0.01]) == 0.0

    def test_known_std(self) -> None:
        # Returns [-0.02, 0.0, 0.02] -> mean=0, var=(0.02²+0+0.02²)/2=0.0004 -> std=0.02
        v = realized_vol_daily([-0.02, 0.0, 0.02])
        assert v == pytest.approx(0.02)

    def test_annualize(self) -> None:
        assert annualize_vol(0.01) == pytest.approx(0.01 * math.sqrt(252))

    def test_annualize_custom_factor(self) -> None:
        assert annualize_vol(0.01, factor=12) == pytest.approx(0.01 * math.sqrt(12))


# ── position_size_vol_target ──────────────────────────────────────────────────


class TestPositionSizing:
    def test_zero_capital_blocked(self) -> None:
        s = position_size_vol_target(capital=0, price=30, realized_vol_annual=0.3)
        assert s.blocked is True
        assert "zero" in s.reason

    def test_zero_price_blocked(self) -> None:
        s = position_size_vol_target(capital=10_000, price=0, realized_vol_annual=0.3)
        assert s.blocked is True

    def test_zero_vol_blocked(self) -> None:
        s = position_size_vol_target(capital=10_000, price=30, realized_vol_annual=0)
        assert s.blocked is True
        assert "missing_realized_vol" in s.reason

    def test_typical_sizing(self) -> None:
        # capital 10k, price 30, vol_anual 30%, target 15%, kelly 0.25
        # raw_qty = (0.15 * 10_000 * 0.25) / (0.3 * 30) = 375 / 9 = 41.67 -> 41
        # max_cap = 0.10 * 10000 / 30 = 33.33 -> cap atua
        s = position_size_vol_target(
            capital=10_000,
            price=30.0,
            realized_vol_annual=0.30,
            target_vol=0.15,
            kelly_fraction=0.25,
            max_position_pct=0.10,
        )
        assert s.blocked is False
        assert s.qty == 33  # capped por max_position_pct
        assert s.notional == pytest.approx(33 * 30)

    def test_high_vol_smaller_size(self) -> None:
        s_low = position_size_vol_target(
            capital=100_000, price=30, realized_vol_annual=0.20
        )
        s_high = position_size_vol_target(
            capital=100_000, price=30, realized_vol_annual=0.50
        )
        # Vol mais alta -> qty menor (ou igual se ambos saturarem cap)
        assert s_high.qty <= s_low.qty

    def test_capital_at_risk_with_sl(self) -> None:
        s = position_size_vol_target(
            capital=10_000,
            price=30.0,
            realized_vol_annual=0.30,
            sl_distance=0.6,  # 2% do preco
        )
        assert s.qty > 0
        assert s.capital_at_risk == pytest.approx(s.qty * 0.6)

    def test_lot_size_rounding(self) -> None:
        # WIN tem lot 5; raw_qty fracionario deve arredondar para baixo
        s = position_size_vol_target(
            capital=100_000,
            price=130_000,  # 1 contrato WIN ~ 130k notional
            realized_vol_annual=0.30,
            lot_size=5,
        )
        # qty pode ser 0 (sub-lot) ou multiplo de 5
        assert s.qty % 5 == 0

    def test_below_lot_blocks(self) -> None:
        s = position_size_vol_target(
            capital=1_000,  # capital pequeno
            price=130_000,  # contrato grande
            realized_vol_annual=0.30,
            lot_size=5,
        )
        assert s.blocked is True
        assert "lot_size" in s.reason


# ── ATR ───────────────────────────────────────────────────────────────────────


class TestATR:
    def test_insufficient_bars_returns_zero(self) -> None:
        bars = [{"high": 10, "low": 9, "close": 9.5}] * 5
        assert compute_atr(bars, period=14) == 0.0

    def test_constant_range_atr(self) -> None:
        # 20 bars com range 1.0 (high=10, low=9, close=9.5) -> ATR ~ 1.0
        bars = [{"high": 10.0, "low": 9.0, "close": 9.5} for _ in range(20)]
        atr = compute_atr(bars, period=14)
        assert atr == pytest.approx(1.0, abs=0.01)

    def test_zero_period_returns_zero(self) -> None:
        bars = [{"high": 10, "low": 9, "close": 9.5} for _ in range(20)]
        # period > len-1 -> insufficient
        assert compute_atr(bars, period=50) == 0.0


class TestATRLevels:
    def test_buy_levels(self) -> None:
        tp, sl = compute_atr_levels(entry=30.0, side="buy", atr=1.0, sl_mult=2.0, tp_mult=3.0)
        assert sl == pytest.approx(28.0)  # 30 - 2*1
        assert tp == pytest.approx(33.0)  # 30 + 3*1

    def test_sell_levels(self) -> None:
        tp, sl = compute_atr_levels(entry=30.0, side="sell", atr=1.0, sl_mult=2.0, tp_mult=3.0)
        assert sl == pytest.approx(32.0)  # 30 + 2*1
        assert tp == pytest.approx(27.0)  # 30 - 3*1

    def test_zero_atr(self) -> None:
        assert compute_atr_levels(entry=30, side="buy", atr=0) == (None, None)

    def test_zero_entry(self) -> None:
        assert compute_atr_levels(entry=0, side="buy", atr=1.0) == (None, None)

    def test_floor_at_one_cent(self) -> None:
        # SL nao pode passar de 0
        tp, sl = compute_atr_levels(entry=1.0, side="buy", atr=10.0, sl_mult=2.0, tp_mult=3.0)
        assert sl == pytest.approx(0.01)


# ── Gates ─────────────────────────────────────────────────────────────────────


class TestGates:
    def test_max_positions_ok(self) -> None:
        ok, _ = check_max_positions(current=2, limit=5)
        assert ok is True

    def test_max_positions_exceeded(self) -> None:
        ok, reason = check_max_positions(current=5, limit=5)
        assert ok is False
        assert "5/5" in reason

    def test_circuit_breaker_ok(self) -> None:
        ok, _ = check_circuit_breaker(pnl_pct_today=-1.0, threshold=-2.0)
        assert ok is True

    def test_circuit_breaker_tripped(self) -> None:
        ok, reason = check_circuit_breaker(pnl_pct_today=-2.5, threshold=-2.0)
        assert ok is False
        assert "circuit_breaker" in reason

    def test_circuit_breaker_at_threshold_trips(self) -> None:
        # <=, nao < — toca o threshold ja bloqueia
        ok, _ = check_circuit_breaker(pnl_pct_today=-2.0, threshold=-2.0)
        assert ok is False
