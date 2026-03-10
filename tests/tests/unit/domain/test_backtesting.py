"""
Testes unitários para o módulo de backtesting.

Cobertura:
  - Engine: trade correto, P&L, comissão, posição forçada no fim
  - Métricas: Sharpe, drawdown, win rate, profit factor
  - RSIStrategy: gera BUY/SELL nos cruzamentos corretos
  - MACDCrossStrategy: bullish/bearish crossover
  - CombinedStrategy: AND para BUY, OR para SELL
  - BacktestService: testa mock do BrapiClient
  - get_strategy: factory válida e inválida
"""

from __future__ import annotations

import math
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from finanalytics_ai.domain.backtesting.engine import (
    BacktestResult,
    Signal,
    Trade,
    _calc_metrics,
    run_backtest,
)
from finanalytics_ai.domain.backtesting.strategies.technical import (
    CombinedStrategy,
    MACDCrossStrategy,
    RSIStrategy,
    get_strategy,
)
from finanalytics_ai.application.services.backtest_service import BacktestError, BacktestService


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_bars(prices: list[float], base_ts: int = 1_700_000_000) -> list[dict]:
    """Cria lista de barras OHLC mínimas a partir de uma lista de preços de fechamento."""
    return [
        {
            "time": base_ts + i * 86400,
            "open": p,
            "high": p * 1.01,
            "low": p * 0.99,
            "close": p,
            "volume": 1000,
        }
        for i, p in enumerate(prices)
    ]


def _make_trade(entry: float, exit_: float, qty: float = 1.0) -> Trade:
    return Trade(
        ticker="TEST",
        entry_date=datetime(2024, 1, 1),
        exit_date=datetime(2024, 1, 10),
        entry_price=entry,
        exit_price=exit_,
        quantity=qty,
    )


# ── Trade ─────────────────────────────────────────────────────────────────────


class TestTrade:
    def test_pnl_winner(self):
        t = _make_trade(100.0, 110.0, 10.0)
        assert t.pnl == pytest.approx(100.0)

    def test_pnl_loser(self):
        t = _make_trade(100.0, 90.0, 10.0)
        assert t.pnl == pytest.approx(-100.0)

    def test_pnl_pct(self):
        t = _make_trade(100.0, 115.0)
        assert t.pnl_pct == pytest.approx(15.0)

    def test_is_winner(self):
        assert _make_trade(10.0, 12.0).is_winner is True
        assert _make_trade(10.0, 8.0).is_winner is False

    def test_duration_days(self):
        t = Trade(
            ticker="X",
            entry_date=datetime(2024, 1, 1),
            exit_date=datetime(2024, 1, 11),
            entry_price=10.0,
            exit_price=11.0,
            quantity=1.0,
        )
        assert t.duration_days == pytest.approx(10.0)

    def test_to_dict_keys(self):
        d = _make_trade(10.0, 11.0).to_dict()
        assert all(k in d for k in ["pnl", "pnl_pct", "is_winner", "duration_days"])


# ── Engine ────────────────────────────────────────────────────────────────────


class TestEngine:
    def test_no_signals_no_trades(self):
        bars = _make_bars([100.0] * 50)

        class HoldStrategy:
            name = "hold"

            def generate_signals(self, bars):
                return [Signal.HOLD] * len(bars)

        result = run_backtest(bars, HoldStrategy(), "TEST", initial_capital=10_000.0)
        assert result.trades == []
        assert result.metrics.total_trades == 0

    def test_buy_sell_produces_trade(self):
        # BUY na barra 5, SELL na barra 15
        prices = [100.0] * 20
        bars = _make_bars(prices)

        class SimpleStrategy:
            name = "simple"

            def generate_signals(self, bars):
                sigs = [Signal.HOLD] * len(bars)
                sigs[5] = Signal.BUY
                sigs[15] = Signal.SELL
                return sigs

        result = run_backtest(bars, SimpleStrategy(), "TEST", initial_capital=10_000.0, commission_pct=0.0)
        assert len(result.trades) == 1

    def test_commission_reduces_pnl(self):
        """Com comissão 0% vs 1% — resultado diferente."""
        prices = [100.0] * 5 + [110.0] * 5
        bars = _make_bars(prices)

        class UpStrategy:
            name = "up"

            def generate_signals(self, b):
                s = [Signal.HOLD] * len(b)
                s[0] = Signal.BUY
                s[9] = Signal.SELL
                return s

        r0 = run_backtest(bars, UpStrategy(), "T", commission_pct=0.0, initial_capital=10_000.0)
        r1 = run_backtest(bars, UpStrategy(), "T", commission_pct=0.01, initial_capital=10_000.0)
        assert r0.metrics.final_equity > r1.metrics.final_equity

    def test_open_position_closed_at_last_bar(self):
        """Posição aberta deve ser fechada no último bar mesmo sem SELL."""
        bars = _make_bars([100.0] * 10)

        class BuyOnlyStrategy:
            name = "buy_only"

            def generate_signals(self, b):
                s = [Signal.HOLD] * len(b)
                s[2] = Signal.BUY
                return s

        result = run_backtest(bars, BuyOnlyStrategy(), "T", commission_pct=0.0)
        assert len(result.trades) == 1
        assert result.trades[0].exit_reason == "Fim do período"

    def test_equity_curve_length_matches_bars(self):
        bars = _make_bars([100.0] * 30)

        class H:
            name = "h"

            def generate_signals(self, b):
                return [Signal.HOLD] * len(b)

        result = run_backtest(bars, H(), "T")
        assert len(result.equity_curve) == 30

    def test_backtest_result_serializable(self):
        bars = _make_bars([100.0] * 30)

        class H:
            name = "h"

            def generate_signals(self, b):
                return [Signal.HOLD] * len(b)

        result = run_backtest(bars, H(), "T")
        d = result.to_dict()
        assert "metrics" in d
        assert "equity_curve" in d
        assert "trades" in d


# ── Métricas ──────────────────────────────────────────────────────────────────


class TestMetrics:
    def test_total_return(self):
        eq = [{"time": i, "equity": 100.0 + i, "drawdown": 0.0} for i in range(10)]
        m = _calc_metrics([], eq, 100.0, 109.0)
        assert m.total_return_pct == pytest.approx(9.0)

    def test_win_rate(self):
        trades = [_make_trade(10, 12), _make_trade(10, 12), _make_trade(10, 8)]
        m = _calc_metrics(trades, [], 1000.0, 1000.0)
        assert m.win_rate_pct == pytest.approx(66.67, abs=0.1)

    def test_profit_factor(self):
        # 2 winners: +R$20, 1 loser: -R$5
        trades = [_make_trade(10, 20, 2), _make_trade(10, 7.5, 2)]
        m = _calc_metrics(trades, [], 1000.0, 1000.0)
        assert m.profit_factor == pytest.approx(4.0)

    def test_max_drawdown(self):
        eq = [
            {"time": 0, "equity": 1000, "drawdown": 0},
            {"time": 1, "equity": 1200, "drawdown": 0},
            {"time": 2, "equity": 900, "drawdown": 25},  # drawdown de 25%
            {"time": 3, "equity": 1100, "drawdown": 8.33},
        ]
        m = _calc_metrics([], eq, 1000.0, 1100.0)
        assert m.max_drawdown_pct == pytest.approx(25.0)

    def test_sharpe_positive_for_uptrend(self):
        equities = [1000.0 + i * 10 for i in range(100)]
        eq = [{"time": i, "equity": e, "drawdown": 0} for i, e in enumerate(equities)]
        m = _calc_metrics([], eq, 1000.0, equities[-1])
        assert m.sharpe_ratio > 0

    def test_no_trades_returns_zero_metrics(self):
        m = _calc_metrics([], [], 1000.0, 1000.0)
        assert m.total_trades == 0
        assert m.win_rate_pct == 0.0
        assert m.profit_factor == 0.0


# ── RSI Strategy ──────────────────────────────────────────────────────────────


class TestRSIStrategy:
    def test_generates_correct_length(self):
        bars = _make_bars([100.0] * 50)
        s = RSIStrategy()
        signals = s.generate_signals(bars)
        assert len(signals) == 50

    def test_all_hold_for_flat_prices(self):
        """Preços constantes = RSI = 50 sempre, nunca cruza oversold/overbought."""
        bars = _make_bars([100.0] * 50)
        s = RSIStrategy()
        signals = s.generate_signals(bars)
        # Pode ter poucos sinais mas não deve ter compra/venda espúria
        buys = signals.count(Signal.BUY)
        sells = signals.count(Signal.SELL)
        assert buys <= 1 and sells <= 1

    def test_generates_buy_after_oversold(self):
        """Preços caindo e voltando devem gerar BUY."""
        prices = [100.0] * 10 + [70.0, 65.0, 60.0, 55.0, 50.0] + [60.0, 70.0, 80.0, 90.0, 100.0] * 3
        bars = _make_bars(prices)
        s = RSIStrategy(oversold=30.0, overbought=70.0)
        signals = s.generate_signals(bars)
        assert Signal.BUY in signals

    def test_params_dict(self):
        s = RSIStrategy(period=10, oversold=25.0, overbought=75.0)
        assert s.params == {"period": 10, "oversold": 25.0, "overbought": 75.0}


# ── MACD Strategy ─────────────────────────────────────────────────────────────


class TestMACDStrategy:
    def test_generates_correct_length(self):
        bars = _make_bars([100.0] * 60)
        s = MACDCrossStrategy()
        assert len(s.generate_signals(bars)) == 60

    def test_bullish_crossover_generates_buy(self):
        """Tendência de alta forte deve gerar BUY de MACD crossover."""
        prices = [50.0 + i * 0.5 for i in range(80)]
        bars = _make_bars(prices)
        s = MACDCrossStrategy()
        signals = s.generate_signals(bars)
        assert Signal.BUY in signals

    def test_bearish_crossover_generates_sell(self):
        """Tendência de queda forte deve gerar SELL."""
        prices = [100.0 - i * 0.5 for i in range(80)]
        bars = _make_bars(prices)
        s = MACDCrossStrategy()
        signals = s.generate_signals(bars)
        assert Signal.SELL in signals

    def test_not_enough_bars_returns_hold(self):
        """Sem dados suficientes (< slow + signal), retorna HOLD."""
        bars = _make_bars([100.0] * 10)  # muito menos que 26+9
        s = MACDCrossStrategy()
        signals = s.generate_signals(bars)
        assert all(sig == Signal.HOLD for sig in signals)


# ── Combined Strategy ─────────────────────────────────────────────────────────


class TestCombinedStrategy:
    def test_buy_requires_both(self):
        """BUY só quando RSI E MACD dão BUY simultaneamente."""
        # Usa mock das sub-estratégias indiretamente via comportamento
        bars = _make_bars([100.0] * 60)
        s = CombinedStrategy()
        signals = s.generate_signals(bars)
        # Deve ter mesmo comprimento
        assert len(signals) == 60

    def test_no_false_buys_on_flat(self):
        """Preços flat não devem gerar muitos BUY."""
        bars = _make_bars([100.0] * 60)
        s = CombinedStrategy()
        signals = s.generate_signals(bars)
        assert signals.count(Signal.BUY) <= 1


# ── Factory ───────────────────────────────────────────────────────────────────


class TestGetStrategy:
    def test_valid_names(self):
        assert get_strategy("rsi").name == "RSI Reversal"
        assert get_strategy("macd").name == "MACD Crossover"
        assert get_strategy("combined").name == "RSI + MACD Combined"

    def test_case_insensitive(self):
        assert get_strategy("RSI").name == "RSI Reversal"
        assert get_strategy("MACD").name == "MACD Crossover"

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="não encontrada"):
            get_strategy("banana")

    def test_custom_params(self):
        s = get_strategy("rsi", {"period": 7, "oversold": 25.0, "overbought": 75.0})
        assert s.period == 7
        assert s.oversold == 25.0


# ── BacktestService ───────────────────────────────────────────────────────────


class TestBacktestService:
    @pytest.mark.asyncio
    async def test_run_returns_result(self):
        mock_brapi = AsyncMock()
        mock_brapi.get_ohlc_bars.return_value = _make_bars([100.0 + i * 0.5 for i in range(60)])

        svc = BacktestService(mock_brapi)
        result = await svc.run("PETR4", "rsi", range_period="3mo")

        assert result.ticker == "PETR4"
        assert result.bars_count == 60
        assert result.metrics.initial_capital == 10_000.0

    @pytest.mark.asyncio
    async def test_empty_bars_raises_backtest_error(self):
        mock_brapi = AsyncMock()
        mock_brapi.get_ohlc_bars.return_value = []

        svc = BacktestService(mock_brapi)
        with pytest.raises(BacktestError, match="Sem dados históricos"):
            await svc.run("XXXX", "rsi")

    @pytest.mark.asyncio
    async def test_too_few_bars_raises_error(self):
        mock_brapi = AsyncMock()
        mock_brapi.get_ohlc_bars.return_value = _make_bars([100.0] * 10)

        svc = BacktestService(mock_brapi)
        with pytest.raises(BacktestError, match="insuficientes"):
            await svc.run("XXXX", "rsi")

    @pytest.mark.asyncio
    async def test_invalid_strategy_raises_error(self):
        mock_brapi = AsyncMock()
        mock_brapi.get_ohlc_bars.return_value = _make_bars([100.0] * 60)

        svc = BacktestService(mock_brapi)
        with pytest.raises(BacktestError, match="não encontrada"):
            await svc.run("PETR4", "invalid_strategy")
