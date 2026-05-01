"""
DSR aplicado ao walk-forward (R5 follow-up).

Por que existe:
  Walk-forward divide IS/OOS, mas a SELECAO no IS via grid search ainda
  introduz multiple testing bias. Aplicar DSR sobre cada fold OOS corrige
  isso usando: N=valid_runs do IS, T=oos_bars-1, observed_sharpe=oos_sharpe.

Cobertura:
  - Fold com OOS valido + N >= 2 + bars > 30 -> oos_dsr populado
  - Fold com OOS invalido (poucos trades) -> oos_dsr None
  - Fold com num_is_trials < 2 -> oos_dsr None (nao da pra deflacionar)
  - Fold com OOS bars <= 30 -> oos_dsr None
  - Agregacao no WalkForwardResult.deflated_sharpe quando ao menos 1 fold tem DSR
  - folds_real conta folds com prob_real >= 0.95
"""

from __future__ import annotations

import math
import random

import pytest

from finanalytics_ai.domain.backtesting.optimizer import (
    OptimizationObjective,
    walk_forward,
)


def _bars(n: int = 800, pattern: str = "zigzag") -> list[dict]:
    """
    Bars sineticos com 2 frequencias + ruido — produz zigzag denso o
    suficiente para RSI gerar >= MIN_TRADES (3) em cada janela OOS de 60-80 bars.
    'flat' fica mantido para edge case (zero trades).
    """
    rnd = random.Random(42)
    out = []
    for i in range(n):
        if pattern == "zigzag":
            p = (
                100.0
                + 20.0 * math.sin(i * 0.5)
                + 8.0 * math.sin(i * 1.7)
                + rnd.uniform(-2.0, 2.0)
                + i * 0.02
            )
        else:  # flat
            p = 100.0
        out.append({
            "time": 1700_000_000 + i * 86400,
            "open": p,
            "high": p + 1.5,
            "low": p - 1.5,
            "close": p,
            "volume": 1_000_000.0,
        })
    return out


class TestWalkForwardDSR:
    def test_result_has_deflated_sharpe_field(self):
        result = walk_forward(
            bars=_bars(800),
            strategy_name="rsi",
            ticker="PETR4",
            objective=OptimizationObjective.SHARPE,
            n_splits=3,
        )
        # Atributo sempre presente (pode ser None se nenhum fold gerou DSR)
        assert hasattr(result, "deflated_sharpe")

    def test_to_dict_includes_deflated_sharpe(self):
        result = walk_forward(
            bars=_bars(800),
            strategy_name="rsi",
            ticker="PETR4",
            objective=OptimizationObjective.SHARPE,
            n_splits=3,
        )
        d = result.to_dict()
        assert "deflated_sharpe" in d

    def test_fold_has_num_is_trials_populated(self):
        result = walk_forward(
            bars=_bars(800),
            strategy_name="rsi",
            ticker="PETR4",
            objective=OptimizationObjective.SHARPE,
            n_splits=3,
        )
        # RSI tem 4*3*3=36 combinacoes; alguns sobrevivem -> num_is_trials > 0
        for fold in result.folds:
            assert fold.num_is_trials >= 0
            # Pelo menos algum fold deve ter avaliado candidatos validos
        assert any(f.num_is_trials >= 2 for f in result.folds)

    def test_fold_oos_dsr_populated_when_valid(self):
        """Sob bars sineticos com bom volume, RSI deve gerar trades OOS suficientes."""
        result = walk_forward(
            bars=_bars(800),
            strategy_name="rsi",
            ticker="PETR4",
            objective=OptimizationObjective.SHARPE,
            n_splits=3,
        )
        # Pelo menos 1 fold deve ter DSR computado
        with_dsr = [f for f in result.folds if f.oos_dsr is not None]
        assert len(with_dsr) >= 1
        for fold in with_dsr:
            assert "deflated_sharpe" in fold.oos_dsr
            assert "prob_real" in fold.oos_dsr
            assert 0.0 <= fold.oos_dsr["prob_real"] <= 1.0

    def test_aggregate_dsr_present_when_any_fold_has_dsr(self):
        result = walk_forward(
            bars=_bars(800),
            strategy_name="rsi",
            ticker="PETR4",
            objective=OptimizationObjective.SHARPE,
            n_splits=3,
        )
        if any(f.oos_dsr is not None for f in result.folds):
            assert result.deflated_sharpe is not None
            assert "deflated_sharpe" in result.deflated_sharpe
            assert "prob_real" in result.deflated_sharpe
            assert "folds_with_dsr" in result.deflated_sharpe
            assert "folds_real" in result.deflated_sharpe
            assert "num_trials" in result.deflated_sharpe
            assert "sample_size" in result.deflated_sharpe

    def test_aggregate_counts_match_per_fold(self):
        result = walk_forward(
            bars=_bars(800),
            strategy_name="rsi",
            ticker="PETR4",
            objective=OptimizationObjective.SHARPE,
            n_splits=3,
        )
        if result.deflated_sharpe:
            with_dsr = [f for f in result.folds if f.oos_dsr is not None]
            assert result.deflated_sharpe["folds_with_dsr"] == len(with_dsr)
            # folds_real e exatamente os que tem prob >= 0.95
            expected_real = sum(1 for f in with_dsr if f.oos_dsr["prob_real"] >= 0.95)
            assert result.deflated_sharpe["folds_real"] == expected_real
            # num_trials e a SOMA dos trials, nao a media
            expected_total_trials = sum(int(f.oos_dsr["num_trials"]) for f in with_dsr)
            assert result.deflated_sharpe["num_trials"] == expected_total_trials

    def test_aggregate_z_is_average(self):
        result = walk_forward(
            bars=_bars(800),
            strategy_name="rsi",
            ticker="PETR4",
            objective=OptimizationObjective.SHARPE,
            n_splits=3,
        )
        if result.deflated_sharpe:
            with_dsr = [f for f in result.folds if f.oos_dsr is not None]
            expected_avg = sum(f.oos_dsr["deflated_sharpe"] for f in with_dsr) / len(with_dsr)
            assert abs(result.deflated_sharpe["deflated_sharpe"] - round(expected_avg, 3)) < 1e-9


class TestWalkForwardDSREdgeCases:
    def test_flat_bars_no_dsr_when_no_oos_trades(self):
        """Bars completamente flat -> 0 trades OOS -> oos_dsr=None em todos os folds."""
        result = walk_forward(
            bars=_bars(800, pattern="flat"),
            strategy_name="rsi",
            ticker="PETR4",
            objective=OptimizationObjective.SHARPE,
            n_splits=3,
        )
        # Sem trades OOS -> nenhum fold tem DSR
        for fold in result.folds:
            assert fold.oos_dsr is None
        # Aggregate fica None
        assert result.deflated_sharpe is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
