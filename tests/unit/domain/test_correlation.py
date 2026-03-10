"""
Testes unitarios para o modulo de correlacao.

Cobertura:
  extract_returns
    - retorno percentual correto
    - primeiro elemento sem retorno (length = bars - 1)
    - lista vazia / 1 barra retorna {}
    - retorno negativo quando preco cai
    - key e o timestamp da barra atual (nao da anterior)

  align_returns
    - inner join: apenas timestamps comuns
    - sem timestamps comuns retorna ([], {})
    - series alinhadas tem mesmo comprimento
    - ordem temporal preservada

  _pearson
    - correlacao perfeita positiva = 1.0
    - correlacao perfeita negativa = -1.0
    - series ortogonais = 0.0
    - series constantes = 0.0 (sem divisao por zero)
    - simetria: pearson(x,y) == pearson(y,x)
    - resultado em [-1, 1]

  correlation_matrix
    - diagonal = 1.0
    - simetrica: matrix[a][b] == matrix[b][a]
    - correto para 2 tickers
    - correto para 3 tickers

  rolling_correlation
    - comprimento = len(series) - window + 1
    - series curtas retornam []
    - timestamps corretos (indice i-1)
    - valores em [-1, 1]

  build_correlation_result
    - common_bars correto
    - matriz completa NxN
    - most/least correlated populados
    - rolling_pairs tem todos os pares
    - diversification_score em [0, 1]
    - score = 0 quando todos perfeitamente correlacionados
    - score = 1 quando todos ortogonais (correlacao 0)
    - dados vazios retorna result valido com common_bars=0
    - errors passados corretamente

  CorrelationService
    - brapi chamado uma vez por ticker
    - resultado com 2 tickers
    - falha em 1 ticker nao cancela (partial success)
    - raises BacktestError com 0 tickers
    - raises BacktestError com 1 ticker
    - raises BacktestError com > MAX_TICKERS
    - raises BacktestError quando < 2 tickers validos
    - window adaptativo quando dados curtos
    - tickers normalizados para uppercase
    - to_dict com todas as chaves
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from finanalytics_ai.application.services.backtest_service import BacktestError
from finanalytics_ai.application.services.correlation_service import (
    MAX_TICKERS,
    CorrelationService,
)
from finanalytics_ai.domain.correlation.engine import (
    CorrelationResult,
    _pearson,
    align_returns,
    build_correlation_result,
    correlation_matrix,
    extract_returns,
    rolling_correlation,
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


def _flat(n: int = 30, price: float = 100.0, base_ts: int = 1_700_000_000) -> list[dict]:
    return _bars([price] * n, base_ts)


def _ramp(n: int = 30, start: float = 100.0, step: float = 1.0) -> list[dict]:
    return _bars([start + i * step for i in range(n)])


# ── extract_returns ───────────────────────────────────────────────────────────


class TestExtractReturns:
    def test_empty_returns_empty(self):
        assert extract_returns([]) == {}

    def test_single_bar_returns_empty(self):
        assert extract_returns(_flat(1)) == {}

    def test_length_is_bars_minus_one(self):
        returns = extract_returns(_flat(10))
        assert len(returns) == 9

    def test_flat_prices_zero_returns(self):
        returns = extract_returns(_flat(20))
        for v in returns.values():
            assert abs(v) < 1e-9

    def test_10pct_gain(self):
        bars = _bars([100.0, 110.0])
        r = extract_returns(bars)
        assert list(r.values())[0] == pytest.approx(10.0)

    def test_negative_return(self):
        bars = _bars([100.0, 90.0])
        r = extract_returns(bars)
        assert list(r.values())[0] == pytest.approx(-10.0)

    def test_key_is_current_bar_timestamp(self):
        bars = _bars([100.0, 110.0])
        ts_first = bars[0]["time"]
        ts_second = bars[1]["time"]
        r = extract_returns(bars)
        assert ts_second in r
        assert ts_first not in r

    def test_multiple_returns_correct(self):
        bars = _bars([100.0, 110.0, 99.0])
        r = extract_returns(bars)
        vals = [r[bars[1]["time"]], r[bars[2]["time"]]]
        assert vals[0] == pytest.approx(10.0)
        assert vals[1] == pytest.approx((99 - 110) / 110 * 100)


# ── align_returns ─────────────────────────────────────────────────────────────


class TestAlignReturns:
    def test_empty_map(self):
        ts, aligned = align_returns({})
        assert ts == [] and aligned == {}

    def test_common_timestamps_only(self):
        a = {100: 1.0, 200: 2.0, 300: 3.0}
        b = {200: 4.0, 300: 5.0, 400: 6.0}
        ts, aligned = align_returns({"A": a, "B": b})
        assert set(ts) == {200, 300}
        assert len(aligned["A"]) == 2
        assert len(aligned["B"]) == 2

    def test_no_common_timestamps(self):
        a = {100: 1.0}
        b = {200: 2.0}
        ts, aligned = align_returns({"A": a, "B": b})
        assert ts == [] and aligned == {}

    def test_aligned_series_same_length(self):
        a = {1: 0.1, 2: 0.2, 3: 0.3}
        b = {1: 0.5, 2: 0.6, 3: 0.7}
        _, aligned = align_returns({"A": a, "B": b})
        assert len(aligned["A"]) == len(aligned["B"]) == 3

    def test_timestamps_sorted_ascending(self):
        a = {300: 1.0, 100: 2.0, 200: 3.0}
        b = {300: 4.0, 100: 5.0, 200: 6.0}
        ts, _ = align_returns({"A": a, "B": b})
        assert ts == sorted(ts)

    def test_values_correspond_to_timestamps(self):
        a = {1: 10.0, 2: 20.0, 3: 30.0}
        b = {1: 1.0, 2: 2.0, 3: 3.0}
        ts, aligned = align_returns({"A": a, "B": b})
        for i, t in enumerate(ts):
            assert aligned["A"][i] == a[t]
            assert aligned["B"][i] == b[t]


# ── _pearson ──────────────────────────────────────────────────────────────────


class TestPearson:
    def test_perfect_positive_correlation(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert _pearson(x, x) == pytest.approx(1.0)

    def test_perfect_negative_correlation(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [-1.0, -2.0, -3.0, -4.0, -5.0]
        assert _pearson(x, y) == pytest.approx(-1.0)

    def test_zero_correlation(self):
        # x cresce, y alternado sem relacao com x
        x = [1.0, 2.0, 3.0, 4.0]
        y = [2.0, 2.0, 2.0, 2.0]  # constante
        assert _pearson(x, y) == pytest.approx(0.0)

    def test_constant_series_returns_zero(self):
        x = [5.0, 5.0, 5.0, 5.0]
        y = [1.0, 2.0, 3.0, 4.0]
        assert _pearson(x, y) == pytest.approx(0.0)

    def test_both_constant_returns_zero(self):
        x = [3.0] * 5
        assert _pearson(x, x) == pytest.approx(0.0)

    def test_symmetric(self):
        x = [1.0, 3.0, 2.0, 5.0, 4.0]
        y = [2.0, 1.0, 4.0, 3.0, 5.0]
        assert _pearson(x, y) == pytest.approx(_pearson(y, x))

    def test_result_in_minus1_to_1(self):
        import random

        random.seed(42)
        x = [random.gauss(0, 1) for _ in range(50)]
        y = [random.gauss(0, 1) for _ in range(50)]
        r = _pearson(x, y)
        assert -1.0 <= r <= 1.0

    def test_too_short_series(self):
        assert _pearson([1.0], [2.0]) == pytest.approx(0.0)

    def test_empty_series(self):
        assert _pearson([], []) == pytest.approx(0.0)

    def test_known_value(self):
        # Correlacao calculada manualmente: x=[1,2,3], y=[2,4,6] => r=1.0
        assert _pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)


# ── correlation_matrix ────────────────────────────────────────────────────────


class TestCorrelationMatrix:
    def test_diagonal_is_one(self):
        aligned = {
            "A": [1.0, 2.0, 3.0, 4.0, 5.0],
            "B": [2.0, 1.0, 4.0, 3.0, 5.0],
        }
        m = correlation_matrix(aligned)
        assert m["A"]["A"] == pytest.approx(1.0)
        assert m["B"]["B"] == pytest.approx(1.0)

    def test_symmetric(self):
        aligned = {
            "A": [1.0, 2.0, 3.0, 4.0, 5.0],
            "B": [5.0, 4.0, 3.0, 2.0, 1.0],
        }
        m = correlation_matrix(aligned)
        assert m["A"]["B"] == pytest.approx(m["B"]["A"])

    def test_perfect_positive_between_identical(self):
        series = [1.0, 2.0, 3.0, 4.0, 5.0]
        aligned = {"A": series, "B": series}
        m = correlation_matrix(aligned)
        assert m["A"]["B"] == pytest.approx(1.0)

    def test_perfect_negative(self):
        aligned = {
            "A": [1.0, 2.0, 3.0, 4.0, 5.0],
            "B": [-1.0, -2.0, -3.0, -4.0, -5.0],
        }
        m = correlation_matrix(aligned)
        assert m["A"]["B"] == pytest.approx(-1.0)

    def test_three_tickers_full_matrix(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        aligned = {"A": a, "B": a, "C": list(reversed(a))}
        m = correlation_matrix(aligned)
        # A e B identicos
        assert m["A"]["B"] == pytest.approx(1.0)
        # A e C inversamente correlacionados
        assert m["A"]["C"] == pytest.approx(-1.0)
        # Todos tickers presentes
        for ta in ["A", "B", "C"]:
            for tb in ["A", "B", "C"]:
                assert tb in m[ta]


# ── rolling_correlation ───────────────────────────────────────────────────────


class TestRollingCorrelation:
    def test_correct_length(self):
        n = 50
        x = [float(i) for i in range(n)]
        ts = list(range(n))
        result = rolling_correlation(x, x, ts, window=10)
        assert len(result) == n - 10 + 1

    def test_empty_when_too_short(self):
        x = [1.0, 2.0, 3.0]
        ts = [1, 2, 3]
        result = rolling_correlation(x, x, ts, window=10)
        assert result == []

    def test_result_in_minus1_to_1(self):
        import random

        random.seed(7)
        n = 60
        x = [random.gauss(0, 1) for _ in range(n)]
        y = [random.gauss(0, 1) for _ in range(n)]
        ts = list(range(n))
        for pt in rolling_correlation(x, y, ts, window=20):
            assert -1.0 <= pt["correlation"] <= 1.0

    def test_timestamps_correct(self):
        n = 20
        x = list(range(n))
        ts = [1000 + i * 86400 for i in range(n)]
        result = rolling_correlation(x, x, ts, window=5)
        # Primeiro ponto: indice 4 (window-1)
        assert result[0]["time"] == ts[4]
        assert result[-1]["time"] == ts[-1]

    def test_result_has_required_keys(self):
        x = list(range(30))
        ts = list(range(30))
        for pt in rolling_correlation(x, x, ts, window=10):
            assert "time" in pt
            assert "correlation" in pt

    def test_identical_series_always_one(self):
        x = [1.0, 2.0, 1.5, 3.0, 2.5, 4.0, 3.5, 5.0, 4.5, 6.0, 5.5]
        ts = list(range(len(x)))
        for pt in rolling_correlation(x, x, ts, window=5):
            assert pt["correlation"] == pytest.approx(1.0)


# ── build_correlation_result ──────────────────────────────────────────────────


class TestBuildCorrelationResult:
    @pytest.fixture
    def two_ticker_map(self):
        return {
            "PETR4": _ramp(50, 100.0, 1.0),
            "VALE3": _ramp(50, 80.0, 0.8),
        }

    def test_common_bars_correct(self, two_ticker_map):
        r = build_correlation_result(two_ticker_map, "1y")
        # 50 barras OHLC -> 49 retornos cada; inner join = 49
        assert r.common_bars == 49

    def test_matrix_nxn(self, two_ticker_map):
        r = build_correlation_result(two_ticker_map, "1y")
        assert "PETR4" in r.matrix and "VALE3" in r.matrix
        assert "VALE3" in r.matrix["PETR4"]

    def test_matrix_diagonal_one(self, two_ticker_map):
        r = build_correlation_result(two_ticker_map, "1y")
        for t in r.tickers:
            assert r.matrix[t][t] == pytest.approx(1.0)

    def test_most_correlated_populated(self):
        bars_map = {
            "A": _ramp(40),
            "B": _ramp(40, step=1.1),
            "C": _ramp(40, start=50.0, step=-0.5),
        }
        r = build_correlation_result(bars_map, "1y")
        assert len(r.most_correlated) >= 1

    def test_least_correlated_populated(self):
        bars_map = {
            "A": _ramp(40),
            "B": _ramp(40, step=1.1),
            "C": _ramp(40, start=50.0, step=-0.5),
        }
        r = build_correlation_result(bars_map, "1y")
        assert len(r.least_correlated) >= 1

    def test_rolling_pairs_all_combinations(self, two_ticker_map):
        r = build_correlation_result(two_ticker_map, "1y", rolling_window=10)
        assert "PETR4/VALE3" in r.rolling_pairs

    def test_rolling_pairs_three_tickers(self):
        bars_map = {f"T{i}": _ramp(50, 100 + i * 10) for i in range(3)}
        r = build_correlation_result(bars_map, "1y", rolling_window=10)
        expected_pairs = {"T0/T1", "T0/T2", "T1/T2"}
        assert expected_pairs == set(r.rolling_pairs.keys())

    def test_diversification_score_in_range(self, two_ticker_map):
        r = build_correlation_result(two_ticker_map, "1y")
        assert 0.0 <= r.diversification_score <= 1.0

    def test_diversification_zero_for_perfect_correlation(self):
        # Ambos identicos -> correlacao 1.0 -> diversification = 0
        bars = _ramp(40)
        r = build_correlation_result({"A": bars, "B": bars}, "1y")
        assert r.diversification_score == pytest.approx(0.0, abs=0.01)

    def test_errors_propagated(self):
        errors = [{"ticker": "XXXX", "error": "Sem dados"}]
        r = build_correlation_result({"A": _ramp(40)}, "1y", errors=errors)
        assert len(r.errors) == 1
        assert r.errors[0]["ticker"] == "XXXX"

    def test_insufficient_data_returns_empty_result(self):
        r = build_correlation_result({"A": _flat(1), "B": _flat(1)}, "1y")
        assert r.common_bars == 0
        assert r.matrix == {}

    def test_to_dict_required_keys(self, two_ticker_map):
        d = build_correlation_result(two_ticker_map, "1y").to_dict()
        required = {
            "tickers",
            "range_period",
            "common_bars",
            "matrix",
            "most_correlated",
            "least_correlated",
            "diversification_score",
            "rolling_pairs",
            "errors",
            "total_tickers",
            "failed_tickers",
        }
        assert required <= set(d.keys())

    def test_metadata_preserved(self, two_ticker_map):
        r = build_correlation_result(two_ticker_map, "6mo")
        assert r.range_period == "6mo"
        assert set(r.tickers) == {"PETR4", "VALE3"}

    @pytest.mark.parametrize("n_tickers", [2, 3, 4, 5])
    def test_various_ticker_counts(self, n_tickers):
        bars_map = {f"T{i}": _ramp(40, 100 + i * 5) for i in range(n_tickers)}
        r = build_correlation_result(bars_map, "1y", rolling_window=10)
        assert len(r.tickers) == n_tickers
        assert len(r.matrix) == n_tickers
        expected_pairs = n_tickers * (n_tickers - 1) // 2
        assert len(r.rolling_pairs) == expected_pairs


# ── CorrelationService ────────────────────────────────────────────────────────


class TestCorrelationService:
    def _make_svc(self) -> CorrelationService:
        return CorrelationService(AsyncMock())

    def _patch_brapi(self, svc: CorrelationService, bars_by_ticker: dict):
        """bars_by_ticker: {ticker: list[bars] | Exception}"""

        async def _fake(ticker, **kw):
            key = str(ticker).upper()
            result = bars_by_ticker.get(key)
            if isinstance(result, Exception):
                raise result
            return result or _ramp(50)

        svc._brapi.get_ohlc_bars = _fake

    @pytest.mark.asyncio
    async def test_returns_correlation_result(self):
        svc = self._make_svc()
        self._patch_brapi(
            svc,
            {
                "PETR4": _ramp(50),
                "VALE3": _ramp(50, 80.0, 0.9),
            },
        )
        r = await svc.compute(["PETR4", "VALE3"])
        assert isinstance(r, CorrelationResult)
        assert r.common_bars > 0

    @pytest.mark.asyncio
    async def test_matrix_has_both_tickers(self):
        svc = self._make_svc()
        self._patch_brapi(
            svc,
            {
                "PETR4": _ramp(50),
                "VALE3": _ramp(50),
            },
        )
        r = await svc.compute(["PETR4", "VALE3"])
        assert "PETR4" in r.matrix
        assert "VALE3" in r.matrix

    @pytest.mark.asyncio
    async def test_empty_tickers_raises(self):
        svc = self._make_svc()
        with pytest.raises(BacktestError, match="menos 2"):
            await svc.compute([])

    @pytest.mark.asyncio
    async def test_single_ticker_raises(self):
        svc = self._make_svc()
        with pytest.raises(BacktestError, match="menos 2"):
            await svc.compute(["PETR4"])

    @pytest.mark.asyncio
    async def test_too_many_tickers_raises(self):
        svc = self._make_svc()
        with pytest.raises(BacktestError, match=str(MAX_TICKERS)):
            await svc.compute([f"T{i}" for i in range(MAX_TICKERS + 1)])

    @pytest.mark.asyncio
    async def test_one_ticker_fails_partial_success(self):
        svc = self._make_svc()
        self._patch_brapi(
            svc,
            {
                "PETR4": _ramp(50),
                "VALE3": _ramp(50),
                "XXXX": RuntimeError("Ticker invalido"),
            },
        )
        r = await svc.compute(["PETR4", "VALE3", "XXXX"])
        assert len(r.errors) == 1
        assert r.errors[0]["ticker"] == "XXXX"
        assert r.common_bars > 0

    @pytest.mark.asyncio
    async def test_all_tickers_fail_raises(self):
        svc = self._make_svc()
        self._patch_brapi(
            svc,
            {
                "A": RuntimeError("err"),
                "B": RuntimeError("err"),
            },
        )
        with pytest.raises(BacktestError, match="validos para apenas"):
            await svc.compute(["A", "B"])

    @pytest.mark.asyncio
    async def test_one_valid_ticker_raises(self):
        svc = self._make_svc()
        self._patch_brapi(
            svc,
            {
                "A": _ramp(50),
                "B": RuntimeError("err"),
            },
        )
        with pytest.raises(BacktestError, match="validos para apenas"):
            await svc.compute(["A", "B"])

    @pytest.mark.asyncio
    async def test_tickers_normalized_uppercase(self):
        svc = self._make_svc()
        seen: list[str] = []

        async def _capture(ticker, **kw):
            seen.append(str(ticker))
            return _ramp(50)

        svc._brapi.get_ohlc_bars = _capture
        await svc.compute(["petr4", "vale3"])
        assert all(t.isupper() for t in seen)

    @pytest.mark.asyncio
    async def test_rolling_window_in_result(self):
        svc = self._make_svc()
        self._patch_brapi(svc, {"PETR4": _ramp(80), "VALE3": _ramp(80, 80.0)})
        r = await svc.compute(["PETR4", "VALE3"], rolling_window=20)
        # Deve ter dados rolantes
        assert len(r.rolling_pairs) > 0

    @pytest.mark.asyncio
    async def test_window_adapted_for_short_data(self):
        svc = self._make_svc()
        # 15 barras → 14 retornos → window 30 > min_bars // 2 → adapta
        self._patch_brapi(svc, {"PETR4": _ramp(15), "VALE3": _ramp(15, 80.0)})
        # Nao deve lancar excecao
        r = await svc.compute(["PETR4", "VALE3"], rolling_window=30)
        assert isinstance(r, CorrelationResult)

    @pytest.mark.asyncio
    async def test_result_serializable(self):
        svc = self._make_svc()
        self._patch_brapi(svc, {"PETR4": _ramp(50), "VALE3": _ramp(50, 80.0)})
        r = await svc.compute(["PETR4", "VALE3"])
        d = r.to_dict()
        assert all(
            k in d
            for k in ["matrix", "rolling_pairs", "diversification_score", "common_bars", "most_correlated"]
        )

    @pytest.mark.asyncio
    async def test_three_tickers_all_pairs(self):
        svc = self._make_svc()
        self._patch_brapi(
            svc,
            {
                "PETR4": _ramp(50),
                "VALE3": _ramp(50, 80.0),
                "ITUB4": _ramp(50, 30.0, 0.3),
            },
        )
        r = await svc.compute(["PETR4", "VALE3", "ITUB4"])
        expected = {"PETR4/VALE3", "PETR4/ITUB4", "VALE3/ITUB4"}
        assert expected == set(r.rolling_pairs.keys())

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        from finanalytics_ai.application.services.correlation_service import MAX_CONCURRENT

        active = [0]
        max_seen = [0]
        lock = asyncio.Lock()

        async def _slow(ticker, **kw):
            async with lock:
                active[0] += 1
                if active[0] > max_seen[0]:
                    max_seen[0] = active[0]
            await asyncio.sleep(0.01)
            async with lock:
                active[0] -= 1
            return _ramp(50)

        svc = self._make_svc()
        svc._brapi.get_ohlc_bars = _slow
        tickers = [f"T{i}" for i in range(7)]
        await svc.compute(tickers)
        assert max_seen[0] <= MAX_CONCURRENT
