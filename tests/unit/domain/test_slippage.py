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
