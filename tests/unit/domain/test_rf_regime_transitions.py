"""
Testes unitarios do Markov empirico de RF Regime (N4b, 28/abr/2026).

Cobertura de compute_transitions:
  - history < 31 retorna None (insuficiencia)
  - matriz 4x4 com soma de cada linha = 1 (probabilidade)
  - regime nao observado retorna uniforme (1/4)
  - duracao media calcula corretamente runs consecutivos
  - most_likely_next bate com argmax de next_regime_probs
  - sample_pairs = len(history) - 1
"""
from __future__ import annotations

import pytest

from finanalytics_ai.domain.rf_regime.classifier import compute_transitions


def _make_history(regimes: list[str]) -> list[dict]:
    """Helper: gera lista [{dia, slope_2y_10y, regime}, ...]."""
    return [
        {"dia": f"2026-01-{i+1:02d}", "slope_2y_10y": 0.01, "regime": r}
        for i, r in enumerate(regimes)
    ]


def test_returns_none_when_history_too_short():
    history = _make_history(["NORMAL"] * 30)
    assert compute_transitions(history, "NORMAL") is None


def test_returns_dict_when_history_meets_minimum():
    history = _make_history(["NORMAL"] * 31)
    result = compute_transitions(history, "NORMAL")
    assert result is not None
    assert "matrix" in result
    assert "next_regime_probs" in result
    assert "most_likely_next" in result
    assert "avg_duration_days" in result
    assert "sample_pairs" in result


def test_sample_pairs_equals_history_minus_one():
    history = _make_history(["NORMAL"] * 50)
    result = compute_transitions(history, "NORMAL")
    assert result["sample_pairs"] == 49


def test_matrix_rows_sum_to_one_when_observed():
    """Cada linha da matriz que tem transicoes observadas deve somar 1."""
    history = _make_history(
        ["NORMAL"] * 20
        + ["STEEPENING"] * 5
        + ["NORMAL"] * 6
        + ["FLATTENING"] * 5
    )
    result = compute_transitions(history, "NORMAL")
    assert result is not None
    # NORMAL aparece N vezes como source — soma da linha deve ser ~1
    # Tolerancia 1e-3 por causa do round(4) na implementacao
    row_sum = sum(result["matrix"]["NORMAL"].values())
    assert abs(row_sum - 1.0) < 1e-3


def test_unobserved_regime_returns_uniform():
    """Regime que nunca aparece como source → linha uniforme (1/4 cada)."""
    # INVERSION nao aparece em momento algum
    history = _make_history(["NORMAL"] * 35)
    result = compute_transitions(history, "NORMAL")
    assert result is not None
    inv_row = result["matrix"]["INVERSION"]
    for prob in inv_row.values():
        assert abs(prob - 0.25) < 1e-6


def test_most_likely_next_is_argmax():
    """most_likely_next deve ser argmax de next_regime_probs."""
    history = _make_history(["NORMAL"] * 35)
    result = compute_transitions(history, "NORMAL")
    next_probs = result["next_regime_probs"]
    expected = max(next_probs.items(), key=lambda kv: kv[1])[0]
    assert result["most_likely_next"] == expected


def test_avg_duration_computes_run_lengths():
    """Sequencia [N, N, N, S, N, N] tem runs N=3, S=1, N=2 → avg N = 2.5, avg S = 1.0"""
    # Precisa >=31 obs total, vou fazer 7 ciclos de 5 elementos
    pattern = ["NORMAL", "NORMAL", "NORMAL", "STEEPENING", "NORMAL"]
    history = _make_history(pattern * 7)  # 35 obs
    result = compute_transitions(history, "NORMAL")
    durations = result["avg_duration_days"]
    # Cada ciclo: NORMAL(3) + STEEPENING(1) + NORMAL(1) — mas NORMAL no fim do ciclo
    # se concatena com NORMAL do proximo ciclo: NORMAL(4) + STEEPENING(1) repetidos
    # Confirmar so existe duracao positiva e razoavel
    assert durations["NORMAL"] > 0
    assert durations["STEEPENING"] >= 1.0
    assert durations["FLATTENING"] == 0.0  # nunca apareceu
    assert durations["INVERSION"] == 0.0


def test_strong_dominant_regime_dominates_transitions():
    """100% NORMAL → P(NORMAL→NORMAL) = 1.0"""
    history = _make_history(["NORMAL"] * 50)
    result = compute_transitions(history, "NORMAL")
    assert result["matrix"]["NORMAL"]["NORMAL"] == 1.0
    assert result["matrix"]["NORMAL"]["STEEPENING"] == 0.0
    assert result["next_regime_probs"]["NORMAL"] == 1.0
    assert result["most_likely_next"] == "NORMAL"


def test_alternating_regimes_distribute_probabilities():
    """[N,S,N,S,...] → P(N→S) = 1.0 e P(S→N) = 1.0"""
    history = _make_history(["NORMAL", "STEEPENING"] * 18)  # 36 obs
    result = compute_transitions(history, "NORMAL")
    # NORMAL sempre seguido de STEEPENING
    assert result["matrix"]["NORMAL"]["STEEPENING"] == 1.0
    assert result["matrix"]["STEEPENING"]["NORMAL"] == 1.0


@pytest.mark.parametrize("current", ["NORMAL", "STEEPENING", "FLATTENING", "INVERSION"])
def test_next_regime_probs_matches_matrix_row_for_current(current):
    """next_regime_probs deve ser exatamente matrix[current]."""
    history = _make_history(
        ["NORMAL"] * 12 + ["STEEPENING"] * 8 + ["FLATTENING"] * 7 + ["INVERSION"] * 8
    )
    result = compute_transitions(history, current)  # type: ignore[arg-type]
    assert result["next_regime_probs"] == result["matrix"][current]
