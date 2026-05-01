"""Testes do modelo de slippage por classe de ativo (R5)."""

from __future__ import annotations

import pytest

from finanalytics_ai.domain.backtesting.slippage import (
    N_TICKS_FUTURE,
    SLIPPAGE_PCT_STOCK,
    TICK_SIZES,
    _is_future,
    apply_slippage,
    slippage_amount,
)


class TestIsFuture:
    def test_wdo_alias_is_future(self) -> None:
        assert _is_future("WDOFUT") is True

    def test_wdo_monthly_contract_is_future(self) -> None:
        assert _is_future("WDOK26") is True

    def test_winm26_is_future(self) -> None:
        assert _is_future("WINM26") is True

    def test_petr4_is_not_future(self) -> None:
        assert _is_future("PETR4") is False

    def test_empty_string_is_not_future(self) -> None:
        assert _is_future("") is False

    def test_lowercase_normalized(self) -> None:
        assert _is_future("wdofut") is True


class TestSlippageAmount:
    def test_stock_slippage_is_pct_of_price(self) -> None:
        assert slippage_amount(40.0, "PETR4") == pytest.approx(40.0 * SLIPPAGE_PCT_STOCK)

    def test_wdo_slippage_is_n_ticks(self) -> None:
        # WDO tick = 0.5; slippage = 2 * 0.5 = 1.0 (independente do preço)
        assert slippage_amount(5500.0, "WDOK26") == pytest.approx(
            N_TICKS_FUTURE * TICK_SIZES["WDO"]
        )

    def test_win_slippage_is_n_ticks(self) -> None:
        # WIN tick = 5.0; slippage = 2 * 5.0 = 10.0 pontos
        assert slippage_amount(130000.0, "WINM26") == pytest.approx(
            N_TICKS_FUTURE * TICK_SIZES["WIN"]
        )

    def test_zero_price_returns_zero(self) -> None:
        assert slippage_amount(0.0, "PETR4") == 0.0

    def test_negative_price_returns_zero(self) -> None:
        assert slippage_amount(-1.0, "PETR4") == 0.0


class TestApplySlippage:
    def test_buy_pays_above_close(self) -> None:
        # BUY em PETR4 a 40.00 deve pagar 40 + 0.05% = 40.02
        result = apply_slippage(40.00, "buy", "PETR4")
        assert result == pytest.approx(40.0 + 40.0 * SLIPPAGE_PCT_STOCK)
        assert result > 40.00

    def test_sell_receives_below_close(self) -> None:
        # SELL em PETR4 a 40.00 deve receber 40 - 0.05% = 39.98
        result = apply_slippage(40.00, "sell", "PETR4")
        assert result == pytest.approx(40.0 - 40.0 * SLIPPAGE_PCT_STOCK)
        assert result < 40.00

    def test_wdo_buy_pays_one_real_above(self) -> None:
        # WDO: 2 ticks * R$ 0,50 = R$ 1,00
        assert apply_slippage(5500.0, "buy", "WDOK26") == pytest.approx(5501.0)

    def test_win_sell_receives_10pts_below(self) -> None:
        # WIN: 2 ticks * 5 pontos = 10 pontos
        assert apply_slippage(130000.0, "sell", "WINM26") == pytest.approx(129990.0)

    def test_sell_never_negative(self) -> None:
        # Sanity: preço muito baixo + slippage maior que preço não pode virar negativo
        # (não deveria acontecer com modelo atual, mas guard contra bug)
        assert apply_slippage(0.01, "sell", "WDOK26") == 0.0

    def test_zero_price_unchanged(self) -> None:
        assert apply_slippage(0.0, "buy", "PETR4") == 0.0


# ── ADV-aware (R5 follow-up) ──────────────────────────────────────────────────


from finanalytics_ai.domain.backtesting.slippage import (
    IMPACT_COEF,
    MAX_ADV_MULT,
    adv_multiplier,
    compute_adv,
)


def _bars_with_volume(n: int = 30, volume: float = 1_000_000.0, close: float = 30.0) -> list[dict]:
    """Helper: bars com volume e close constantes — ADV notional previsível."""
    return [{"time": 1700_000_000 + i * 86400, "close": close, "volume": volume} for i in range(n)]


class TestComputeADV:
    def test_basic_notional_average(self) -> None:
        bars = _bars_with_volume(n=25, volume=1_000_000, close=30.0)
        # ADV at index 24 com lookback 20: media de bars[4:24] (20 bars)
        adv = compute_adv(bars, idx=24, lookback=20)
        assert adv == pytest.approx(30.0 * 1_000_000)

    def test_no_lookahead_excludes_current_bar(self) -> None:
        bars = [{"time": i, "close": 30.0, "volume": 1_000_000.0} for i in range(10)]
        bars.append({"time": 10, "close": 30.0, "volume": 999_999_999.0})  # outlier
        # idx=10 com lookback=10: media de bars[0:10] (sem outlier)
        adv = compute_adv(bars, idx=10, lookback=10)
        assert adv == pytest.approx(30.0 * 1_000_000)

    def test_short_window_uses_what_is_available(self) -> None:
        bars = _bars_with_volume(n=5, volume=1_000_000, close=30.0)
        adv = compute_adv(bars, idx=3, lookback=20)  # so 3 bars antes
        assert adv == pytest.approx(30.0 * 1_000_000)

    def test_zero_volume_ignored(self) -> None:
        bars = [
            {"time": 0, "close": 30.0, "volume": 0.0},
            {"time": 1, "close": 30.0, "volume": 1_000_000.0},
            {"time": 2, "close": 30.0, "volume": 1_000_000.0},
        ]
        adv = compute_adv(bars, idx=2, lookback=20)
        # bar[0] ignorado (vol=0), media de bars[1:2] = 30M
        assert adv == pytest.approx(30.0 * 1_000_000)

    def test_idx_zero_returns_zero(self) -> None:
        bars = _bars_with_volume(n=5)
        assert compute_adv(bars, idx=0) == 0.0

    def test_empty_bars_returns_zero(self) -> None:
        assert compute_adv([], idx=5) == 0.0


class TestADVMultiplier:
    def test_no_adv_returns_one(self) -> None:
        assert adv_multiplier(notional_trade=100_000, adv_notional=0) == 1.0
        assert adv_multiplier(notional_trade=100_000, adv_notional=-1) == 1.0

    def test_zero_trade_returns_one(self) -> None:
        assert adv_multiplier(notional_trade=0, adv_notional=1_000_000) == 1.0

    def test_low_participation_close_to_one(self) -> None:
        # 0.01% participation -> 1 + IMPACT_COEF*sqrt(0.0001) = 1 + IMPACT_COEF*0.01
        m = adv_multiplier(notional_trade=100, adv_notional=1_000_000)
        expected = 1.0 + IMPACT_COEF * 0.01
        assert m == pytest.approx(expected, rel=1e-6)

    def test_one_pct_participation(self) -> None:
        # 1% participation: 1 + IMPACT_COEF*sqrt(0.01) = 1 + IMPACT_COEF*0.1
        m = adv_multiplier(notional_trade=10_000, adv_notional=1_000_000)
        expected = 1.0 + IMPACT_COEF * 0.1
        assert m == pytest.approx(expected, rel=1e-6)

    def test_capped_at_max(self) -> None:
        # Participation enorme deveria saturar no cap
        m = adv_multiplier(notional_trade=100_000_000, adv_notional=1_000_000)
        assert m == pytest.approx(MAX_ADV_MULT)

    def test_monotonically_increasing(self) -> None:
        prev = 1.0
        for trade in [1_000, 10_000, 50_000, 100_000, 500_000]:
            m = adv_multiplier(notional_trade=trade, adv_notional=10_000_000)
            assert m >= prev
            prev = m


class TestSlippageAmountADVAware:
    def test_low_participation_close_to_base(self) -> None:
        # 0.001% participation: stock de 30 BRL com base 0.05% = 0.015 BRL.
        # Multiplier ≈ 1.001 -> diff < 0.001%
        base = slippage_amount(30.0, "PETR4")  # sem ADV = base
        with_adv = slippage_amount(30.0, "PETR4", notional_trade=100, adv_notional=10_000_000)
        assert with_adv == pytest.approx(base, rel=0.01)

    def test_high_participation_increases_slippage(self) -> None:
        base = slippage_amount(30.0, "PETR4")
        with_adv = slippage_amount(30.0, "PETR4", notional_trade=500_000, adv_notional=10_000_000)
        # 5% participation -> mult > 1.2
        assert with_adv > base * 1.2

    def test_extreme_participation_capped(self) -> None:
        base = slippage_amount(30.0, "PETR4")
        with_adv = slippage_amount(
            30.0, "PETR4", notional_trade=1_000_000_000, adv_notional=1_000_000
        )
        assert with_adv == pytest.approx(base * MAX_ADV_MULT)

    def test_futures_also_scaled(self) -> None:
        """ADV-aware tambem afeta futuros (mult sobre N_TICKS_FUTURE * tick)."""
        base = slippage_amount(5500.0, "WDOK26")  # 2 * 0.5 = 1.0
        with_adv = slippage_amount(5500.0, "WDOK26", notional_trade=500_000, adv_notional=1_000_000)
        assert with_adv > base * 1.4  # 50% participation -> mult ~1.5+

    def test_no_kwargs_falls_back_to_fixed(self) -> None:
        """Compat retro: chamada sem ADV kwargs == modelo fixo."""
        legacy = slippage_amount(30.0, "PETR4")
        assert legacy == pytest.approx(0.0005 * 30.0)


class TestApplySlippageADVAware:
    def test_buy_with_high_participation(self) -> None:
        from finanalytics_ai.domain.backtesting.slippage import apply_slippage as apply_sl

        base_price = apply_sl(30.0, "buy", "PETR4")  # base: 30 + 0.015 = 30.015
        adv_price = apply_sl(30.0, "buy", "PETR4", notional_trade=500_000, adv_notional=10_000_000)
        # Buy paga acima -> com ADV, paga MAIS acima
        assert adv_price > base_price

    def test_sell_with_high_participation(self) -> None:
        from finanalytics_ai.domain.backtesting.slippage import apply_slippage as apply_sl

        base_price = apply_sl(30.0, "sell", "PETR4")  # base: 30 - 0.015 = 29.985
        adv_price = apply_sl(30.0, "sell", "PETR4", notional_trade=500_000, adv_notional=10_000_000)
        # Sell recebe abaixo -> com ADV, recebe MENOS
        assert adv_price < base_price
