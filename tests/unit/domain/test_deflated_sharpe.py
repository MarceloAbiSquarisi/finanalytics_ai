"""Testes do Deflated Sharpe Ratio (Lopez de Prado, 2014) — R5."""

from __future__ import annotations

from finanalytics_ai.domain.backtesting.metrics import (
    deflated_sharpe,
    expected_max_sharpe,
    sample_skew_kurtosis,
)


class TestExpectedMaxSharpe:
    def test_n2_positive(self) -> None:
        # E[max] cresce com N — N=2 e o piso util
        assert expected_max_sharpe(2) > 0.0

    def test_n_increasing_grows(self) -> None:
        # Mais trials => maior expected max => mais penalizacao no DSR
        assert expected_max_sharpe(100) > expected_max_sharpe(10)
        assert expected_max_sharpe(500) > expected_max_sharpe(100)

    def test_n_lt_2_returns_zero(self) -> None:
        assert expected_max_sharpe(0) == 0.0
        assert expected_max_sharpe(1) == 0.0


class TestDeflatedSharpe:
    def test_high_sr_few_trials_passes(self) -> None:
        # SR 2.5 anualizado com 1 trial e 1000 dias deve ter prob_real alta
        r = deflated_sharpe(observed_sharpe=2.5, num_trials=1, sample_size=1000)
        assert r.prob_real > 0.95

    def test_modest_sr_many_trials_fails(self) -> None:
        # SR 1.0 anualizado obtido apos grid search 200 candidatos
        # provavelmente e ruido — DSR deve cair drasticamente
        r = deflated_sharpe(observed_sharpe=1.0, num_trials=200, sample_size=500)
        assert r.prob_real < 0.5

    def test_dsr_decreases_with_more_trials(self) -> None:
        # Mesma SR, mesma amostra; aumentar trials reduz prob_real
        r10 = deflated_sharpe(observed_sharpe=1.5, num_trials=10, sample_size=500)
        r500 = deflated_sharpe(observed_sharpe=1.5, num_trials=500, sample_size=500)
        assert r10.prob_real > r500.prob_real

    def test_negative_skew_penalizes(self) -> None:
        # Strategy com fat tails de perda (negative skew) deve ter DSR menor
        normal = deflated_sharpe(2.0, num_trials=20, sample_size=500, skew=0.0, kurtosis=3.0)
        bad_tails = deflated_sharpe(2.0, num_trials=20, sample_size=500, skew=-0.5, kurtosis=6.0)
        assert bad_tails.deflated_sharpe < normal.deflated_sharpe

    def test_to_dict_keys(self) -> None:
        r = deflated_sharpe(1.5, num_trials=10, sample_size=300)
        d = r.to_dict()
        for key in [
            "observed_sharpe",
            "deflated_sharpe",
            "prob_real",
            "e_max_sharpe",
            "num_trials",
        ]:
            assert key in d
        assert d["num_trials"] == 10

    def test_sample_size_too_small_returns_neutral(self) -> None:
        r = deflated_sharpe(2.0, num_trials=10, sample_size=1)
        # Com T=1 nao da pra inferir nada — fallback prob=0.5
        assert r.prob_real == 0.5

    def test_prob_in_unit_interval(self) -> None:
        # prob_real e CDF normal — sempre em [0, 1]
        for sr in [-2.0, 0.0, 0.5, 1.5, 3.0]:
            for n in [2, 50, 200]:
                r = deflated_sharpe(sr, num_trials=n, sample_size=300)
                assert 0.0 <= r.prob_real <= 1.0


class TestSampleSkewKurtosis:
    def test_normal_returns_kurt_close_to_3(self) -> None:
        # Distribuicao normal sintetica via random.gauss (seed fixo p/ reprodutibilidade).
        # Pearson kurtosis de normal pura = 3.0; com 1000 amostras tolerancia ~0.5.
        import random

        rng = random.Random(42)
        normal_returns = [rng.gauss(0.0, 0.01) for _ in range(1000)]
        skew, kurt = sample_skew_kurtosis(normal_returns)
        assert abs(skew) < 0.3, f"skew={skew}"
        assert 2.5 < kurt < 3.5, f"kurt={kurt}"

    def test_constant_returns_zero_skew_default_kurt(self) -> None:
        # Variance zero — fallback (0, 3)
        skew, kurt = sample_skew_kurtosis([0.001] * 50)
        assert skew == 0.0
        assert kurt == 3.0

    def test_too_few_returns_default(self) -> None:
        skew, kurt = sample_skew_kurtosis([0.01, 0.02])
        assert (skew, kurt) == (0.0, 3.0)

    def test_negative_skew_detected(self) -> None:
        # Lista com cauda esquerda longa (poucos retornos muito negativos)
        returns = [0.001] * 90 + [-0.05] * 10
        skew, _ = sample_skew_kurtosis(returns)
        assert skew < 0
