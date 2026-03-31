"""
finanalytics_ai.application.ml.risk_estimator
Estimador de risco probabilistico em 4 camadas:
  1. Historico      — nao-parametrico, sem suposicoes de distribuicao
  2. t-Student      — parametrico, captura fat tails
  3. GARCH(1,1)     — volatilidade condicional
  4. Monte Carlo    — 100k simulacoes com parametros ajustados da t-Student

Todas as metricas expressam perda como valor positivo.
Ex: var_95_historical=0.032 = perda maxima de 3.2% com 95% de confianca.
"""
from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timezone
from typing import Any

import numpy as np
import scipy.stats as st
import structlog

from finanalytics_ai.domain.ml.entities import RiskMetrics

log = structlog.get_logger(__name__)

_CONFIDENCE = 0.95
_ALPHA = 1.0 - _CONFIDENCE
_MC_PATHS = 100_000
_MIN_RETURNS = 30
_GARCH_MIN_RETURNS = 100


class RiskEstimator:
    """Calcula RiskMetrics a partir de serie de retornos diarios. Stateless."""

    async def estimate(
        self,
        ticker: str,
        returns: list[float],
        reference_date: date | None = None,
        window_days: int = 252,
    ) -> RiskMetrics | None:
        if len(returns) < _MIN_RETURNS:
            log.warning("risk_estimator.insufficient_data",
                        ticker=ticker, n=len(returns), needed=_MIN_RETURNS)
            return None

        ref = reference_date or date.today()
        arr = np.asarray(returns, dtype=np.float64)
        arr = arr[np.isfinite(arr)]
        if len(arr) < _MIN_RETURNS:
            return None

        hist = _historical(arr)
        param = _parametric_t(arr)
        garch_result = await asyncio.to_thread(_garch, arr, ticker)
        mc_result = await asyncio.to_thread(_monte_carlo, arr, param)
        vol_annual = float(arr.std() * math.sqrt(252))

        return RiskMetrics(
            ticker=ticker,
            date=datetime.combine(ref, datetime.min.time()).replace(tzinfo=timezone.utc),
            window_days=window_days,
            var_95_historical=hist["var"],
            cvar_95_historical=hist["cvar"],
            var_95_parametric=param["var"],
            cvar_95_parametric=param["cvar"],
            t_degrees_of_freedom=param["df"],
            var_95_garch=garch_result["var"],
            cvar_95_garch=garch_result["cvar"],
            garch_volatility_forecast=garch_result["vol_forecast"],
            var_95_mc=mc_result["var"],
            cvar_95_mc=mc_result["cvar"],
            volatility_annual=vol_annual,
        )


def _historical(arr: np.ndarray) -> dict[str, float]:
    sorted_r = np.sort(arr)
    cutoff = max(int(len(sorted_r) * _ALPHA), 1)
    var = float(-sorted_r[cutoff - 1])
    cvar = float(-sorted_r[:cutoff].mean())
    return {"var": max(var, 0.0), "cvar": max(cvar, 0.0)}


def _parametric_t(arr: np.ndarray) -> dict[str, float]:
    try:
        df, loc, scale = st.t.fit(arr)
    except Exception:
        loc, scale = float(arr.mean()), float(arr.std())
        df = 100.0
    df = max(df, 1.01)
    t_alpha = st.t.ppf(_ALPHA, df, loc=loc, scale=scale)
    var = float(-t_alpha)
    if df > 1.0:
        t_s = st.t.ppf(_ALPHA, df)
        cvar = float(-(loc - scale * (st.t.pdf(t_s, df) / _ALPHA) * (df + t_s**2) / (df - 1)))
    else:
        cvar = var * 1.3
    return {
        "var": max(var, 0.0),
        "cvar": max(cvar, max(var, 0.0)),
        "df": df,
        "loc": loc,
        "scale": scale,
    }


def _garch(arr: np.ndarray, ticker: str) -> dict[str, Any]:
    """GARCH(1,1)-t. Retorna None nos campos se serie curta ou nao-convergencia."""
    null: dict[str, Any] = {"var": None, "cvar": None, "vol_forecast": None}
    if len(arr) < _GARCH_MIN_RETURNS:
        return null
    try:
        from arch import arch_model
        res = arch_model(
            arr * 100, vol="Garch", p=1, q=1, dist="t", rescale=False
        ).fit(disp="off", show_warning=False)
        fcast = res.forecast(horizon=1, reindex=False)
        vol_d = float(np.sqrt(fcast.variance.values[-1, 0])) / 100.0
        nu = max(float(res.params.get("nu", 6.0)), 2.01)
        t_q = float(st.t.ppf(_ALPHA, nu))
        var_g = float(-vol_d * t_q)
        pdf_q = float(st.t.pdf(t_q, nu))
        cvar_g = float(vol_d * pdf_q / _ALPHA * (nu + t_q**2) / (nu - 1))
        log.debug("risk_estimator.garch_ok", ticker=ticker,
                  vol_d=round(vol_d, 5), nu=round(nu, 1))
        return {
            "var": max(var_g, 0.0),
            "cvar": max(cvar_g, max(var_g, 0.0)),
            "vol_forecast": vol_d,
        }
    except Exception as exc:
        log.warning("risk_estimator.garch_fallback", ticker=ticker, error=str(exc))
        vol_hist = float(arr.std())
        var_fb = max(-vol_hist * float(st.t.ppf(_ALPHA, 6.0)), 0.0)
        return {"var": var_fb, "cvar": var_fb * 1.3, "vol_forecast": vol_hist}


def _monte_carlo(arr: np.ndarray, param: dict[str, float]) -> dict[str, float]:
    """100k simulacoes t-Student com parametros ajustados. Seed=42 para reproducibilidade."""
    sim = st.t.rvs(
        param["df"], loc=param["loc"], scale=param["scale"],
        size=_MC_PATHS, random_state=np.random.default_rng(42),
    )
    cutoff = max(int(_MC_PATHS * _ALPHA), 1)
    s = np.sort(sim)
    var_mc = float(-s[cutoff - 1])
    cvar_mc = float(-s[:cutoff].mean())
    return {"var": max(var_mc, 0.0), "cvar": max(cvar_mc, max(var_mc, 0.0))}