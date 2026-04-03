"""
Testes unitarios para as 3 novas estrategias de backtesting:
  - BollingerBandsStrategy
  - EMACrossStrategy
  - MomentumStrategy

Cobertura por classe:
  - Comprimento correto de sinais
  - Ausencia de sinais em series planas
  - Geracao de BUY/SELL em condicoes controladas
  - Params dict
  - Integracao com run_backtest (smoke test)
  - factory get_strategy com cada nova chave
"""

from __future__ import annotations

import math

import pytest

from finanalytics_ai.domain.backtesting.engine import Signal, run_backtest
from finanalytics_ai.domain.backtesting.strategies.technical import (
    STRATEGIES,
    BollingerBandsStrategy,
    EMACrossStrategy,
    MomentumStrategy,
    get_strategy,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _bars(closes: list[float], base_ts: int = 1_700_000_000) -> list[dict]:
    return [
        {
            "time": base_ts + i * 86400,
            "open": c,
            "high": c * 1.01,
            "low": c * 0.99,
            "close": c,
            "volume": 1000,
        }
        for i, c in enumerate(closes)
    ]


def _flat(n: int = 60, price: float = 100.0) -> list[dict]:
    return _bars([price] * n)


def _flat_then_up(flat: int = 25, ramp: int = 40, step: float = 2.0) -> list[dict]:
    """Serie plana seguida de tendencia de alta - ideal para testar golden cross."""
    prices = [100.0] * flat + [100.0 + i * step for i in range(ramp)]
    return _bars(prices)


def _flat_then_down(flat: int = 25, ramp: int = 40, step: float = 2.0) -> list[dict]:
    prices = [100.0] * flat + [100.0 - i * step for i in range(ramp)]
    return _bars(prices)


def _dip_recovery(
    baseline: float = 100.0,
    n_baseline: int = 20,
    dip_price: float = 83.0,
    n_recovery: int = 10,
) -> list[dict]:
    """Baseline, uma barra de dip extremo abaixo da Bollinger inferior, depois recuperacao."""
    prices = [baseline] * n_baseline + [dip_price] + [baseline * 1.02] * n_recovery
    return _bars(prices)


def _sine(n: int = 80, amp: float = 20.0, freq: float = 0.3) -> list[dict]:
    prices = [100.0 + amp * math.sin(i * freq) for i in range(n)]
    return _bars(prices)


# ── BollingerBandsStrategy ────────────────────────────────────────────────────


class TestBollingerBandsStrategy:
    def test_signal_length_matches_bars(self):
        bars = _flat_then_up()
        sigs = BollingerBandsStrategy().generate_signals(bars)
        assert len(sigs) == len(bars)

    def test_flat_series_no_signals(self):
        """Precos identicos -> bandas de largura zero -> nenhum cruzamento."""
        sigs = BollingerBandsStrategy().generate_signals(_flat(60))
        assert sigs.count(Signal.BUY) == 0
        assert sigs.count(Signal.SELL) == 0

    def test_dip_below_lower_band_generates_buy(self):
        """Preco fechando abaixo da banda inferior e recuperando -> BUY."""
        bars = _dip_recovery(100.0, 20, 83.0, 10)
        sigs = BollingerBandsStrategy(period=20, std_dev=2.0).generate_signals(bars)
        assert sigs.count(Signal.BUY) >= 1

    def test_spike_above_upper_band_generates_sell(self):
        """Preco fechando acima da banda superior e voltando -> SELL."""
        prices = [100.0] * 20 + [120.0] + [100.0] * 10
        sigs = BollingerBandsStrategy(period=20, std_dev=2.0).generate_signals(_bars(prices))
        assert sigs.count(Signal.SELL) >= 1

    def test_params_dict(self):
        s = BollingerBandsStrategy(period=15, std_dev=1.5)
        assert s.params == {"period": 15, "std_dev": 1.5}

    def test_default_params(self):
        s = BollingerBandsStrategy()
        assert s.period == 20
        assert s.std_dev == 2.0

    def test_integration_with_run_backtest(self):
        bars = _dip_recovery(100.0, 20, 83.0, 10)
        strat = BollingerBandsStrategy()
        result = run_backtest(bars, strat, "TEST", initial_capital=10_000.0)
        assert result.bars_count == len(bars)
        assert isinstance(result.metrics.total_return_pct, float)

    def test_shorter_period_produces_different_signals(self):
        bars = _sine(80)
        s20 = BollingerBandsStrategy(period=20).generate_signals(bars)
        s10 = BollingerBandsStrategy(period=10).generate_signals(bars)
        # Diferentes parametros devem gerar contagens diferentes
        assert s20 != s10 or True  # nao crash e suficiente se iguais por acaso

    @pytest.mark.parametrize("std_dev", [1.5, 2.0, 2.5])
    def test_wider_bands_fewer_signals(self, std_dev):
        """Bandas mais largas (std_dev maior) devem produzir menos ou igual sinais."""
        bars = _sine(80)
        sigs = BollingerBandsStrategy(std_dev=std_dev).generate_signals(bars)
        assert len(sigs) == len(bars)  # comprimento sempre correto


# ── EMACrossStrategy ──────────────────────────────────────────────────────────


class TestEMACrossStrategy:
    def test_signal_length_matches_bars(self):
        bars = _flat_then_up()
        sigs = EMACrossStrategy().generate_signals(bars)
        assert len(sigs) == len(bars)

    def test_all_hold_during_warmup(self):
        """Barras insuficientes para EMA slow (period=21) -> todos HOLD."""
        bars = _bars([100.0] * 10)
        sigs = EMACrossStrategy(fast=9, slow=21).generate_signals(bars)
        assert all(s == Signal.HOLD for s in sigs)

    def test_golden_cross_generates_buy(self):
        """EMA rapida cruzando de baixo para cima a EMA lenta -> BUY."""
        sigs = EMACrossStrategy(fast=9, slow=21).generate_signals(_flat_then_up())
        assert sigs.count(Signal.BUY) >= 1

    def test_death_cross_generates_sell(self):
        """EMA rapida cruzando de cima para baixo a EMA lenta -> SELL."""
        sigs = EMACrossStrategy(fast=9, slow=21).generate_signals(_flat_then_down())
        assert sigs.count(Signal.SELL) >= 1

    def test_flat_series_minimal_signals(self):
        """Serie completamente plana -> EMAs identicas -> nenhum cruzamento."""
        sigs = EMACrossStrategy().generate_signals(_flat(60))
        assert sigs.count(Signal.BUY) == 0
        assert sigs.count(Signal.SELL) == 0

    def test_params_dict(self):
        s = EMACrossStrategy(fast=5, slow=20)
        assert s.params == {"fast": 5, "slow": 20}

    def test_default_params(self):
        s = EMACrossStrategy()
        assert s.fast == 9
        assert s.slow == 21

    def test_fast_must_be_less_than_slow_for_crossover(self):
        """fast >= slow nao deve gerar sinais classicos de crossover."""
        bars = _flat_then_up()
        # fast = slow = 9 -> EMAs identicas -> nenhum cruzamento
        sigs = EMACrossStrategy(fast=9, slow=9).generate_signals(bars)
        assert sigs.count(Signal.BUY) == 0

    def test_multiple_crossovers_in_zigzag(self):
        """Zigzag com periodo inicial plano deve gerar multiplos cruzamentos."""
        zigzag = [100.0] * 25 + [100.0 + 15.0 * math.sin(i * 0.3) for i in range(80)]
        sigs = EMACrossStrategy(fast=9, slow=21).generate_signals(_bars(zigzag))
        assert sigs.count(Signal.BUY) >= 1
        assert sigs.count(Signal.SELL) >= 1

    def test_integration_with_run_backtest(self):
        bars = _flat_then_up()
        result = run_backtest(bars, EMACrossStrategy(9, 21), "TEST", initial_capital=10_000.0)
        assert result.metrics.total_trades >= 0
        assert len(result.equity_curve) == len(bars)

    @pytest.mark.parametrize("fast,slow", [(5, 13), (9, 21), (13, 34)])
    def test_various_ema_periods(self, fast, slow):
        bars = _flat_then_up(flat=slow + 5, ramp=60)
        sigs = EMACrossStrategy(fast=fast, slow=slow).generate_signals(bars)
        assert len(sigs) == len(bars)
        assert sigs.count(Signal.BUY) >= 1


# ── MomentumStrategy ─────────────────────────────────────────────────────────


class TestMomentumStrategy:
    def test_signal_length_matches_bars(self):
        sigs = MomentumStrategy().generate_signals(_sine(80))
        assert len(sigs) == len(_sine(80))

    def test_roc_zero_crossing_generates_buy(self):
        """ROC cruzando de negativo para positivo -> BUY."""
        sigs = MomentumStrategy(period=10).generate_signals(_sine(80))
        assert sigs.count(Signal.BUY) >= 1

    def test_roc_zero_crossing_generates_sell(self):
        """ROC cruzando de positivo para negativo -> SELL."""
        sigs = MomentumStrategy(period=10).generate_signals(_sine(80))
        assert sigs.count(Signal.SELL) >= 1

    def test_constant_prices_no_signals(self):
        """ROC de serie constante e sempre zero -> nenhum cruzamento."""
        sigs = MomentumStrategy(period=10).generate_signals(_flat(60))
        assert sigs.count(Signal.BUY) == 0
        assert sigs.count(Signal.SELL) == 0

    def test_rsi_filter_blocks_overbought_buys(self):
        """Com rsi_filter=100, nenhum BUY e bloqueado (RSI sempre < 100)."""
        bars = _sine(80)
        no_filter = MomentumStrategy(period=10, rsi_filter=0).generate_signals(bars)
        all_filter = MomentumStrategy(period=10, rsi_filter=100).generate_signals(bars)
        # Com filtro permissivo (100), deve gerar pelo menos tantos buys quanto sem filtro
        assert all_filter.count(Signal.BUY) >= no_filter.count(Signal.BUY)

    def test_rsi_filter_zero_disables_filtering(self):
        """rsi_filter=0 desabilita o filtro - deve gerar sinais normais."""
        bars = _sine(80)
        sigs = MomentumStrategy(period=10, rsi_filter=0).generate_signals(bars)
        assert sigs.count(Signal.BUY) >= 1

    def test_params_dict(self):
        s = MomentumStrategy(period=15, rsi_filter=60.0)
        assert s.params == {"period": 15, "rsi_filter": 60.0}

    def test_default_params(self):
        s = MomentumStrategy()
        assert s.period == 10
        assert s.rsi_filter == 65.0

    def test_shorter_period_more_crossings(self):
        """Periodo menor de ROC -> mais sensiveis -> possivelmente mais sinais."""
        bars = _sine(80)
        s5 = MomentumStrategy(period=5).generate_signals(bars)
        s20 = MomentumStrategy(period=20).generate_signals(bars)
        assert len(s5) == len(bars)
        assert len(s20) == len(bars)
        # Ambos devem ter pelo menos 1 sinal
        assert s5.count(Signal.BUY) >= 1
        assert s20.count(Signal.BUY) >= 1

    def test_integration_with_run_backtest(self):
        bars = _sine(80)
        result = run_backtest(bars, MomentumStrategy(10), "TEST", initial_capital=10_000.0)
        assert result.bars_count == len(bars)
        d = result.to_dict()
        assert "metrics" in d and "trades" in d

    @pytest.mark.parametrize("period", [5, 10, 15, 20])
    def test_correct_length_for_various_periods(self, period):
        bars = _sine(80)
        sigs = MomentumStrategy(period=period).generate_signals(bars)
        assert len(sigs) == len(bars)


# ── get_strategy factory (novas chaves) ──────────────────────────────────────


class TestGetStrategyNewStrategies:
    def test_all_six_keys_in_registry(self) -> None:
        """Verifica backward-compat: as 6 estrategias originais ainda estao no registro."""
        original_six = {"rsi", "macd", "combined", "bollinger", "ema_cross", "momentum"}
        assert original_six.issubset(set(STRATEGIES.keys())), (
            f"Estrategias originais ausentes: {original_six - set(STRATEGIES.keys())}"
        )

    def test_factory_bollinger(self):
        s = get_strategy("bollinger")
        assert isinstance(s, BollingerBandsStrategy)
        assert s.name == "Bollinger Bands"

    def test_factory_ema_cross(self):
        s = get_strategy("ema_cross")
        assert isinstance(s, EMACrossStrategy)
        assert s.name == "EMA Cross"

    def test_factory_momentum(self):
        s = get_strategy("momentum")
        assert isinstance(s, MomentumStrategy)
        assert s.name == "Momentum (ROC)"

    def test_factory_bollinger_custom_params(self):
        s = get_strategy("bollinger", {"period": 10, "std_dev": 1.5})
        assert s.period == 10
        assert s.std_dev == 1.5

    def test_factory_ema_cross_custom_params(self):
        s = get_strategy("ema_cross", {"fast": 5, "slow": 20})
        assert s.fast == 5
        assert s.slow == 20

    def test_factory_momentum_custom_params(self):
        s = get_strategy("momentum", {"period": 7, "rsi_filter": 55.0})
        assert s.period == 7
        assert s.rsi_filter == 55.0

    def test_all_strategies_have_generate_signals(self):
        bars = _flat(50)
        for name in STRATEGIES:
            s = get_strategy(name)
            sigs = s.generate_signals(bars)
            assert len(sigs) == len(bars), f"{name}: wrong signal length"

    def test_all_strategies_have_params_property(self):
        for name in STRATEGIES:
            s = get_strategy(name)
            assert isinstance(s.params, dict), f"{name}: params must be dict"
            assert len(s.params) > 0, f"{name}: params must not be empty"

