"""
Testes da logica pura de pairs trading (R3.2.A).

Cobertura:
  compute_zscore
    - historico vazio / 1 elemento -> None
    - std == 0 (degenerado) -> None
    - calculo correto com synthetic data
  PairThresholds
    - aceita defaults
    - rejeita ordem invalida (z_exit > z_entry, etc.)
  decide_pair_action — state machine completa
    - NONE + |Z| < entry -> NONE
    - NONE + Z > entry -> OPEN_SHORT_SPREAD
    - NONE + Z < -entry -> OPEN_LONG_SPREAD
    - SHORT_SPREAD + Z entre exit e stop -> NONE (segura)
    - SHORT_SPREAD + Z < exit -> CLOSE
    - SHORT_SPREAD + |Z| > stop -> STOP
    - LONG_SPREAD + Z > -exit -> CLOSE
    - LONG_SPREAD + |Z| > stop -> STOP
    - boundary z = exatamente entry/exit/stop (testa < vs <=)
  apply_bonferroni
    - n_pairs > 0 divide alpha
    - n_pairs <= 0 retorna alpha sem mudar
  validate_pair_filters
    - p > alpha_bonf -> bloqueado (bonferroni)
    - half_life None -> bloqueado
    - half_life < min -> bloqueado
    - half_life > max -> bloqueado
    - tudo ok -> (True, None)
"""

from __future__ import annotations

import math

import pytest

from finanalytics_ai.domain.pairs import (
    PairAction,
    PairPosition,
    PairThresholds,
    apply_bonferroni,
    compute_zscore,
    decide_pair_action,
    validate_pair_filters,
)


# ── compute_zscore ────────────────────────────────────────────────────────────


class TestZscore:
    def test_empty_history_returns_none(self) -> None:
        assert compute_zscore([], 1.0) is None

    def test_single_element_returns_none(self) -> None:
        assert compute_zscore([5.0], 5.0) is None

    def test_constant_history_returns_none(self) -> None:
        # std = 0
        assert compute_zscore([3.0, 3.0, 3.0, 3.0], 4.0) is None

    def test_known_zscore(self) -> None:
        # mean=10, var=2.5 (sample), std=sqrt(2.5)
        # z(13) = (13-10) / sqrt(2.5) ≈ 1.897
        history = [8.0, 9.0, 10.0, 11.0, 12.0]
        z = compute_zscore(history, 13.0)
        assert z is not None
        assert z == pytest.approx(3.0 / math.sqrt(2.5), abs=1e-6)

    def test_negative_zscore(self) -> None:
        # spread atual abaixo da media
        history = [10.0, 11.0, 12.0, 13.0, 14.0]
        # mean=12, std=sample_std≈1.581
        z = compute_zscore(history, 9.0)
        assert z is not None
        assert z < 0


# ── PairThresholds ───────────────────────────────────────────────────────────


class TestThresholds:
    def test_defaults_valid(self) -> None:
        t = PairThresholds()
        assert t.z_exit == 0.5
        assert t.z_entry == 2.0
        assert t.z_stop == 4.0

    def test_custom_valid(self) -> None:
        t = PairThresholds(z_exit=0.3, z_entry=1.5, z_stop=3.0)
        assert t.z_exit == 0.3

    def test_invalid_order_raises(self) -> None:
        # exit > entry: invalido
        with pytest.raises(ValueError):
            PairThresholds(z_exit=2.5, z_entry=2.0, z_stop=4.0)
        # entry > stop: invalido
        with pytest.raises(ValueError):
            PairThresholds(z_exit=0.5, z_entry=5.0, z_stop=4.0)
        # exit negativo: invalido (precisa ser > 0)
        with pytest.raises(ValueError):
            PairThresholds(z_exit=-0.1, z_entry=2.0, z_stop=4.0)


# ── decide_pair_action — sem posicao ─────────────────────────────────────────


class TestDecideNonePosition:
    def test_z_low_returns_none(self) -> None:
        # Z dentro da banda [-z_entry, z_entry] -> nao abre
        assert decide_pair_action(0.0, PairPosition.NONE) == PairAction.NONE
        assert decide_pair_action(1.5, PairPosition.NONE) == PairAction.NONE
        assert decide_pair_action(-1.99, PairPosition.NONE) == PairAction.NONE

    def test_z_high_opens_short_spread(self) -> None:
        # Z > z_entry -> spread alto, aposta convergencia (vende A, compra B)
        assert decide_pair_action(2.5, PairPosition.NONE) == PairAction.OPEN_SHORT_SPREAD
        assert decide_pair_action(3.0, PairPosition.NONE) == PairAction.OPEN_SHORT_SPREAD

    def test_z_low_opens_long_spread(self) -> None:
        # Z < -z_entry -> spread baixo (compra A, vende B)
        assert decide_pair_action(-2.5, PairPosition.NONE) == PairAction.OPEN_LONG_SPREAD
        assert decide_pair_action(-3.0, PairPosition.NONE) == PairAction.OPEN_LONG_SPREAD

    def test_boundary_at_entry_does_not_open(self) -> None:
        # z = exatamente entry -> nao abre (regra estrita >)
        assert decide_pair_action(2.0, PairPosition.NONE) == PairAction.NONE
        assert decide_pair_action(-2.0, PairPosition.NONE) == PairAction.NONE


# ── decide_pair_action — SHORT_SPREAD aberto ─────────────────────────────────


class TestDecideShortSpread:
    def test_z_in_band_holds(self) -> None:
        # Z entre exit e stop -> aguarda
        assert decide_pair_action(1.5, PairPosition.SHORT_SPREAD) == PairAction.NONE
        assert decide_pair_action(2.5, PairPosition.SHORT_SPREAD) == PairAction.NONE

    def test_z_below_exit_closes(self) -> None:
        # Z desceu abaixo de z_exit -> mean reversion atingida
        assert decide_pair_action(0.4, PairPosition.SHORT_SPREAD) == PairAction.CLOSE
        assert decide_pair_action(0.0, PairPosition.SHORT_SPREAD) == PairAction.CLOSE
        assert decide_pair_action(-1.0, PairPosition.SHORT_SPREAD) == PairAction.CLOSE

    def test_z_above_stop_stops(self) -> None:
        # |Z| > z_stop -> stop forcado
        assert decide_pair_action(4.5, PairPosition.SHORT_SPREAD) == PairAction.STOP

    def test_negative_z_above_stop_also_stops(self) -> None:
        # |Z| > stop conta independente de signal
        assert decide_pair_action(-4.5, PairPosition.SHORT_SPREAD) == PairAction.STOP


# ── decide_pair_action — LONG_SPREAD aberto ──────────────────────────────────


class TestDecideLongSpread:
    def test_z_below_minus_exit_holds(self) -> None:
        # Long aposta que Z volta pra cima (>= -exit)
        # Z = -1 ainda esta abaixo de -0.5, segura
        assert decide_pair_action(-1.0, PairPosition.LONG_SPREAD) == PairAction.NONE
        assert decide_pair_action(-2.0, PairPosition.LONG_SPREAD) == PairAction.NONE

    def test_z_above_minus_exit_closes(self) -> None:
        # Z subiu acima de -exit -> reversion
        assert decide_pair_action(0.0, PairPosition.LONG_SPREAD) == PairAction.CLOSE
        assert decide_pair_action(-0.4, PairPosition.LONG_SPREAD) == PairAction.CLOSE

    def test_extreme_negative_z_stops(self) -> None:
        # Z continuou caindo - stop loss
        assert decide_pair_action(-4.5, PairPosition.LONG_SPREAD) == PairAction.STOP

    def test_extreme_positive_z_also_stops(self) -> None:
        # |Z| > stop e' simetrico
        assert decide_pair_action(4.5, PairPosition.LONG_SPREAD) == PairAction.STOP


# ── apply_bonferroni ─────────────────────────────────────────────────────────


class TestBonferroni:
    def test_basic_division(self) -> None:
        assert apply_bonferroni(0.05, 28) == pytest.approx(0.05 / 28)

    def test_n_pairs_zero_returns_unchanged(self) -> None:
        assert apply_bonferroni(0.05, 0) == 0.05

    def test_n_pairs_negative_returns_unchanged(self) -> None:
        assert apply_bonferroni(0.05, -1) == 0.05

    def test_n_pairs_one(self) -> None:
        # 1 par -> sem ajuste real
        assert apply_bonferroni(0.05, 1) == 0.05


# ── validate_pair_filters ────────────────────────────────────────────────────


class TestValidateFilters:
    def test_all_pass(self) -> None:
        ok, reason = validate_pair_filters(p_value=0.001, half_life=15.0, n_pairs_tested=28)
        assert ok is True
        assert reason is None

    def test_p_above_bonferroni_fails(self) -> None:
        # alpha_eff = 0.05/28 ≈ 0.00179. p=0.04 falha
        ok, reason = validate_pair_filters(p_value=0.04, half_life=15.0, n_pairs_tested=28)
        assert ok is False
        assert "bonferroni_failed" in reason

    def test_p_below_bonferroni_passes(self) -> None:
        # p=0.001 < alpha_eff
        ok, reason = validate_pair_filters(p_value=0.001, half_life=10.0, n_pairs_tested=28)
        assert ok is True

    def test_half_life_none_fails(self) -> None:
        ok, reason = validate_pair_filters(p_value=0.0001, half_life=None, n_pairs_tested=28)
        assert ok is False
        assert "half_life_undefined" in reason

    def test_half_life_too_short_fails(self) -> None:
        ok, reason = validate_pair_filters(p_value=0.0001, half_life=2.0, n_pairs_tested=28)
        assert ok is False
        assert "half_life_too_short" in reason

    def test_half_life_too_long_fails(self) -> None:
        ok, reason = validate_pair_filters(p_value=0.0001, half_life=60.0, n_pairs_tested=28)
        assert ok is False
        assert "half_life_too_long" in reason

    def test_custom_half_life_range(self) -> None:
        # half_life=8 OK no default (5-30) mas falha em (10-30)
        ok, _ = validate_pair_filters(
            p_value=0.0001,
            half_life=8.0,
            n_pairs_tested=28,
            min_half_life=10.0,
        )
        assert ok is False

    def test_real_world_cmin3_vale3(self) -> None:
        """
        Caso real do screening 01/mai: CMIN3-VALE3 com p=0.045, half-life=27d,
        em 28 pares testados.
        """
        ok, reason = validate_pair_filters(p_value=0.0452, half_life=27.0, n_pairs_tested=28)
        # alpha_eff = 0.05/28 ≈ 0.00179. p=0.0452 >> alpha_eff -> reject
        assert ok is False
        assert "bonferroni" in reason

    def test_real_world_strict_p(self) -> None:
        """Mesmo caso mas com p hipotetico apertado (0.0001) -> pass."""
        ok, reason = validate_pair_filters(p_value=0.0001, half_life=27.0, n_pairs_tested=28)
        assert ok is True
