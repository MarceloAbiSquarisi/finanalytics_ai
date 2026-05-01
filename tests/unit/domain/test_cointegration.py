"""
Testes do Engle-Granger 2-step + half-life (R3.1).

Cobertura:
  compute_hedge_ratio
    - synthetic A = 2*B (sem ruido) -> beta = 2
    - shape mismatch -> ValueError
    - input vazio -> ValueError
    - B identicamente zero -> ValueError
  compute_residuals
    - residual_t = A_t - beta*B_t
  adf_test
    - serie estacionaria (white noise) -> p_value baixo (< 0.05)
    - random walk -> p_value alto (> 0.10 quase sempre)
    - serie curta -> ValueError
  compute_half_life
    - serie OU mean-reverting -> half_life > 0 finito
    - random walk -> None (lambda <= 0)
    - serie curta -> None
  engle_granger
    - par cointegrado sintetico (X, X*beta + ruido AR(1)) -> cointegrated=True
    - 2 random walks independentes -> cointegrated=False (esperado em > 90% das seeds)
    - shape mismatch -> ValueError

RNG seed fixo p/ reprodutibilidade. statsmodels.adfuller e' deterministico
dado o input.
"""

from __future__ import annotations

import numpy as np
import pytest

from finanalytics_ai.domain.pairs.cointegration import (
    CointegrationResult,
    adf_test,
    compute_half_life,
    compute_hedge_ratio,
    compute_residuals,
    engle_granger,
)


# ── compute_hedge_ratio ───────────────────────────────────────────────────────


class TestHedgeRatio:
    def test_exact_ratio_no_noise(self) -> None:
        rng = np.random.default_rng(42)
        b = rng.uniform(10, 100, size=200)
        a = 2.0 * b
        beta = compute_hedge_ratio(a, b)
        assert beta == pytest.approx(2.0, abs=1e-9)

    def test_with_noise_close_to_true_beta(self) -> None:
        rng = np.random.default_rng(0)
        b = rng.uniform(10, 100, size=500)
        a = 1.5 * b + rng.normal(0, 0.1, size=500)
        beta = compute_hedge_ratio(a, b)
        assert beta == pytest.approx(1.5, abs=0.02)

    def test_shape_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="shape mismatch"):
            compute_hedge_ratio([1, 2, 3], [1, 2])

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            compute_hedge_ratio([], [])

    def test_zero_b_raises(self) -> None:
        with pytest.raises(ValueError, match="identically zero"):
            compute_hedge_ratio([1.0, 2.0, 3.0], [0.0, 0.0, 0.0])


# ── compute_residuals ────────────────────────────────────────────────────────


class TestResiduals:
    def test_basic(self) -> None:
        a = np.array([10.0, 20.0, 30.0])
        b = np.array([5.0, 10.0, 15.0])
        r = compute_residuals(a, b, beta=2.0)
        np.testing.assert_allclose(r, [0.0, 0.0, 0.0])

    def test_with_offset(self) -> None:
        a = np.array([10.0, 21.0, 31.0])
        b = np.array([5.0, 10.0, 15.0])
        r = compute_residuals(a, b, beta=2.0)
        np.testing.assert_allclose(r, [0.0, 1.0, 1.0])


# ── adf_test ──────────────────────────────────────────────────────────────────


class TestADF:
    def test_white_noise_is_stationary(self) -> None:
        rng = np.random.default_rng(7)
        series = rng.normal(0, 1, size=300)
        p = adf_test(series)
        assert p < 0.05  # rejeita H0

    def test_random_walk_not_stationary(self) -> None:
        rng = np.random.default_rng(7)
        steps = rng.normal(0, 1, size=300)
        rw = np.cumsum(steps)
        p = adf_test(rw)
        assert p > 0.10  # falha em rejeitar H0

    def test_too_short_raises(self) -> None:
        with pytest.raises(ValueError, match="too short"):
            adf_test([1.0, 2.0, 3.0])


# ── compute_half_life ────────────────────────────────────────────────────────


class TestHalfLife:
    def test_ornstein_uhlenbeck_finite_half_life(self) -> None:
        # OU: x_{t+1} = (1-lambda)*x_t + sigma*epsilon, esperado half-life = ln(2)/lambda
        rng = np.random.default_rng(42)
        n = 500
        true_lambda = 0.1  # half_life teorico = 6.93
        x = np.zeros(n)
        for t in range(1, n):
            x[t] = (1 - true_lambda) * x[t - 1] + rng.normal(0, 1)
        hl = compute_half_life(x)
        assert hl is not None
        # Estimado deve ficar na vizinhanca do teorico (tolerancia ampla)
        assert 3.0 < hl < 15.0

    def test_random_walk_returns_none(self) -> None:
        # Random walk -> coef >= 0, half-life undefined
        rng = np.random.default_rng(7)
        rw = np.cumsum(rng.normal(0, 1, size=500))
        hl = compute_half_life(rw)
        # Pode ser None ou MUITO grande; aceitamos ambos
        assert hl is None or hl > 100

    def test_too_short_returns_none(self) -> None:
        assert compute_half_life([1.0]) is None
        assert compute_half_life([1.0, 2.0]) is None


# ── engle_granger orchestrator ────────────────────────────────────────────────


class TestEngleGranger:
    def test_cointegrated_pair_detected(self) -> None:
        """Constroi par cointegrado: A_t = 2*B_t + spread_t (spread OU mean-rev)."""
        rng = np.random.default_rng(123)
        n = 500
        # B = random walk (I(1))
        b = np.cumsum(rng.normal(0, 1, size=n)) + 100
        # spread OU mean-reverting (estacionario)
        spread = np.zeros(n)
        for t in range(1, n):
            spread[t] = 0.85 * spread[t - 1] + rng.normal(0, 0.5)
        # A = beta*B + spread -> cointegrado por construcao
        a = 2.0 * b + spread

        result = engle_granger(a, b)

        assert isinstance(result, CointegrationResult)
        assert result.cointegrated is True
        assert result.p_value_adf < 0.05
        assert result.beta == pytest.approx(2.0, abs=0.05)
        assert result.half_life is not None
        assert result.half_life > 0
        assert result.sample_size == n

    def test_independent_random_walks_not_cointegrated(self) -> None:
        """2 random walks independentes — esperado p_value alto (> 0.05)."""
        rng = np.random.default_rng(99)
        n = 500
        a = np.cumsum(rng.normal(0, 1, size=n)) + 50
        b = np.cumsum(rng.normal(0, 1, size=n)) + 80

        result = engle_granger(a, b)
        # Esperamos NAO cointegrado, mas spurious cointegration acontece em
        # ~5% das simulacoes. Aceitar tanto False quanto p > 0.01 e' robusto.
        assert result.cointegrated is False or result.p_value_adf > 0.01

    def test_shape_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="shape mismatch"):
            engle_granger([1.0, 2.0, 3.0], [1.0, 2.0])

    def test_too_small_sample_raises(self) -> None:
        with pytest.raises(ValueError, match="sample too small"):
            engle_granger([1.0] * 15, [1.0] * 15)

    def test_p_threshold_applied(self) -> None:
        """Threshold custom — pode flipar cointegrated."""
        rng = np.random.default_rng(123)
        n = 500
        b = np.cumsum(rng.normal(0, 1, size=n)) + 100
        spread = np.zeros(n)
        for t in range(1, n):
            spread[t] = 0.85 * spread[t - 1] + rng.normal(0, 0.5)
        a = 2.0 * b + spread

        # Threshold normal — esperado True
        r1 = engle_granger(a, b, p_threshold=0.05)
        # Threshold ultra restrito (1e-10) — quase sempre False mesmo cointegrado
        r2 = engle_granger(a, b, p_threshold=1e-10)
        assert r1.p_value_adf == r2.p_value_adf  # mesmo p_value
        assert r1.cointegrated is True
        # r2 pode ser True OU False dependendo da magnitude exata; o ponto
        # e' que threshold fica respeitado
        assert r2.cointegrated == (r2.p_value_adf < 1e-10)
