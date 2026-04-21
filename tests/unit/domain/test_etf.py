"""Testes unitários — Análise de ETFs (Sprint 31)"""

from __future__ import annotations

import math

from finanalytics_ai.domain.etf.entities import (
    ETF_CATALOG,
    ETFMetrics,
    TrackingErrorResult,
    etfs_by_category,
    get_etf,
)


def _returns(prices):
    return [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]


def _max_drawdown(prices):
    peak, mdd = prices[0], 0.0
    for p in prices:
        peak = max(peak, p)
        mdd = min(mdd, (p - peak) / peak)
    return mdd


def _cagr(s, e, n):
    return (e / s) ** (1 / (n / 252)) - 1


def _pearson(xs, ys):
    n = min(len(xs), len(ys))
    mx, my = sum(xs[:n]) / n, sum(ys[:n]) / n
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs[:n]))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys[:n]))
    return cov / (sx * sy) if sx * sy else 0.0


# ── Catálogo ──────────────────────────────────────────────────────────────────


def test_catalog_size():
    assert len(ETF_CATALOG) >= 20


def test_bova11_exists():
    assert get_etf("BOVA11") is not None


def test_bova11_benchmark():
    assert get_etf("BOVA11").benchmark == "^BVSP"


def test_ivvb11_category():
    assert get_etf("IVVB11").category == "Ações EUA"


def test_hash11_category():
    assert get_etf("HASH11").category == "Cripto"


def test_ntnb11_category():
    assert get_etf("NTNB11").category == "Renda Fixa"


def test_rf_category_count():
    assert len(etfs_by_category("Renda Fixa")) >= 3


def test_gold11_category():
    assert get_etf("GOLD11").category == "Commodities"


def test_all_have_benchmark():
    assert all(e.benchmark for e in ETF_CATALOG)


def test_all_ter_nonneg():
    assert all(e.ter >= 0 for e in ETF_CATALOG)


# ── Cálculos ──────────────────────────────────────────────────────────────────

PRICES = [100, 102, 101, 105, 103, 108, 107, 110, 108, 115]
RETS = _returns(PRICES)


def test_returns_length():
    assert len(RETS) == 9


def test_first_return():
    assert abs(RETS[0] - 0.02) < 1e-9


def test_max_dd_negative():
    assert _max_drawdown(PRICES) < 0


def test_max_dd_monotone_zero():
    assert _max_drawdown([100, 110, 120]) == 0.0


def test_cagr_positive():
    assert _cagr(100, 115, 252) > 0


def test_cagr_approx_15pct():
    assert abs(_cagr(100, 115, 252) - 0.15) < 0.001


def test_pearson_self():
    assert abs(_pearson(RETS, RETS) - 1.0) < 1e-9


def test_pearson_negative():
    assert _pearson(RETS, [-r for r in RETS]) < 0


# ── TrackingError quality_label ───────────────────────────────────────────────


def _te(pct):
    return TrackingErrorResult(
        "B", "B", "1y", pct, 0.5, 0.99, 1.0, 0.98, 0.1, 10.0, 10.5, -0.5, 200, []
    )


def test_quality_excelente():
    assert _te(0.3).quality_label == "Excelente replicação"


def test_quality_boa():
    assert _te(1.0).quality_label == "Boa replicação"


def test_quality_razoavel():
    assert _te(2.0).quality_label == "Replicação razoável"


def test_quality_desvio():
    assert _te(4.0).quality_label == "Desvio elevado"


# ── ETFMetrics value object ───────────────────────────────────────────────────

_M = ETFMetrics(
    "BOVA11",
    "iShares",
    "1y",
    0.15,
    0.14,
    0.18,
    1.2,
    -0.12,
    0.025,
    1.16,
    200,
    100.0,
    115.0,
    "Ações BR",
    0.10,
)


def test_metrics_total_return_pct():
    assert _M.total_return_pct == 15.0


def test_metrics_max_dd_pct():
    assert _M.max_drawdown_pct == -12.0


def test_metrics_sharpe_label():
    assert _M.sharpe_label == "Bom"


def test_metrics_to_dict_keys():
    assert all(
        k in _M.to_dict() for k in ["ticker", "sharpe", "volatility_pct", "max_drawdown_pct"]
    )
