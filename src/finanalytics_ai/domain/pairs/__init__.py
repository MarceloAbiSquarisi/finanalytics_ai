"""Pares cointegrados (R3) — domain layer.

Funcoes puras p/ Engle-Granger 2-step + half-life. Usado offline
(scripts/cointegration_screen.py popula cointegrated_pairs) e online
(R3.2 PairsTradingStrategy le da tabela).
"""

from finanalytics_ai.domain.pairs.cointegration import (
    CointegrationResult,
    adf_test,
    compute_half_life,
    compute_hedge_ratio,
    compute_residuals,
    engle_granger,
)
from finanalytics_ai.domain.pairs.strategy_logic import (
    DEFAULT_BONFERRONI_ALPHA,
    DEFAULT_MAX_HALF_LIFE,
    DEFAULT_MIN_HALF_LIFE,
    DEFAULT_Z_ENTRY,
    DEFAULT_Z_EXIT,
    DEFAULT_Z_STOP,
    PairAction,
    PairPosition,
    PairThresholds,
    apply_bonferroni,
    compute_zscore,
    decide_pair_action,
    validate_pair_filters,
)

__all__ = [
    "CointegrationResult",
    "DEFAULT_BONFERRONI_ALPHA",
    "DEFAULT_MAX_HALF_LIFE",
    "DEFAULT_MIN_HALF_LIFE",
    "DEFAULT_Z_ENTRY",
    "DEFAULT_Z_EXIT",
    "DEFAULT_Z_STOP",
    "PairAction",
    "PairPosition",
    "PairThresholds",
    "adf_test",
    "apply_bonferroni",
    "compute_half_life",
    "compute_hedge_ratio",
    "compute_residuals",
    "compute_zscore",
    "decide_pair_action",
    "engle_granger",
    "validate_pair_filters",
]
