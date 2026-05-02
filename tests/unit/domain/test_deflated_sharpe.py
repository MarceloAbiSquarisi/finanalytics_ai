"""Testes do Deflated Sharpe Ratio (Lopez de Prado, 2014) — R5."""

from __future__ import annotations

import math

import pytest

from finanalytics_ai.domain.backtesting.metrics import (
    deflated_sharpe,
    expected_max_sharpe,
    roc_auc,
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


# ── ROC / AUC (R5 — backtest classificador binário) ───────────────────────────


class TestRocAuc:
    def test_perfect_classifier(self) -> None:
        """Scores ordenam labels perfeitamente — AUC=1."""
        # 3 winners (score alto) seguidos de 3 losers (score baixo)
        result = roc_auc(
            y_true=[True, True, True, False, False, False],
            y_score=[0.9, 0.8, 0.7, 0.3, 0.2, 0.1],
        )
        assert result is not None
        assert result.auc == pytest.approx(1.0)
        assert result.n_positive == 3
        assert result.n_negative == 3
        # Curva: (0,0) → (0,1) → (1,1) — passa pelo canto superior esquerdo
        assert (0.0, 1.0) in [(round(f, 4), round(t, 4)) for f, t in result.curve]
        assert (1.0, 1.0) in [(round(f, 4), round(t, 4)) for f, t in result.curve]

    def test_anti_perfect_classifier(self) -> None:
        """Scores invertidos — AUC=0 (sinal anti-perfeito)."""
        result = roc_auc(
            y_true=[True, True, False, False],
            y_score=[0.1, 0.2, 0.8, 0.9],  # losers com score MAIS alto
        )
        assert result is not None
        assert result.auc == pytest.approx(0.0, abs=1e-9)

    def test_random_classifier_is_half(self) -> None:
        """Scores onde winners ocupam ranks balanceados — AUC = 0.5 exato.

        4 winners em ranks {1, 4, 5, 8} (sum=18 = n_pos*(n_pos+n_neg+1)/2
        com n_pos=n_neg=4 — fórmula Mann-Whitney AUC=0.5).
        """
        # Ordem por score DESC: W L L W W L L W
        result = roc_auc(
            y_true=[True, False, False, True, True, False, False, True],
            y_score=[8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0],
        )
        assert result is not None
        assert result.auc == pytest.approx(0.5)

    def test_only_winners_returns_nan_auc(self) -> None:
        """N_neg=0 — AUC indefinida; struct retornada com NaN."""
        result = roc_auc(y_true=[True, True, True], y_score=[1.0, 2.0, 3.0])
        assert result is not None
        assert math.isnan(result.auc)
        assert result.n_positive == 3
        assert result.n_negative == 0
        assert result.curve == []

    def test_only_losers_returns_nan_auc(self) -> None:
        result = roc_auc(y_true=[False, False, False], y_score=[1.0, 2.0, 3.0])
        assert result is not None
        assert math.isnan(result.auc)
        assert result.n_negative == 3

    def test_empty_returns_none(self) -> None:
        assert roc_auc(y_true=[], y_score=[]) is None

    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match=r"y_true.*!=.*y_score"):
            roc_auc(y_true=[True, False], y_score=[0.5])

    def test_nan_score_raises(self) -> None:
        with pytest.raises(ValueError, match="NaN"):
            roc_auc(y_true=[True, False], y_score=[0.5, float("nan")])

    def test_tied_scores_handled_correctly(self) -> None:
        """Múltiplos pontos com mesmo score — devem virar 1 ponto na curva."""
        # 2 winners + 2 losers, todos com mesmo score → AUC=0.5 (sem discriminação)
        result = roc_auc(
            y_true=[True, True, False, False],
            y_score=[0.5, 0.5, 0.5, 0.5],
        )
        assert result is not None
        assert result.auc == pytest.approx(0.5)

    def test_to_dict_roundtrip(self) -> None:
        result = roc_auc(
            y_true=[True, False, True, False],
            y_score=[0.9, 0.5, 0.8, 0.3],
        )
        assert result is not None
        d = result.to_dict()
        assert "auc" in d
        assert "curve" in d
        assert d["n_positive"] == 2
        assert d["n_negative"] == 2
        # Curve elements são listas [fpr, tpr]
        assert all(isinstance(p, list) and len(p) == 2 for p in d["curve"])

    def test_curve_starts_at_origin(self) -> None:
        result = roc_auc(
            y_true=[True, False, True, False],
            y_score=[4.0, 3.0, 2.0, 1.0],
        )
        assert result is not None
        assert result.curve[0] == (0.0, 0.0)

    def test_realistic_strategy_partial_skill(self) -> None:
        """Strategy com skill parcial — AUC entre 0.5 e 1.0.

        4 winners scoram alto (0.7-0.9) + 1 winner score baixo (0.4).
        3 losers scoram baixo (0.2-0.6). Skill verdadeira mas imperfeita.
        Mann-Whitney: 14/(5×3) ≈ 0.933.
        """
        scores = [0.9, 0.85, 0.8, 0.7, 0.6, 0.4, 0.3, 0.2]
        labels = [True, True, True, True, False, True, False, False]
        result = roc_auc(y_true=labels, y_score=scores)
        assert result is not None
        assert result.auc == pytest.approx(14 / 15)
        assert 0.5 < result.auc < 1.0
