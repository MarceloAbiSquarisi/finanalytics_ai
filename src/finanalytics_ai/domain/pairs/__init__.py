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

__all__ = [
    "CointegrationResult",
    "adf_test",
    "compute_half_life",
    "compute_hedge_ratio",
    "compute_residuals",
    "engle_granger",
]
