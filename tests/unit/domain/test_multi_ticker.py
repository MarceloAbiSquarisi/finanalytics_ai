"""
Testes unitarios para o modulo comparativo multi-ticker.

Cobertura:
  build_multi_ticker_result
    - todos sucesso: rankings, best/worst ticker, scores normalizados
    - todos falha: result vazio com erros
    - mix sucesso+falha: erros em errors, sucessos em rankings
    - ticker unico: score_pct == 100.0
    - ranking ordenado por score desc
    - hit_rate calcula % de tickers validos
    - score_std zero quando todos scores iguais
    - score_pct normalizado 0-100 relativo ao maximo

  _find_consensus_params
    - valor mais frequente vence
    - empate: maior valor vence
    - lista vazia retorna {}
    - keys de todos os dicts sao preservadas

  MultiTickerService
    - brapi chamado N vezes (um por ticker)
    - semaforo limita concorrencia (mock verifica)
    - resultado correto com tickers validos
    - falha em 1 ticker nao cancela os outros
    - lista vazia raises BacktestError
    - mais de MAX_TICKERS raises BacktestError
    - objetivo invalido raises BacktestError
    - tickers duplicados sao normalizados (uppercase)
"""
from __future__ import annotations

import asyncio
import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from finanalytics_ai.application.services.backtest_service import BacktestError
from finanalytics_ai.application.services.multi_ticker_service import (
    MAX_CONCURRENT,
    MultiTickerService,
)
from finanalytics_ai.domain.backtesting.multi_ticker import (
    MAX_TICKERS,
    MultiTickerResult,
    TickerRanking,
    _find_consensus_params,
    build_multi_ticker_result,
)
from finanalytics_ai.domain.backtesting.optimizer import (
    OptimizationObjective,
    OptimizationResult,
    OptimizedRun,
)
from finanalytics_ai.domain.backtesting.engine import BacktestMetrics


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dummy_metrics(**kw) -> BacktestMetrics:
    defaults = dict(
        total_return_pct=10.0, sharpe_ratio=1.2, max_drawdown_pct=8.0,
        win_rate_pct=60.0, profit_factor=1.8, calmar_ratio=1.5,
        total_trades=12, winning_trades=7, losing_trades=5,
        avg_win_pct=4.0, avg_loss_pct=-2.5, avg_duration_days=6.0,
        initial_capital=10_000.0, final_equity=11_000.0,
    )
    defaults.update(kw)
    return BacktestMetrics(**defaults)


def _dummy_opt_result(
    ticker:      str   = "PETR4",
    best_score:  float = 1.0,
    valid_runs:  int   = 5,
    total_runs:  int   = 36,
    best_params: dict  | None = None,
) -> OptimizationResult:
    """
    Cria OptimizationResult minimo sem executar grid search.

    Nota: best_params e best_score sao @property derivados de top[0],
    nao campos do construtor — por isso so passamos 'top'.
    """
    params = best_params or {"period": 14, "oversold": 30.0, "overbought": 70.0}
    top_run = OptimizedRun(
        rank    = 1,
        params  = params,
        score   = best_score,
        metrics = _dummy_metrics(sharpe_ratio=best_score),
        is_valid= valid_runs > 0,
    )
    return OptimizationResult(
        ticker       = ticker,
        strategy     = "rsi",
        range_period = "1y",
        objective    = "sharpe",
        total_runs   = total_runs,
        valid_runs   = valid_runs,
        top          = [top_run],
        heatmap      = {},
    )


def _bars(n: int = 150) -> list[dict]:
    return [
        {"time": 1700000000 + i * 86400, "open": 100.0,
         "high": 101.0, "low": 99.0, "close": 100.0 + i * 0.05, "volume": 1000}
        for i in range(n)
    ]


# ── build_multi_ticker_result ─────────────────────────────────────────────────

class TestBuildMultiTickerResult:
    def test_all_success_produces_rankings(self):
        results = [
            ("PETR4", _dummy_opt_result("PETR4", best_score=1.5)),
            ("VALE3", _dummy_opt_result("VALE3", best_score=0.8)),
            ("ITUB4", _dummy_opt_result("ITUB4", best_score=1.1)),
        ]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        assert len(r.rankings) == 3
        assert len(r.errors)   == 0

    def test_rankings_ordered_by_score_desc(self):
        results = [
            ("PETR4", _dummy_opt_result("PETR4", best_score=0.5)),
            ("VALE3", _dummy_opt_result("VALE3", best_score=2.0)),
            ("ITUB4", _dummy_opt_result("ITUB4", best_score=1.0)),
        ]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        assert r.rankings[0].ticker == "VALE3"
        assert r.rankings[1].ticker == "ITUB4"
        assert r.rankings[2].ticker == "PETR4"

    def test_rank_numbers_start_at_one(self):
        results = [
            ("A", _dummy_opt_result("A", 1.0)),
            ("B", _dummy_opt_result("B", 2.0)),
        ]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        assert r.rankings[0].rank == 1
        assert r.rankings[1].rank == 2

    def test_best_ticker_is_highest_score(self):
        results = [
            ("LOW",  _dummy_opt_result("LOW",  0.3)),
            ("HIGH", _dummy_opt_result("HIGH", 3.0)),
            ("MID",  _dummy_opt_result("MID",  1.5)),
        ]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        assert r.best_ticker == "HIGH"

    def test_worst_ticker_is_lowest_score(self):
        results = [
            ("LOW",  _dummy_opt_result("LOW",  0.3)),
            ("HIGH", _dummy_opt_result("HIGH", 3.0)),
        ]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        assert r.worst_ticker == "LOW"

    def test_single_ticker_score_pct_is_100(self):
        results = [("PETR4", _dummy_opt_result("PETR4", best_score=1.5))]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        assert r.rankings[0].score_pct == pytest.approx(100.0)

    def test_top_ticker_score_pct_is_100(self):
        results = [
            ("PETR4", _dummy_opt_result("PETR4", best_score=2.0)),
            ("VALE3", _dummy_opt_result("VALE3", best_score=1.0)),
        ]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        assert r.rankings[0].score_pct == pytest.approx(100.0)

    def test_score_pct_in_range_0_100(self):
        results = [
            ("A", _dummy_opt_result("A", 5.0)),
            ("B", _dummy_opt_result("B", 3.0)),
            ("C", _dummy_opt_result("C", 1.0)),
        ]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        for ranking in r.rankings:
            assert 0.0 <= ranking.score_pct <= 100.0

    def test_hit_rate_all_valid(self):
        results = [
            ("A", _dummy_opt_result("A", valid_runs=5)),
            ("B", _dummy_opt_result("B", valid_runs=3)),
        ]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        assert r.hit_rate == pytest.approx(100.0)

    def test_hit_rate_half_valid(self):
        results = [
            ("A", _dummy_opt_result("A", valid_runs=5)),
            ("B", _dummy_opt_result("B", valid_runs=0)),
        ]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        assert r.hit_rate == pytest.approx(50.0)

    def test_hit_rate_zero_no_valid(self):
        results = [
            ("A", _dummy_opt_result("A", valid_runs=0)),
            ("B", _dummy_opt_result("B", valid_runs=0)),
        ]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        assert r.hit_rate == pytest.approx(0.0)

    def test_avg_score_correct(self):
        results = [
            ("A", _dummy_opt_result("A", best_score=2.0)),
            ("B", _dummy_opt_result("B", best_score=4.0)),
        ]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        assert r.avg_score == pytest.approx(3.0)

    def test_score_std_zero_for_equal_scores(self):
        results = [
            ("A", _dummy_opt_result("A", best_score=1.5)),
            ("B", _dummy_opt_result("B", best_score=1.5)),
        ]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        assert r.score_std == pytest.approx(0.0)

    def test_score_std_positive_for_different_scores(self):
        results = [
            ("A", _dummy_opt_result("A", best_score=1.0)),
            ("B", _dummy_opt_result("B", best_score=3.0)),
        ]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        assert r.score_std > 0

    def test_all_failures_empty_rankings(self):
        results = [
            ("PETR4", ValueError("Sem dados")),
            ("VALE3", RuntimeError("timeout")),
        ]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        assert len(r.rankings) == 0
        assert len(r.errors)   == 2
        assert r.avg_score     == 0.0
        assert r.best_ticker   == ""

    def test_partial_failure_errors_captured(self):
        results = [
            ("PETR4", _dummy_opt_result("PETR4", best_score=1.5)),
            ("XXXX",  ValueError("Ticker invalido")),
            ("VALE3", _dummy_opt_result("VALE3", best_score=0.9)),
        ]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        assert len(r.rankings) == 2
        assert len(r.errors)   == 1
        assert r.errors[0]["ticker"] == "XXXX"
        assert "Ticker invalido" in r.errors[0]["error"]

    def test_tickers_list_includes_all_inputs(self):
        results = [
            ("PETR4", _dummy_opt_result("PETR4")),
            ("VALE3", ValueError("erro")),
        ]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        assert "PETR4" in r.tickers
        assert "VALE3" in r.tickers

    def test_to_dict_required_keys(self):
        results = [("PETR4", _dummy_opt_result("PETR4"))]
        d = build_multi_ticker_result(results, "rsi", "1y", "sharpe").to_dict()
        required = {"strategy", "range_period", "objective", "tickers",
                    "rankings", "avg_score", "score_std", "hit_rate",
                    "best_ticker", "worst_ticker", "consensus_params",
                    "errors", "total_tickers", "failed_tickers"}
        assert required <= set(d.keys())

    def test_ticker_ranking_to_dict_keys(self):
        results = [("PETR4", _dummy_opt_result("PETR4"))]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        d = r.rankings[0].to_dict()
        assert all(k in d for k in ["rank", "ticker", "best_score", "score_pct",
                                     "best_params", "total_runs", "valid_runs",
                                     "top_metrics", "has_valid"])

    def test_has_valid_true_for_valid_runs(self):
        results = [("A", _dummy_opt_result("A", valid_runs=3))]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        assert r.rankings[0].has_valid is True

    def test_has_valid_false_for_zero_valid_runs(self):
        results = [("A", _dummy_opt_result("A", valid_runs=0))]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        assert r.rankings[0].has_valid is False

    def test_consensus_params_populated(self):
        results = [
            ("A", _dummy_opt_result("A", best_params={"period": 14, "oversold": 30.0})),
            ("B", _dummy_opt_result("B", best_params={"period": 14, "oversold": 25.0})),
            ("C", _dummy_opt_result("C", best_params={"period":  7, "oversold": 30.0})),
        ]
        r = build_multi_ticker_result(results, "rsi", "1y", "sharpe")
        # period=14 aparece 2x, period=7 aparece 1x -> consenso=14
        assert r.consensus_params.get("period") == 14
        # oversold=30.0 aparece 2x -> consenso=30.0
        assert r.consensus_params.get("oversold") == pytest.approx(30.0)

    def test_metadata_fields_preserved(self):
        results = [("PETR4", _dummy_opt_result("PETR4"))]
        r = build_multi_ticker_result(results, "macd", "6mo", "return")
        assert r.strategy     == "macd"
        assert r.range_period == "6mo"
        assert r.objective    == "return"


# ── _find_consensus_params ────────────────────────────────────────────────────

class TestFindConsensusParams:
    def test_empty_list_returns_empty(self):
        assert _find_consensus_params([]) == {}

    def test_single_dict_returns_same(self):
        p = {"period": 14, "oversold": 30.0}
        assert _find_consensus_params([p]) == p

    def test_majority_wins(self):
        params = [
            {"period": 14},
            {"period": 14},
            {"period":  7},
        ]
        assert _find_consensus_params(params)["period"] == 14

    def test_tie_broken_by_max(self):
        params = [
            {"period": 7},
            {"period": 14},
        ]
        # empate -> maior vence
        assert _find_consensus_params(params)["period"] == 14

    def test_float_values_work(self):
        params = [
            {"oversold": 30.0},
            {"oversold": 30.0},
            {"oversold": 25.0},
        ]
        assert _find_consensus_params(params)["oversold"] == pytest.approx(30.0)

    def test_missing_key_in_some_dicts(self):
        params = [
            {"a": 1, "b": 2},
            {"a": 1},
            {"a": 3, "b": 2},
        ]
        result = _find_consensus_params(params)
        assert result["a"] == 1   # 1 aparece 2x
        assert result["b"] == 2   # 2 aparece 2x (unico valor)

    def test_all_keys_from_all_dicts(self):
        params = [
            {"x": 1},
            {"y": 2},
        ]
        result = _find_consensus_params(params)
        assert "x" in result
        assert "y" in result


# ── MultiTickerService ────────────────────────────────────────────────────────

class TestMultiTickerService:
    """
    Testa o servico isolado via mock do OptimizerService interno.

    Estrategia: patch em 'optimizer_service.OptimizerService' para controlar
    o que cada ticker retorna sem tocar na BRAPI ou no grid search.
    """

    def _make_svc(self) -> MultiTickerService:
        mock_brapi = AsyncMock()
        return MultiTickerService(mock_brapi)

    def _patch_optimizer(self, svc: MultiTickerService, side_effects: dict):
        """
        side_effects: {ticker: OptimizationResult | Exception}
        """
        async def _fake_optimize(ticker, **kwargs):
            effect = side_effects.get(ticker.upper())
            if isinstance(effect, Exception):
                raise effect
            return effect

        svc._optimizer.optimize = _fake_optimize

    @pytest.mark.asyncio
    async def test_returns_multi_ticker_result(self):
        svc = self._make_svc()
        self._patch_optimizer(svc, {
            "PETR4": _dummy_opt_result("PETR4", best_score=1.5),
            "VALE3": _dummy_opt_result("VALE3", best_score=0.8),
        })
        result = await svc.compare(["PETR4", "VALE3"], "rsi")
        assert isinstance(result, MultiTickerResult)
        assert len(result.rankings) == 2

    @pytest.mark.asyncio
    async def test_best_ticker_correct(self):
        svc = self._make_svc()
        self._patch_optimizer(svc, {
            "PETR4": _dummy_opt_result("PETR4", best_score=2.5),
            "VALE3": _dummy_opt_result("VALE3", best_score=0.5),
        })
        result = await svc.compare(["PETR4", "VALE3"], "rsi")
        assert result.best_ticker == "PETR4"

    @pytest.mark.asyncio
    async def test_failed_ticker_in_errors(self):
        svc = self._make_svc()
        self._patch_optimizer(svc, {
            "PETR4": _dummy_opt_result("PETR4", best_score=1.0),
            "XXXX":  ValueError("Ticker nao encontrado"),
        })
        result = await svc.compare(["PETR4", "XXXX"], "rsi")
        assert len(result.rankings) == 1
        assert len(result.errors)   == 1
        assert result.errors[0]["ticker"] == "XXXX"

    @pytest.mark.asyncio
    async def test_all_tickers_fail_empty_rankings(self):
        svc = self._make_svc()
        self._patch_optimizer(svc, {
            "PETR4": RuntimeError("timeout"),
            "VALE3": RuntimeError("timeout"),
        })
        result = await svc.compare(["PETR4", "VALE3"], "rsi")
        assert len(result.rankings) == 0
        assert len(result.errors)   == 2

    @pytest.mark.asyncio
    async def test_empty_tickers_raises_backtest_error(self):
        svc = self._make_svc()
        with pytest.raises(BacktestError, match="menos 1 ticker"):
            await svc.compare([], "rsi")

    @pytest.mark.asyncio
    async def test_whitespace_only_tickers_raises(self):
        svc = self._make_svc()
        with pytest.raises(BacktestError, match="menos 1 ticker"):
            await svc.compare(["  ", ""], "rsi")

    @pytest.mark.asyncio
    async def test_too_many_tickers_raises(self):
        svc = self._make_svc()
        too_many = [f"T{i}" for i in range(MAX_TICKERS + 1)]
        with pytest.raises(BacktestError, match=str(MAX_TICKERS)):
            await svc.compare(too_many, "rsi")

    @pytest.mark.asyncio
    async def test_invalid_objective_raises(self):
        svc = self._make_svc()
        with pytest.raises(BacktestError, match="invalido"):
            await svc.compare(["PETR4"], "rsi", objective="banana")

    @pytest.mark.asyncio
    async def test_tickers_normalized_to_uppercase(self):
        svc = self._make_svc()
        seen_tickers: list[str] = []

        async def _capture(ticker, **kw):
            seen_tickers.append(ticker)
            return _dummy_opt_result(ticker, 1.0)

        svc._optimizer.optimize = _capture
        await svc.compare(["petr4", "vale3", "itub4"], "rsi")
        assert all(t.isupper() for t in seen_tickers)

    @pytest.mark.asyncio
    async def test_tickers_whitespace_stripped(self):
        svc = self._make_svc()
        seen: list[str] = []

        async def _capture(ticker, **kw):
            seen.append(ticker)
            return _dummy_opt_result(ticker, 1.0)

        svc._optimizer.optimize = _capture
        await svc.compare(["  PETR4  ", " VALE3"], "rsi")
        assert all(" " not in t for t in seen)

    @pytest.mark.asyncio
    async def test_max_concurrent_semaphore_respected(self):
        """
        Verifica que nunca mais de MAX_CONCURRENT otimizacoes correm simultaneamente.
        """
        active   = [0]
        max_seen = [0]
        lock     = asyncio.Lock()

        async def _slow_optimize(ticker, **kw):
            async with lock:
                active[0] += 1
                if active[0] > max_seen[0]:
                    max_seen[0] = active[0]
            await asyncio.sleep(0.01)
            async with lock:
                active[0] -= 1
            return _dummy_opt_result(ticker, 1.0)

        svc = self._make_svc()
        svc._optimizer.optimize = _slow_optimize

        tickers = [f"T{i}" for i in range(8)]
        await svc.compare(tickers, "rsi")

        assert max_seen[0] <= MAX_CONCURRENT, (
            f"Max simultaneous: {max_seen[0]}, expected <= {MAX_CONCURRENT}"
        )

    @pytest.mark.asyncio
    async def test_result_metadata_correct(self):
        svc = self._make_svc()
        self._patch_optimizer(svc, {
            "PETR4": _dummy_opt_result("PETR4", best_score=1.0),
        })
        result = await svc.compare(["PETR4"], "bollinger", range_period="6mo", objective="return")
        assert result.strategy     == "bollinger"
        assert result.range_period == "6mo"
        assert result.objective    == "return"

    @pytest.mark.asyncio
    async def test_passes_params_to_optimizer(self):
        svc = self._make_svc()
        received: dict = {}

        async def _capture(ticker, **kw):
            received.update(kw)
            return _dummy_opt_result(ticker, 1.0)

        svc._optimizer.optimize = _capture
        await svc.compare(
            ["PETR4"], "rsi",
            range_period="2y", initial_capital=5_000.0,
            commission_pct=0.002, objective="calmar",
        )
        assert received["range_period"]    == "2y"
        assert received["initial_capital"] == pytest.approx(5_000.0)
        assert received["commission_pct"]  == pytest.approx(0.002)
        assert received["objective"]       == "calmar"

    @pytest.mark.asyncio
    async def test_single_ticker_returns_valid_result(self):
        svc = self._make_svc()
        self._patch_optimizer(svc, {
            "WEGE3": _dummy_opt_result("WEGE3", best_score=2.0),
        })
        result = await svc.compare(["WEGE3"], "rsi")
        assert len(result.rankings)     == 1
        assert result.rankings[0].rank  == 1
        assert result.hit_rate          == pytest.approx(100.0)
        assert result.best_ticker       == "WEGE3"
        assert result.worst_ticker      == "WEGE3"

    @pytest.mark.asyncio
    async def test_result_serializable(self):
        svc = self._make_svc()
        self._patch_optimizer(svc, {
            "PETR4": _dummy_opt_result("PETR4", best_score=1.5),
            "VALE3": _dummy_opt_result("VALE3", best_score=0.8),
        })
        result = await svc.compare(["PETR4", "VALE3"], "rsi")
        d = result.to_dict()
        assert "rankings"  in d
        assert "avg_score" in d
        assert "hit_rate"  in d
        assert "errors"    in d
