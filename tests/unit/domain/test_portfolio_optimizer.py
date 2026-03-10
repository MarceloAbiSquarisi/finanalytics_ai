"""Testes unitários — Otimizador de Portfólio (Sprint 32)"""

import math, random
from finanalytics_ai.domain.portfolio_optimizer.engine import (
    markowitz_optimize,
    risk_parity_optimize,
    black_litterman_optimize,
    equal_weight,
    _covariance_matrix,
    _mat_vec,
    _dot,
    _gauss_jordan_inverse,
    _returns,
)

random.seed(42)
N, T = 4, 252
_rets_mat = [[random.gauss(0.0004 * (i + 1), 0.012 + i * 0.003) for _ in range(T)] for i in range(N)]
_means = [sum(r) / T for r in _rets_mat]
_cov = _covariance_matrix(_rets_mat)
_tkrs = ["BOVA11", "IVVB11", "SMAL11", "NTNB11"]
_RF = 0.1065

_mz, _fr = markowitz_optimize(_tkrs, _means, _cov, _RF, n_samples=300)
_rp = risk_parity_optimize(_tkrs, _means, _cov, _RF)
_bl = black_litterman_optimize(_tkrs, _means, _cov, _RF, [("BOVA11", 0.18)])
_ew = equal_weight(_tkrs, _means, _cov, _RF)


def test_markowitz_beats_ew_sharpe():
    assert _mz.sharpe >= _ew.sharpe


def test_markowitz_weights_sum_100():
    assert abs(sum(w.weight_pct for w in _mz.weights) - 100) < 0.1


def test_rp_weights_sum_100():
    assert abs(sum(w.weight_pct for w in _rp.weights) - 100) < 0.1


def test_bl_weights_sum_100():
    assert abs(sum(w.weight_pct for w in _bl.weights) - 100) < 0.1


def test_ew_weights_equal():
    pcts = [w.weight_pct for w in _ew.weights]
    assert max(pcts) - min(pcts) < 0.01


def test_frontier_not_empty():
    assert len(_fr) > 0


def test_frontier_has_vol_ret_sharpe():
    p = _fr[0]
    assert all(k in p for k in ["vol", "ret", "sharpe"])


def test_markowitz_to_dict():
    d = _mz.to_dict()
    assert all(k in d for k in ["method", "sharpe", "weights", "annual_return_pct", "volatility_pct"])


def test_risk_parity_contributions_uniform():
    w = [aw.weight for aw in _rp.weights]
    wcov = _mat_vec(_cov, w)
    pv = _dot(w, wcov)
    rc = [w[i] * wcov[i] / pv for i in range(N)] if pv > 0 else [1 / N] * N
    std_rp = math.sqrt(sum((r - 1 / N) ** 2 for r in rc) / N)
    w2 = [1 / N] * N
    wc2 = _mat_vec(_cov, w2)
    pv2 = _dot(w2, wc2)
    rc2 = [w2[i] * wc2[i] / pv2 for i in range(N)] if pv2 > 0 else [1 / N] * N
    std_ew = math.sqrt(sum((r - 1 / N) ** 2 for r in rc2) / N)
    assert std_rp <= std_ew + 1e-6


def test_bl_has_notes():
    assert len(_bl.notes) > 0


def test_bl_view_in_notes():
    assert any("18" in n or "BOVA11" in n for n in _bl.notes)


def test_gauss_jordan_identity():
    I = [[2.0, 1.0], [5.0, 3.0]]
    inv = _gauss_jordan_inverse(I)
    # I × inv deve ser identidade
    prod = [[sum(I[i][k] * inv[k][j] for k in range(2)) for j in range(2)] for i in range(2)]
    assert abs(prod[0][0] - 1.0) < 1e-9
    assert abs(prod[1][1] - 1.0) < 1e-9
    assert abs(prod[0][1]) < 1e-9


def test_all_methods_positive_vol():
    for m in [_mz, _rp, _bl, _ew]:
        assert m.volatility > 0


def test_portfolios_long_only():
    for m in [_mz, _rp, _bl, _ew]:
        for aw in m.weights:
            assert aw.weight >= -1e-9
