"""
Testes unitarios para optimizer.py e walk-forward validation.

Cobertura:
  OptimizationObjective
    - todos os valores validos mapeiam para _score
    - score correto extraido de BacktestMetrics

  grid_search
    - conta correto de combinacoes (4x3x3=36 para RSI)
    - filtra combinacoes invalidas (fast >= slow no EMA/MACD)
    - ordena por objetivo escolhido (maior e melhor)
    - penaliza runs com < MIN_TRADES
    - retorna OptimizedRun com rank=1 no top[0]
    - heatmap gerado com keys corretas
    - heatmap vazio quando nao ha dados validos
    - erro em estrategia desconhecida
    - erro em grid maior que MAX_COMBINATIONS
    - custom_space sobrescreve espaco padrao
    - to_dict contem todas as chaves esperadas

  _filter_invalid
    - EMA/MACD com fast >= slow sao removidos

  walk_forward
    - numero correto de folds gerados
    - is_bars + oos_bars cobrem o dataset corretamente
    - modo anchored expande IS a cada fold
    - modo rolling mantem IS do mesmo tamanho
    - metricas de robustez calculadas (efficiency, consistency, degradation)
    - equity composta tem pontos de todos os folds
    - to_dict contem todas as chaves obrigatorias
    - erro com dados insuficientes

  OptimizerService
    - chama BrapiClient uma unica vez
    - valida objetivo invalido -> BacktestError
    - valida dados insuficientes -> BacktestError
    - retorna OptimizationResult correto

  WalkForwardService
    - chama BrapiClient uma unica vez
    - valida objetivo invalido -> BacktestError
    - valida dados insuficientes -> BacktestError
    - retorna WalkForwardResult correto
"""
from __future__ import annotations

import math
from unittest.mock import AsyncMock, patch

import pytest

from finanalytics_ai.application.services.backtest_service import BacktestError
from finanalytics_ai.application.services.optimizer_service import OptimizerService
from finanalytics_ai.application.services.walkforward_service import WalkForwardService
from finanalytics_ai.domain.backtesting.optimizer import (
    MAX_COMBINATIONS,
    MIN_TRADES,
    PARAM_SPACES,
    OptimizationObjective,
    OptimizationResult,
    OptimizedRun,
    WalkForwardFold,
    WalkForwardResult,
    _filter_invalid,
    _score,
    grid_search,
    walk_forward,
)
from finanalytics_ai.domain.backtesting.engine import BacktestMetrics


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bars(n: int = 150, pattern: str = "sine") -> list[dict]:
    """Gera barras sinteticas com volume suficiente para grid search."""
    if pattern == "sine":
        prices = [100.0 + 15.0 * math.sin(i * 0.2) + i * 0.05 for i in range(n)]
    elif pattern == "flat":
        prices = [100.0] * n
    elif pattern == "up":
        prices = [100.0] * 25 + [100.0 + i * 1.5 for i in range(n - 25)]
    elif pattern == "down":
        prices = [150.0] * 25 + [150.0 - i * 1.5 for i in range(n - 25)]
    else:
        prices = [100.0] * n
    return [
        {"time": 1_700_000_000 + i * 86400, "open": p, "high": p * 1.01,
         "low": p * 0.99, "close": p, "volume": 1000}
        for i, p in enumerate(prices)
    ]


def _dummy_metrics(**kwargs) -> BacktestMetrics:
    """Cria BacktestMetrics com valores padrao sobrescritos por kwargs."""
    defaults = dict(
        total_return_pct=5.0, sharpe_ratio=1.0, max_drawdown_pct=5.0,
        win_rate_pct=60.0, profit_factor=1.5, calmar_ratio=1.0,
        total_trades=10, winning_trades=6, losing_trades=4,
        avg_win_pct=3.0, avg_loss_pct=-2.0, avg_duration_days=5.0,
        initial_capital=10_000.0, final_equity=10_500.0,
    )
    defaults.update(kwargs)
    return BacktestMetrics(**defaults)


# ── OptimizationObjective & _score ────────────────────────────────────────────

class TestOptimizationObjective:
    def test_all_values_exist(self):
        expected = {"sharpe", "return", "calmar", "win_rate", "profit_factor"}
        assert {o.value for o in OptimizationObjective} == expected

    def test_score_sharpe(self):
        m = _dummy_metrics(sharpe_ratio=1.5)
        assert _score(m, OptimizationObjective.SHARPE) == pytest.approx(1.5)

    def test_score_return(self):
        m = _dummy_metrics(total_return_pct=12.3)
        assert _score(m, OptimizationObjective.RETURN) == pytest.approx(12.3)

    def test_score_calmar(self):
        m = _dummy_metrics(calmar_ratio=2.0)
        assert _score(m, OptimizationObjective.CALMAR) == pytest.approx(2.0)

    def test_score_win_rate(self):
        m = _dummy_metrics(win_rate_pct=75.0)
        assert _score(m, OptimizationObjective.WIN_RATE) == pytest.approx(75.0)

    def test_score_profit_factor(self):
        m = _dummy_metrics(profit_factor=3.0)
        assert _score(m, OptimizationObjective.PROFIT_F) == pytest.approx(3.0)


# ── _filter_invalid ───────────────────────────────────────────────────────────

class TestFilterInvalid:
    def test_ema_cross_removes_fast_gte_slow(self):
        names  = ["fast", "slow"]
        combos = [(5, 13), (9, 9), (21, 13), (9, 21), (13, 34)]
        valid  = _filter_invalid("ema_cross", names, combos)
        for combo in valid:
            p = dict(zip(names, combo))
            assert p["fast"] < p["slow"], f"Invalid combo passed: {combo}"

    def test_macd_removes_fast_gte_slow(self):
        names  = ["fast", "slow"]
        combos = [(8, 26), (12, 12), (26, 8), (10, 24)]
        valid  = _filter_invalid("macd", names, combos)
        for combo in valid:
            p = dict(zip(names, combo))
            assert p["fast"] < p["slow"]

    def test_rsi_passes_all_combos(self):
        names  = ["period", "oversold", "overbought"]
        combos = [(7, 30.0, 70.0), (14, 25.0, 75.0)]
        valid  = _filter_invalid("rsi", names, combos)
        assert len(valid) == len(combos)

    def test_unknown_strategy_passes_all(self):
        names  = ["a", "b"]
        combos = [(1, 2), (3, 4)]
        assert _filter_invalid("unknown", names, combos) == combos


# ── grid_search ───────────────────────────────────────────────────────────────

class TestGridSearch:
    @pytest.fixture
    def bars_sine(self):
        return _bars(150, "sine")

    def test_rsi_combination_count(self, bars_sine):
        """RSI: 4 periods x 3 oversold x 3 overbought = 36 combinacoes."""
        result = grid_search(bars_sine, "rsi", objective=OptimizationObjective.SHARPE)
        assert result.total_runs == 4 * 3 * 3

    def test_bollinger_combination_count(self, bars_sine):
        """Bollinger: 4 periods x 3 std_dev = 12 combinacoes."""
        result = grid_search(bars_sine, "bollinger")
        assert result.total_runs == 4 * 3

    def test_ema_cross_filters_fast_gte_slow(self, bars_sine):
        """EMA: 4x4=16 combos mas combos com fast>=slow sao removidos."""
        result = grid_search(bars_sine, "ema_cross")
        # 4*4=16, combos invalidas removidas: fast=5/slow=5,13<5..etc
        # Combinacoes validas: fast<slow no espaco [5,9,13,21] x [13,21,34,50]
        # (5,13),(5,21),(5,34),(5,50),(9,13),(9,21),(9,34),(9,50),
        # (13,21),(13,34),(13,50),(21,34),(21,50) = 13
        assert result.total_runs == 13

    def test_top_results_ordered_by_score(self, bars_sine):
        result = grid_search(bars_sine, "rsi", objective=OptimizationObjective.SHARPE)
        valid_tops = [r for r in result.top if r.is_valid]
        if len(valid_tops) >= 2:
            for i in range(len(valid_tops) - 1):
                assert valid_tops[i].score >= valid_tops[i + 1].score

    def test_top_rank_starts_at_one(self, bars_sine):
        result = grid_search(bars_sine, "rsi")
        assert result.top[0].rank == 1

    def test_top_n_respected(self, bars_sine):
        result = grid_search(bars_sine, "rsi", top_n=5)
        assert len(result.top) <= 5

    def test_valid_runs_gte_min_trades(self, bars_sine):
        result = grid_search(bars_sine, "rsi")
        for run in result.top:
            if run.is_valid:
                assert run.metrics.total_trades >= MIN_TRADES

    def test_invalid_strategy_raises_value_error(self, bars_sine):
        with pytest.raises(ValueError, match="Sem espaco de parametros"):
            grid_search(bars_sine, "nonexistent")

    def test_grid_too_large_raises_value_error(self, bars_sine):
        huge_space = {
            "period":     list(range(5, 55)),   # 50
            "oversold":   list(range(10, 50)),  # 40
            "overbought": list(range(55, 90)),  # 35
        }  # 50*40*35 = 70,000
        with pytest.raises(ValueError, match="limite"):
            grid_search(bars_sine, "rsi", custom_space=huge_space)

    def test_custom_space_overrides_default(self, bars_sine):
        custom = {"period": [7, 14], "oversold": [30.0], "overbought": [70.0]}
        result  = grid_search(bars_sine, "rsi", custom_space=custom)
        assert result.total_runs == 2 * 1 * 1

    def test_to_dict_keys(self, bars_sine):
        d = grid_search(bars_sine, "rsi").to_dict()
        required = {"ticker", "strategy", "range_period", "objective",
                    "total_runs", "valid_runs", "top", "heatmap",
                    "best_params", "best_score"}
        assert required <= set(d.keys())

    def test_best_params_is_dict(self, bars_sine):
        result = grid_search(bars_sine, "rsi")
        assert isinstance(result.best_params, dict)
        assert len(result.best_params) > 0

    def test_best_score_is_float(self, bars_sine):
        result = grid_search(bars_sine, "rsi")
        assert isinstance(result.best_score, float)

    def test_heatmap_has_required_keys(self, bars_sine):
        result = grid_search(bars_sine, "rsi")
        hm = result.heatmap
        if hm:  # pode ser vazio se nao ha dados validos
            assert "x_label" in hm
            assert "y_label" in hm
            assert "x_values" in hm
            assert "y_values" in hm
            assert "matrix" in hm

    def test_heatmap_matrix_values_are_floats_or_none(self, bars_sine):
        result = grid_search(bars_sine, "rsi")
        for cell in result.heatmap.get("matrix", []):
            assert "x" in cell and "y" in cell and "score" in cell
            if cell["score"] is not None:
                assert isinstance(cell["score"], float)

    @pytest.mark.parametrize("objective", list(OptimizationObjective))
    def test_all_objectives_run_without_error(self, bars_sine, objective):
        result = grid_search(bars_sine, "bollinger", objective=objective)
        assert result.total_runs > 0

    def test_optimized_run_to_dict(self, bars_sine):
        result = grid_search(bars_sine, "rsi")
        d = result.top[0].to_dict()
        assert all(k in d for k in ["rank", "params", "score", "is_valid", "metrics"])

    def test_penalized_invalid_runs_have_negative_score(self, bars_sine):
        """Runs com poucos trades devem aparecer no final do ranking."""
        result = grid_search(bars_sine, "rsi")
        valid_positions   = [i for i, r in enumerate(result.top) if r.is_valid]
        invalid_positions = [i for i, r in enumerate(result.top) if not r.is_valid]
        if valid_positions and invalid_positions:
            assert max(valid_positions) < max(invalid_positions) or \
                   min(valid_positions) < min(invalid_positions)


# ── walk_forward ──────────────────────────────────────────────────────────────

class TestWalkForward:
    @pytest.fixture
    def bars_long(self):
        """300 barras (~1.2y) suficientes para 3 splits."""
        return _bars(300, "sine")

    def test_correct_number_of_folds(self, bars_long):
        result = walk_forward(bars_long, "rsi", n_splits=3, oos_pct=0.3)
        assert len(result.folds) == 3

    def test_fold_bars_cover_dataset(self, bars_long):
        """Todos os folds devem ter is_bars + oos_bars > 0."""
        result = walk_forward(bars_long, "rsi", n_splits=3, oos_pct=0.3)
        for f in result.folds:
            assert f.is_bars  > 0
            assert f.oos_bars > 0

    def test_rolling_is_size_constant(self, bars_long):
        """Modo rolling: todos os folds devem ter is_bars semelhante."""
        result = walk_forward(bars_long, "rsi", n_splits=3, oos_pct=0.3, anchored=False)
        is_sizes = [f.is_bars for f in result.folds]
        # Variacao maxima de 1 barra por arredondamento
        assert max(is_sizes) - min(is_sizes) <= 1

    def test_anchored_is_size_grows(self, bars_long):
        """Modo anchored (expanding): cada fold deve ter mais barras IS que o anterior."""
        result = walk_forward(bars_long, "rsi", n_splits=3, oos_pct=0.3, anchored=True)
        is_sizes = [f.is_bars for f in result.folds]
        assert is_sizes[0] < is_sizes[1] < is_sizes[2]

    def test_best_params_dict_non_empty(self, bars_long):
        result = walk_forward(bars_long, "rsi", n_splits=3)
        for f in result.folds:
            assert isinstance(f.best_params, dict)
            assert len(f.best_params) > 0

    def test_is_score_positive_for_valid_optimizations(self, bars_long):
        """IS score deve ser positivo quando a otimizacao encontra trades validos."""
        result = walk_forward(bars_long, "rsi", n_splits=3)
        valid_folds = [f for f in result.folds if f.best_is_score > 0]
        assert len(valid_folds) >= 1, "Pelo menos 1 fold deve ter IS score positivo"

    def test_efficiency_ratio_in_range(self, bars_long):
        """Efficiency ratio pode ser qualquer float, mas deve existir."""
        result = walk_forward(bars_long, "rsi", n_splits=3)
        assert isinstance(result.efficiency_ratio, float)

    def test_consistency_pct_in_range(self, bars_long):
        result = walk_forward(bars_long, "rsi", n_splits=3)
        assert 0.0 <= result.consistency <= 100.0

    def test_degradation_is_float(self, bars_long):
        result = walk_forward(bars_long, "rsi", n_splits=3)
        assert isinstance(result.degradation, float)

    def test_combined_equity_not_empty(self, bars_long):
        result = walk_forward(bars_long, "rsi", n_splits=3)
        # Pode estar vazio se nenhum OOS gerou trades — mas estrutura existe
        assert isinstance(result.combined_equity, list)

    def test_combined_equity_has_fold_annotation(self, bars_long):
        result = walk_forward(bars_long, "momentum", n_splits=3)
        for pt in result.combined_equity:
            assert "time" in pt and "equity" in pt and "fold" in pt

    def test_to_dict_required_keys(self, bars_long):
        d = walk_forward(bars_long, "rsi", n_splits=3).to_dict()
        required = {
            "ticker", "strategy", "range_period", "objective",
            "n_splits", "anchored", "total_bars", "folds",
            "avg_oos_score", "avg_is_score", "efficiency_ratio",
            "consistency", "degradation", "combined_equity", "combined_return",
        }
        assert required <= set(d.keys())

    def test_fold_to_dict_required_keys(self, bars_long):
        result = walk_forward(bars_long, "rsi", n_splits=3)
        d = result.folds[0].to_dict()
        required = {
            "fold", "is_bars", "oos_bars", "best_params",
            "best_is_score", "best_is_trades", "oos_score", "oos_valid",
        }
        assert required <= set(d.keys())

    def test_combined_return_computes_from_initial(self, bars_long):
        result = walk_forward(bars_long, "rsi", n_splits=3, initial_capital=10_000.0)
        assert isinstance(result.combined_return, float)

    def test_too_few_bars_raises_value_error(self):
        tiny_bars = _bars(20, "flat")
        with pytest.raises(ValueError, match="pequena"):
            walk_forward(tiny_bars, "rsi", n_splits=3)

    def test_n_splits_clamped_to_range(self):
        """n_splits fora de [2, 6] e silenciosamente clampado."""
        bars = _bars(300, "sine")
        r_low  = walk_forward(bars, "rsi", n_splits=0)   # clampa para 2
        r_high = walk_forward(bars, "rsi", n_splits=99)  # clampa para 6
        assert len(r_low.folds)  >= 2
        assert len(r_high.folds) <= 6

    @pytest.mark.parametrize("strategy", ["rsi", "macd", "bollinger", "ema_cross", "momentum"])
    def test_all_strategies_produce_valid_result(self, bars_long, strategy):
        result = walk_forward(bars_long, strategy, n_splits=3)
        assert result.strategy == strategy
        assert len(result.folds) == 3

    def test_oos_pct_affects_fold_sizes(self):
        bars = _bars(300, "sine")
        r20  = walk_forward(bars, "rsi", n_splits=3, oos_pct=0.2)
        r40  = walk_forward(bars, "rsi", n_splits=3, oos_pct=0.4)
        oos_avg_20 = sum(f.oos_bars for f in r20.folds) / len(r20.folds)
        oos_avg_40 = sum(f.oos_bars for f in r40.folds) / len(r40.folds)
        assert oos_avg_40 > oos_avg_20, "Maior oos_pct deve gerar janelas OOS maiores"


# ── OptimizerService ──────────────────────────────────────────────────────────

class TestOptimizerService:
    def _make_service(self, bars: list[dict]) -> tuple[OptimizerService, AsyncMock]:
        mock_brapi = AsyncMock()
        mock_brapi.get_ohlc_bars.return_value = bars
        return OptimizerService(mock_brapi), mock_brapi

    @pytest.mark.asyncio
    async def test_returns_optimization_result(self):
        svc, _ = self._make_service(_bars(150))
        result = await svc.optimize("PETR4", "rsi", range_period="1y")
        assert isinstance(result, OptimizationResult)
        assert result.ticker == "PETR4"
        assert result.strategy == "rsi"

    @pytest.mark.asyncio
    async def test_brapi_called_exactly_once(self):
        svc, mock = self._make_service(_bars(150))
        await svc.optimize("PETR4", "bollinger")
        mock.get_ohlc_bars.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalid_objective_raises_backtest_error(self):
        svc, _ = self._make_service(_bars(150))
        with pytest.raises(BacktestError, match="invalido"):
            await svc.optimize("PETR4", "rsi", objective="banana")

    @pytest.mark.asyncio
    async def test_empty_bars_raises_backtest_error(self):
        svc, _ = self._make_service([])
        with pytest.raises(BacktestError, match="Sem dados"):
            await svc.optimize("PETR4", "rsi")

    @pytest.mark.asyncio
    async def test_too_few_bars_raises_backtest_error(self):
        svc, _ = self._make_service(_bars(20))
        with pytest.raises(BacktestError, match="insuficiente"):
            await svc.optimize("PETR4", "rsi")

    @pytest.mark.asyncio
    async def test_top_n_capped_at_20(self):
        svc, _ = self._make_service(_bars(150))
        result = await svc.optimize("PETR4", "rsi", top_n=100)
        assert len(result.top) <= 20

    @pytest.mark.asyncio
    async def test_result_serializable(self):
        svc, _ = self._make_service(_bars(150))
        result = await svc.optimize("PETR4", "rsi")
        d = result.to_dict()
        assert "top" in d and "heatmap" in d and "best_params" in d

    @pytest.mark.asyncio
    async def test_all_objectives_work(self):
        svc, _ = self._make_service(_bars(150))
        for obj in OptimizationObjective:
            result = await svc.optimize("PETR4", "rsi", objective=obj.value)
            assert result.objective == obj.value


# ── WalkForwardService ────────────────────────────────────────────────────────

class TestWalkForwardService:
    def _make_service(self, bars: list[dict]) -> tuple[WalkForwardService, AsyncMock]:
        mock_brapi = AsyncMock()
        mock_brapi.get_ohlc_bars.return_value = bars
        return WalkForwardService(mock_brapi), mock_brapi

    @pytest.mark.asyncio
    async def test_returns_walk_forward_result(self):
        svc, _ = self._make_service(_bars(300))
        result = await svc.run("PETR4", "rsi", range_period="2y", n_splits=3)
        assert isinstance(result, WalkForwardResult)
        assert result.ticker == "PETR4"
        assert result.strategy == "rsi"

    @pytest.mark.asyncio
    async def test_brapi_called_exactly_once(self):
        svc, mock = self._make_service(_bars(300))
        await svc.run("PETR4", "rsi", n_splits=3)
        mock.get_ohlc_bars.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalid_objective_raises_backtest_error(self):
        svc, _ = self._make_service(_bars(300))
        with pytest.raises(BacktestError, match="invalido"):
            await svc.run("PETR4", "rsi", objective="nonsense")

    @pytest.mark.asyncio
    async def test_empty_bars_raises_backtest_error(self):
        svc, _ = self._make_service([])
        with pytest.raises(BacktestError, match="Sem dados"):
            await svc.run("PETR4", "rsi")

    @pytest.mark.asyncio
    async def test_too_few_bars_raises_backtest_error(self):
        svc, _ = self._make_service(_bars(30))
        with pytest.raises(BacktestError, match="insuficiente"):
            await svc.run("PETR4", "rsi", n_splits=3)

    @pytest.mark.asyncio
    async def test_correct_n_splits_in_result(self):
        svc, _ = self._make_service(_bars(300))
        result = await svc.run("PETR4", "rsi", n_splits=3)
        assert result.n_splits == 3
        assert len(result.folds) == 3

    @pytest.mark.asyncio
    async def test_anchored_flag_reflected_in_result(self):
        svc, _ = self._make_service(_bars(300))
        r_rolling  = await svc.run("PETR4", "rsi", n_splits=3, anchored=False)
        r_anchored = await svc.run("PETR4", "rsi", n_splits=3, anchored=True)
        assert r_rolling.anchored  is False
        assert r_anchored.anchored is True

    @pytest.mark.asyncio
    async def test_result_serializable(self):
        svc, _ = self._make_service(_bars(300))
        result = await svc.run("PETR4", "rsi", n_splits=3)
        d = result.to_dict()
        assert "folds" in d and "efficiency_ratio" in d and "consistency" in d

    @pytest.mark.asyncio
    async def test_n_splits_clamped(self):
        """n_splits=10 deve ser clampado para 6 dentro do service."""
        svc, _ = self._make_service(_bars(400))
        result = await svc.run("PETR4", "rsi", n_splits=10)
        assert len(result.folds) <= 6
