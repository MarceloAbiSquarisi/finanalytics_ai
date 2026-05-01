"""
Engle-Granger 2-step + half-life Ornstein-Uhlenbeck — funcoes puras (R3.1).

Pipeline:
  1. compute_hedge_ratio: OLS A ~ B -> beta (numpy.linalg.lstsq, sem
     dependencia statsmodels p/ ser leve neste passo)
  2. compute_residuals: spread_t = A_t - beta * B_t (sem intercept — ver
     nota abaixo)
  3. adf_test: Augmented Dickey-Fuller no spread (statsmodels.adfuller)
     - H0: serie e' I(1) (random walk, NAO cointegrado)
     - p_value < 0.05 rejeita H0 -> spread e' estacionario -> cointegrado
  4. compute_half_life: AR(1) no diff(spread) ~ spread_lag
     - delta_t = -lambda * spread_lag + epsilon
     - half_life = ln(2) / lambda  (dias p/ desvio do equilibrio cair pela metade)
     - Half-life curto (5-30 dias) e' o sweet spot p/ pairs trading

Nota sobre intercept:
  A versao "limpa" do Engle-Granger inclui intercept na regressao OLS
  (A = alfa + beta * B + e). Implementacao abaixo NAO inclui intercept —
  alfa e' absorvido na media do residuo, e p_value do ADF "regression='c'"
  ja desconsidera media. Para preco em log seria diferente; preco bruto
  R$ funciona bem assim. Documentado p/ revisao futura.

Convencao series:
  prices_a, prices_b: list[float] | np.ndarray, mesmo tamanho, alinhadas
  por data (caller garante). Ordem chronologica antiga -> nova.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class CointegrationResult:
    """Resultado completo do Engle-Granger pra um par."""

    beta: float  # coef OLS A ~ B (sem intercept)
    rho: float  # correlacao Pearson de A vs B
    residuals: np.ndarray  # spread_t = A_t - beta * B_t
    p_value_adf: float  # ADF p-value no spread
    cointegrated: bool  # p_value_adf < 0.05
    half_life: float | None  # dias (None se beta_ar1 nao reverte)
    sample_size: int  # len(prices_a)


# ── 1. Hedge ratio ───────────────────────────────────────────────────────────


def compute_hedge_ratio(
    prices_a: list[float] | np.ndarray, prices_b: list[float] | np.ndarray
) -> float:
    """
    OLS sem intercept: beta = (B'A) / (B'B).
    Resolve A = beta * B no sentido de minimizar sum((A - beta*B)^2).
    """
    a = np.asarray(prices_a, dtype=np.float64)
    b = np.asarray(prices_b, dtype=np.float64)
    if a.shape != b.shape:
        raise ValueError(f"shape mismatch: A={a.shape} B={b.shape}")
    if len(b) == 0:
        raise ValueError("empty input")
    denom = float(np.dot(b, b))
    if denom == 0.0:
        raise ValueError("B series is identically zero")
    return float(np.dot(b, a) / denom)


def compute_residuals(
    prices_a: list[float] | np.ndarray,
    prices_b: list[float] | np.ndarray,
    beta: float,
) -> np.ndarray:
    """spread_t = A_t - beta * B_t. Retorna ndarray."""
    a = np.asarray(prices_a, dtype=np.float64)
    b = np.asarray(prices_b, dtype=np.float64)
    return a - beta * b


# ── 2. ADF test ──────────────────────────────────────────────────────────────


def adf_test(series: np.ndarray | list[float], regression: str = "c") -> float:
    """
    Augmented Dickey-Fuller. Retorna p_value.

    H0: serie e' I(1) (raiz unitaria, random walk).
    p_value < 0.05 -> rejeita H0 -> serie estacionaria -> cointegrado.

    regression:
      "c"  — com constante (default; assume media != 0)
      "n"  — sem constante (assume media = 0)
      "ct" — com constante e trend
    """
    arr = np.asarray(series, dtype=np.float64)
    if len(arr) < 12:  # statsmodels minimo p/ ADF
        raise ValueError(f"series too short for ADF: {len(arr)} < 12")
    # Import diferido p/ permitir test em ambientes sem statsmodels
    from statsmodels.tsa.stattools import adfuller

    result = adfuller(arr, regression=regression, autolag="AIC")
    return float(result[1])


# ── 3. Half-life (Ornstein-Uhlenbeck via AR(1) no diff) ──────────────────────


def compute_half_life(residuals: np.ndarray | list[float]) -> float | None:
    """
    Estima half-life de mean-reversion.

    Modelo: delta_residual_t = -lambda * residual_lag + epsilon
      OLS de diff(spread) ~ spread_lag (sem intercept)
      coef = -lambda
      half_life = ln(2) / lambda

    Retorna None se:
      - serie tem <= 2 pontos (nao da pra fazer diff)
      - lambda <= 0 (serie nao mean-reverte; explosiva ou random walk)
      - lambda muito perto de 0 -> half_life infinito
    """
    arr = np.asarray(residuals, dtype=np.float64)
    if len(arr) < 3:
        return None

    diff = np.diff(arr)  # delta_t = arr_t - arr_{t-1}
    lag = arr[:-1]  # arr_{t-1} para o mesmo t

    # OLS sem intercept: coef = (lag'diff) / (lag'lag)
    denom = float(np.dot(lag, lag))
    if denom == 0.0:
        return None
    coef = float(np.dot(lag, diff) / denom)
    # coef = -lambda. Para mean-reversion, coef < 0 (lambda > 0).
    if coef >= 0 or math.isclose(coef, 0.0, abs_tol=1e-12):
        return None
    lambda_ = -coef
    half = math.log(2.0) / lambda_
    return half


# ── 4. Engle-Granger orchestrator ────────────────────────────────────────────


def engle_granger(
    prices_a: list[float] | np.ndarray,
    prices_b: list[float] | np.ndarray,
    p_threshold: float = 0.05,
) -> CointegrationResult:
    """
    Pipeline completo: hedge ratio -> residuals -> ADF -> half-life.

    cointegrated = (p_value_adf < p_threshold).
    """
    a = np.asarray(prices_a, dtype=np.float64)
    b = np.asarray(prices_b, dtype=np.float64)
    if a.shape != b.shape:
        raise ValueError(f"shape mismatch: A={a.shape} B={b.shape}")
    if len(a) < 20:  # razoavel mais defensivo que ADF minimo (12)
        raise ValueError(f"sample too small: {len(a)} < 20")

    beta = compute_hedge_ratio(a, b)
    rho = float(np.corrcoef(a, b)[0, 1])
    residuals = compute_residuals(a, b, beta)
    p_value = adf_test(residuals, regression="c")
    cointegrated = p_value < p_threshold
    half_life = compute_half_life(residuals) if cointegrated else None

    return CointegrationResult(
        beta=beta,
        rho=rho,
        residuals=residuals,
        p_value_adf=p_value,
        cointegrated=cointegrated,
        half_life=half_life,
        sample_size=len(a),
    )
