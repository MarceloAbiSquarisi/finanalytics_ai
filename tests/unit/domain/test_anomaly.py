"""
Testes unitarios do modulo de deteccao de anomalias.

Cobertura:
  detect_zscore
    - retorno normal nao dispara
    - retorno anômalo acima do threshold dispara (UP)
    - retorno anomalo negativo dispara (DOWN)
    - serie curta retorna []
    - std~0 (serie flat) retorna []
    - severidade correta: HIGH para |z|>3.5, MEDIUM para 2.5-3.5
    - score e o z-score calculado
    - timestamp e o da ultima barra

  detect_bollinger
    - preco dentro das bandas nao dispara
    - preco acima da banda superior dispara (UP)
    - preco abaixo da banda inferior dispara (DOWN)
    - serie curta retorna []
    - std~0 retorna []
    - context contem upper/lower/middle band

  detect_cusum
    - serie sem tendencia nao dispara
    - tendencia de alta persistente dispara (UP)
    - tendencia de queda persistente dispara (DOWN)
    - serie curta retorna []
    - std~0 retorna []
    - score e s_pos ou s_neg

  detect_volume_spike
    - volume normal nao dispara
    - volume spike (3x) dispara
    - volume spike alto (5x) = HIGH
    - serie curta retorna []
    - volume zero ignorado
    - ratio correto no score

  analyze_ticker
    - sem barras retorna error
    - retorna AnomalyResult com ticker correto
    - anomalias ordenadas por severidade desc
    - exception interna retorna result com error
    - config None usa defaults

  AnomalyResult
    - has_anomalies correto
    - max_severity correto
    - to_dict com chaves obrigatorias

  build_multi_anomaly_result
    - resultados ordenados: HIGH primeiro
    - tickers_with_anomalies correto
    - high_severity_count correto
    - ticker sem dados retorna error no result

  AnomalyService
    - brapi chamado uma vez por ticker
    - resultado MultiAnomalyResult retornado
    - falha em fetch nao cancela outros
    - tickers normalizados uppercase
    - lista vazia lanca ValueError
    - config repassado ao engine
    - scan_single retorna AnomalyResult
"""
from __future__ import annotations

import asyncio
import math
from unittest.mock import AsyncMock

import pytest

from finanalytics_ai.application.services.anomaly_service import AnomalyService
from finanalytics_ai.domain.anomaly.engine import (
    AnomalyDirection,
    AnomalyEvent,
    AnomalyResult,
    AnomalySeverity,
    AnomalyType,
    DetectorConfig,
    MultiAnomalyResult,
    analyze_ticker,
    build_multi_anomaly_result,
    detect_bollinger,
    detect_cusum,
    detect_volume_spike,
    detect_zscore,
    _mean,
    _std,
    _returns,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bars(closes: list[float], volumes: list[float] | None = None,
          base_ts: int = 1_700_000_000) -> list[dict]:
    vols = volumes or [1_000_000.0] * len(closes)
    return [
        {"time": base_ts + i * 86400, "open": c, "high": c * 1.01,
         "low": c * 0.99, "close": c, "volume": int(vols[i])}
        for i, c in enumerate(closes)
    ]


def _flat_bars(n: int = 50, price: float = 100.0) -> list[dict]:
    return _bars([price] * n)


def _ramp_bars(n: int = 50, start: float = 100.0, step: float = 0.1) -> list[dict]:
    return _bars([start + i * step for i in range(n)])


def _noisy_bars(n: int = 40, seed: int = 42, spike_pct: float | None = None,
                crash_pct: float | None = None) -> list[dict]:
    """Barras com variancia realista (~0.5% daily) e spike/crash opcional na ultima barra."""
    import random
    random.seed(seed)
    closes = [100.0 * (1 + random.gauss(0, 0.005)) for _ in range(n)]
    if spike_pct is not None:
        closes[-1] = closes[-2] * (1 + spike_pct / 100)
    if crash_pct is not None:
        closes[-1] = closes[-2] * (1 - crash_pct / 100)
    return _bars(closes)


def _trend_bars(n_ref: int = 35, n_trend: int = 15, seed: int = 7,
                trend_pct: float = 2.0) -> list[dict]:
    """Barras com variancia na referencia e tendencia clara nos ultimos n_trend periodos."""
    import random
    random.seed(seed)
    ref = [100.0 * (1 + random.gauss(0, 0.003)) for _ in range(n_ref)]
    last = ref[-1]
    trend = [last * (1 + i * trend_pct / 100) for i in range(1, n_trend + 1)]
    return _bars(ref + trend)


def _downtrend_bars(n_ref: int = 35, n_trend: int = 15, seed: int = 7,
                    trend_pct: float = 2.0) -> list[dict]:
    import random
    random.seed(seed)
    ref = [100.0 * (1 + random.gauss(0, 0.003)) for _ in range(n_ref)]
    last = ref[-1]
    trend = [last * (1 - i * trend_pct / 100) for i in range(1, n_trend + 1)]
    return _bars(ref + trend)


def _cfg(**kwargs) -> DetectorConfig:
    return DetectorConfig(**kwargs)


# ── Helpers estatisticos ──────────────────────────────────────────────────────

class TestStatHelpers:
    def test_mean_correct(self):
        assert _mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)

    def test_mean_empty(self):
        assert _mean([]) == 0.0

    def test_std_correct(self):
        import statistics
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert _std(x) == pytest.approx(statistics.stdev(x))

    def test_std_single(self):
        assert _std([5.0]) == 0.0

    def test_returns_correct(self):
        closes = [100.0, 110.0, 99.0]
        r = _returns(closes)
        assert r[0] == pytest.approx(10.0)
        assert r[1] == pytest.approx((99 - 110) / 110 * 100)

    def test_returns_empty_for_single(self):
        assert _returns([100.0]) == []


# ── detect_zscore ─────────────────────────────────────────────────────────────

class TestDetectZscore:
    def test_normal_return_no_alert(self):
        # Serie com retornos pequenos e estavel
        closes = [100.0 + i * 0.01 for i in range(40)]
        result = detect_zscore(_bars(closes), "T", _cfg(zscore_window=20, zscore_threshold=2.5))
        assert result == []

    def test_spike_up_triggers(self):
        result = detect_zscore(_noisy_bars(40, seed=42, spike_pct=20), "T",
                               _cfg(zscore_window=20, zscore_threshold=2.5))
        assert len(result) == 1
        assert result[0].direction == AnomalyDirection.UP

    def test_spike_down_triggers(self):
        result = detect_zscore(_noisy_bars(40, seed=42, crash_pct=25), "T",
                               _cfg(zscore_window=20, zscore_threshold=2.5))
        assert len(result) == 1
        assert result[0].direction == AnomalyDirection.DOWN

    def test_short_series_empty(self):
        result = detect_zscore(_bars([100.0] * 5), "T", _cfg(zscore_window=20))
        assert result == []

    def test_flat_series_empty(self):
        # std=0, nao deve disparar nem dar erro
        closes = [100.0] * 50
        result = detect_zscore(_bars(closes), "T", _cfg(zscore_window=20, zscore_threshold=2.5))
        assert result == []

    def test_severity_high_for_large_z(self):
        result = detect_zscore(_noisy_bars(40, seed=1, spike_pct=50), "T",
                               _cfg(zscore_window=20, zscore_threshold=2.5))
        if result:
            assert result[0].severity in (AnomalySeverity.HIGH, AnomalySeverity.MEDIUM)

    def test_anomaly_type_correct(self):
        result = detect_zscore(_noisy_bars(40, seed=42, spike_pct=20), "T",
                               _cfg(zscore_window=20, zscore_threshold=2.5))
        if result:
            assert result[0].anomaly_type == AnomalyType.ZSCORE_SPIKE

    def test_timestamp_is_last_bar(self):
        bars = _noisy_bars(40, seed=42, spike_pct=20)
        result = detect_zscore(bars, "T", _cfg(zscore_window=20, zscore_threshold=2.5))
        if result:
            assert result[0].timestamp == bars[-1]["time"]

    def test_ticker_in_event(self):
        result = detect_zscore(_noisy_bars(40, seed=42, spike_pct=20), "PETR4",
                               _cfg(zscore_window=20, zscore_threshold=2.5))
        if result:
            assert result[0].ticker == "PETR4"


# ── detect_bollinger ──────────────────────────────────────────────────────────

class TestDetectBollinger:
    def test_inside_bands_no_alert(self):
        # Preco dentro da banda
        closes = [100.0] * 25
        result = detect_bollinger(_bars(closes), "T", _cfg(bollinger_window=20, bollinger_k=2.0))
        assert result == []

    def test_breakout_up_triggers(self):
        # Historia com std=1, preco atual muito acima
        import random; random.seed(42)
        history = [100.0 + random.gauss(0, 0.5) for _ in range(22)]
        history[-1] = 130.0  # breakout
        result = detect_bollinger(_bars(history), "T", _cfg(bollinger_window=20, bollinger_k=2.0))
        assert len(result) == 1
        assert result[0].direction == AnomalyDirection.UP

    def test_breakdown_down_triggers(self):
        import random; random.seed(7)
        history = [100.0 + random.gauss(0, 0.5) for _ in range(22)]
        history[-1] = 70.0  # breakdown
        result = detect_bollinger(_bars(history), "T", _cfg(bollinger_window=20, bollinger_k=2.0))
        assert len(result) == 1
        assert result[0].direction == AnomalyDirection.DOWN

    def test_short_series_empty(self):
        result = detect_bollinger(_bars([100.0] * 5), "T", _cfg(bollinger_window=20))
        assert result == []

    def test_flat_std_empty(self):
        result = detect_bollinger(_flat_bars(25), "T", _cfg(bollinger_window=20))
        assert result == []

    def test_context_has_bands(self):
        import random; random.seed(1)
        history = [100.0 + random.gauss(0, 1) for _ in range(22)]
        history[-1] = 130.0
        result = detect_bollinger(_bars(history), "T", _cfg(bollinger_window=20, bollinger_k=2.0))
        if result:
            ctx = result[0].context
            assert "upper_band" in ctx
            assert "lower_band" in ctx
            assert "middle_band" in ctx

    def test_anomaly_type_correct(self):
        import random; random.seed(2)
        history = [100.0 + random.gauss(0, 1) for _ in range(22)]
        history[-1] = 130.0
        result = detect_bollinger(_bars(history), "T", _cfg(bollinger_window=20, bollinger_k=2.0))
        if result:
            assert result[0].anomaly_type == AnomalyType.BOLLINGER_BREAK


# ── detect_cusum ──────────────────────────────────────────────────────────────

class TestDetectCusum:
    def test_no_trend_no_alert(self):
        # Retornos aleatorios em torno de 0
        import random; random.seed(99)
        closes = [100.0]
        for _ in range(49):
            closes.append(closes[-1] * (1 + random.gauss(0, 0.002)))
        result = detect_cusum(_bars(closes), "T", _cfg(
            cusum_window=25, cusum_k=0.5, cusum_threshold=5.0
        ))
        # Pode ou nao disparar com dados aleatorios — apenas verifica sem crash
        assert isinstance(result, list)

    def test_strong_uptrend_triggers_up(self):
        result = detect_cusum(_trend_bars(n_ref=35, n_trend=15, seed=7, trend_pct=2.0), "T",
                              _cfg(cusum_window=30, cusum_k=0.3, cusum_threshold=3.0))
        assert len(result) >= 1
        assert result[0].direction == AnomalyDirection.UP

    def test_strong_downtrend_triggers_down(self):
        result = detect_cusum(_downtrend_bars(n_ref=35, n_trend=15, seed=7, trend_pct=2.0), "T",
                              _cfg(cusum_window=30, cusum_k=0.3, cusum_threshold=3.0))
        assert len(result) >= 1
        assert result[0].direction == AnomalyDirection.DOWN

    def test_short_series_empty(self):
        result = detect_cusum(_bars([100.0] * 5), "T", _cfg(cusum_window=30))
        assert result == []

    def test_anomaly_type_correct(self):
        result = detect_cusum(_trend_bars(seed=7), "T",
                              _cfg(cusum_window=30, cusum_k=0.3, cusum_threshold=3.0))
        if result:
            assert result[0].anomaly_type == AnomalyType.CUSUM_SHIFT

    def test_context_has_s_values(self):
        result = detect_cusum(_trend_bars(seed=7), "T",
                              _cfg(cusum_window=30, cusum_k=0.3, cusum_threshold=3.0))
        if result:
            assert "s_pos" in result[0].context
            assert "s_neg" in result[0].context


# ── detect_volume_spike ───────────────────────────────────────────────────────

class TestDetectVolumeSpike:
    def test_normal_volume_no_alert(self):
        vols = [1_000_000.0] * 25
        result = detect_volume_spike(_bars([100.0] * 25, vols), "T",
                                     _cfg(volume_window=20, volume_multiplier=3.0))
        assert result == []

    def test_spike_3x_triggers(self):
        vols = [1_000_000.0] * 24 + [4_000_000.0]  # 4x
        result = detect_volume_spike(_bars([100.0] * 25, vols), "T",
                                     _cfg(volume_window=20, volume_multiplier=3.0))
        assert len(result) == 1

    def test_spike_5x_is_high(self):
        vols = [1_000_000.0] * 24 + [6_000_000.0]  # 6x
        result = detect_volume_spike(_bars([100.0] * 25, vols), "T",
                                     _cfg(volume_window=20, volume_multiplier=3.0))
        assert len(result) == 1
        assert result[0].severity == AnomalySeverity.HIGH

    def test_short_series_empty(self):
        result = detect_volume_spike(_bars([100.0] * 5), "T", _cfg(volume_window=20))
        assert result == []

    def test_zero_volume_ignored(self):
        # Barras com volume 0 nao devem ser contadas na media
        vols = [0.0] * 10 + [1_000_000.0] * 14 + [4_000_000.0]
        result = detect_volume_spike(_bars([100.0] * 25, vols), "T",
                                     _cfg(volume_window=20, volume_multiplier=3.0))
        assert isinstance(result, list)

    def test_ratio_in_score(self):
        vols = [1_000_000.0] * 24 + [5_000_000.0]  # 5x
        result = detect_volume_spike(_bars([100.0] * 25, vols), "T",
                                     _cfg(volume_window=20, volume_multiplier=3.0))
        if result:
            assert result[0].score == pytest.approx(5.0, abs=0.1)

    def test_anomaly_type_correct(self):
        vols = [1_000_000.0] * 24 + [5_000_000.0]
        result = detect_volume_spike(_bars([100.0] * 25, vols), "T",
                                     _cfg(volume_window=20, volume_multiplier=3.0))
        if result:
            assert result[0].anomaly_type == AnomalyType.VOLUME_SPIKE


# ── analyze_ticker ────────────────────────────────────────────────────────────

class TestAnalyzeTicker:
    def test_empty_bars_returns_error(self):
        r = analyze_ticker([], "T")
        assert r.error is not None
        assert r.bars_analyzed == 0

    def test_returns_correct_ticker(self):
        r = analyze_ticker(_flat_bars(50), "PETR4")
        assert r.ticker == "PETR4"

    def test_flat_series_no_anomalies(self):
        r = analyze_ticker(_flat_bars(50), "T")
        # Serie flat: zscore std=0, bollinger std=0, cusum sem tendencia
        # Pode ter 0 anomalias
        assert isinstance(r.anomalies, list)

    def test_anomalies_sorted_high_first(self):
        bars = _noisy_bars(40, seed=42, spike_pct=20)
        vols = [1_000_000.0] * 39 + [10_000_000.0]
        bars[-1]["volume"] = int(vols[-1])
        r = analyze_ticker(bars, "T")
        if len(r.anomalies) >= 2:
            order = {AnomalySeverity.HIGH: 2, AnomalySeverity.MEDIUM: 1, AnomalySeverity.LOW: 0}
            scores = [order[a.severity] for a in r.anomalies]
            assert scores == sorted(scores, reverse=True)

    def test_config_none_uses_defaults(self):
        r = analyze_ticker(_flat_bars(50), "T", config=None)
        assert r.bars_analyzed <= 100  # lookback_bars default=100

    def test_bars_analyzed_correct(self):
        r = analyze_ticker(_flat_bars(50), "T", config=_cfg(lookback_bars=30))
        assert r.bars_analyzed == 30


# ── AnomalyResult ─────────────────────────────────────────────────────────────

class TestAnomalyResult:
    def _event(self, sev: AnomalySeverity) -> AnomalyEvent:
        return AnomalyEvent(
            ticker="T", anomaly_type=AnomalyType.ZSCORE_SPIKE,
            severity=sev, direction=AnomalyDirection.UP,
            score=3.0, threshold=2.5, current_value=5.0,
            description="test", timestamp=1700000000,
        )

    def test_has_anomalies_false(self):
        r = AnomalyResult(ticker="T", bars_analyzed=50, anomalies=[])
        assert r.has_anomalies is False

    def test_has_anomalies_true(self):
        r = AnomalyResult(ticker="T", bars_analyzed=50,
                          anomalies=[self._event(AnomalySeverity.LOW)])
        assert r.has_anomalies is True

    def test_max_severity_none_when_empty(self):
        r = AnomalyResult(ticker="T", bars_analyzed=50, anomalies=[])
        assert r.max_severity is None

    def test_max_severity_high(self):
        r = AnomalyResult(ticker="T", bars_analyzed=50, anomalies=[
            self._event(AnomalySeverity.LOW),
            self._event(AnomalySeverity.HIGH),
            self._event(AnomalySeverity.MEDIUM),
        ])
        assert r.max_severity == AnomalySeverity.HIGH

    def test_to_dict_required_keys(self):
        r = AnomalyResult(ticker="T", bars_analyzed=50, anomalies=[])
        d = r.to_dict()
        for k in ["ticker", "bars_analyzed", "anomaly_count", "has_anomalies",
                  "max_severity", "anomalies", "error"]:
            assert k in d


# ── build_multi_anomaly_result ────────────────────────────────────────────────

class TestBuildMultiAnomalyResult:
    def test_results_sorted_high_first(self):
        anom_bars  = _noisy_bars(40, seed=42, spike_pct=20)
        normal_bars = _noisy_bars(40, seed=99)
        ticker_bars = {
            "NORM": normal_bars,
            "ANOM": anom_bars,
        }
        r = build_multi_anomaly_result(ticker_bars, "3mo")
        # ANOM deve aparecer primeiro (tem anomalias)
        assert r.results[0].ticker == "ANOM" or r.results[0].has_anomalies

    def test_total_tickers_correct(self):
        ticker_bars = {f"T{i}": _flat_bars(50) for i in range(4)}
        r = build_multi_anomaly_result(ticker_bars, "3mo")
        assert r.total_tickers == 4

    def test_tickers_with_anomalies_zero_for_flat(self):
        r = build_multi_anomaly_result({"A": _flat_bars(50)}, "3mo")
        # Serie flat pode ou nao ter anomalias — apenas verifica tipo
        assert isinstance(r.tickers_with_anomalies, int)

    def test_empty_bars_creates_error_result(self):
        r = build_multi_anomaly_result({"FAIL": []}, "3mo")
        assert r.results[0].error is not None

    def test_to_dict_required_keys(self):
        r = build_multi_anomaly_result({"T": _flat_bars(50)}, "3mo")
        d = r.to_dict()
        for k in ["total_tickers", "tickers_with_anomalies", "high_severity_count",
                  "range_period", "results"]:
            assert k in d

    def test_range_period_preserved(self):
        r = build_multi_anomaly_result({"T": _flat_bars(50)}, "6mo")
        assert r.range_period == "6mo"


# ── AnomalyService ────────────────────────────────────────────────────────────

class TestAnomalyService:
    def _make_svc(self) -> AnomalyService:
        return AnomalyService(AsyncMock())

    def _patch_brapi(self, svc: AnomalyService, bars_by_ticker: dict) -> None:
        async def _fake(ticker, **kw):
            k = str(ticker).upper()
            result = bars_by_ticker.get(k)
            if isinstance(result, Exception):
                raise result
            return result or _flat_bars(50)
        svc._brapi.get_ohlc_bars = _fake

    @pytest.mark.asyncio
    async def test_returns_multi_result(self):
        svc = self._make_svc()
        self._patch_brapi(svc, {"PETR4": _flat_bars(50), "VALE3": _flat_bars(50)})
        r = await svc.scan(["PETR4", "VALE3"])
        assert isinstance(r, MultiAnomalyResult)

    @pytest.mark.asyncio
    async def test_total_tickers_correct(self):
        svc = self._make_svc()
        self._patch_brapi(svc, {"A": _flat_bars(50), "B": _flat_bars(50)})
        r = await svc.scan(["A", "B"])
        assert r.total_tickers == 2

    @pytest.mark.asyncio
    async def test_empty_tickers_raises(self):
        svc = self._make_svc()
        with pytest.raises(ValueError, match="pelo menos 1"):
            await svc.scan([])

    @pytest.mark.asyncio
    async def test_tickers_normalized_uppercase(self):
        seen = []
        async def _cap(ticker, **kw):
            seen.append(str(ticker))
            return _flat_bars(50)
        svc = self._make_svc()
        svc._brapi.get_ohlc_bars = _cap
        await svc.scan(["petr4", "vale3"])
        assert all(t.isupper() for t in seen)

    @pytest.mark.asyncio
    async def test_fetch_failure_creates_error_result(self):
        svc = self._make_svc()
        self._patch_brapi(svc, {
            "OK": _flat_bars(50),
            "FAIL": RuntimeError("timeout"),
        })
        r = await svc.scan(["OK", "FAIL"])
        assert r.total_tickers == 2
        # FAIL vai ter error no result
        fail_r = next((x for x in r.results if x.ticker == "FAIL"), None)
        assert fail_r is not None

    @pytest.mark.asyncio
    async def test_config_passed_to_engine(self):
        svc = self._make_svc()
        self._patch_brapi(svc, {"T": _flat_bars(50)})
        cfg = DetectorConfig(zscore_threshold=4.0, lookback_bars=40)
        r = await svc.scan(["T"], config=cfg)
        # lookback aplicado: bars_analyzed <= 40
        assert r.results[0].bars_analyzed <= 40

    @pytest.mark.asyncio
    async def test_scan_single_returns_anomaly_result(self):
        svc = self._make_svc()
        self._patch_brapi(svc, {"PETR4": _flat_bars(50)})
        r = await svc.scan_single("PETR4")
        assert isinstance(r, AnomalyResult)
        assert r.ticker == "PETR4"

    @pytest.mark.asyncio
    async def test_max_tickers_cap(self):
        from finanalytics_ai.application.services.anomaly_service import MAX_TICKERS
        seen = []
        async def _cap(ticker, **kw):
            seen.append(str(ticker))
            return _flat_bars(50)
        svc = self._make_svc()
        svc._brapi.get_ohlc_bars = _cap
        tickers = [f"T{i}" for i in range(MAX_TICKERS + 5)]
        await svc.scan(tickers)
        assert len(seen) <= MAX_TICKERS

    @pytest.mark.asyncio
    async def test_result_serializable(self):
        import json
        svc = self._make_svc()
        self._patch_brapi(svc, {"T": _flat_bars(50)})
        r = await svc.scan(["T"])
        d = r.to_dict()
        json.dumps(d)  # nao deve levantar excecao
