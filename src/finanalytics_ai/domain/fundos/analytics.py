"""
Fundos CVM Analytics — 3 análises canônicas para fundos de investimento.

1. **style_analysis**: regressão OLS do retorno diário do fundo contra fatores
   (CDI, IBOV, USD/BRL, SMAL11) para revelar exposição. Útil para detectar
   "multimercado vendendo RF disfarçada" ou "ações que se diz long-only mas
   tem net 80% vendido".

2. **peer_ranking**: ordena fundos da mesma categoria (tipo) por sharpe
   rolling N meses. Identifica gestores consistentes vs sorte.

3. **nav_anomalies**: detecta saltos de cota > 3σ vs janela rolling 30d.
   Suspeita de marcação errada, evento corporativo não anunciado, ou erro
   de digitação CVM.

Design:
  - Python puro + numpy. Zero deps de ML.
  - Funções recebem listas/arrays já prontas (queries SQL ficam na route).
  - Tipagem rigorosa via TypedDict.
"""

from __future__ import annotations

from datetime import date
from statistics import mean, pstdev
from typing import TypedDict

import numpy as np


# ── Tipos ──────────────────────────────────────────────────────────────────────


class StyleCoef(TypedDict):
    factor: str
    beta: float
    pct: float  # peso normalizado (sum dos abs(betas) = 100%)


class StyleResult(TypedDict):
    cnpj: str
    sample_size: int
    r_squared: float
    alpha_daily: float            # intercepto (alpha diário em decimal)
    alpha_annualized_pct: float   # ann. ≈ (1+alpha)^252 - 1, em %
    coefficients: list[StyleCoef]
    period: dict                   # {start, end}


class PeerEntry(TypedDict):
    cnpj: str
    denominacao: str | None
    rank: int
    sharpe: float
    return_pct: float
    volatility_pct: float
    sample_size: int


class PeerRanking(TypedDict):
    tipo: str
    window_months: int
    total_funds: int
    top: list[PeerEntry]


class NavAnomaly(TypedDict):
    data: str
    quota_value: float
    daily_return_pct: float
    z_score: float
    rolling_mean_pct: float
    rolling_std_pct: float


class AnomaliesResult(TypedDict):
    cnpj: str
    sample_size: int
    threshold_sigma: float
    rolling_window: int
    anomalies: list[NavAnomaly]


# ── 1. Style Analysis (OLS) ───────────────────────────────────────────────────


def style_analysis(
    fund_returns: list[tuple[date, float]],
    factor_returns: dict[str, list[tuple[date, float]]],
) -> StyleResult | None:
    """Regressão OLS dos retornos diários do fundo contra N fatores.

    Args:
        fund_returns: [(data, retorno_log_diário), ...] — ordem ASC.
        factor_returns: {fator_nome: [(data, retorno_log_diário), ...]}.
            Datas devem ter algum overlap com fund_returns.

    Returns:
        StyleResult com beta de cada fator + alpha (intercepto).
        None se overlap insuficiente (<30 obs).
    """
    if not fund_returns or not factor_returns:
        return None

    # Indexa por data pra fazer inner-join
    fund_map = {d: r for d, r in fund_returns if r is not None}
    common_dates: set[date] = set(fund_map.keys())
    factor_maps: dict[str, dict[date, float]] = {}
    for name, series in factor_returns.items():
        m = {d: r for d, r in series if r is not None}
        factor_maps[name] = m
        common_dates &= set(m.keys())

    if len(common_dates) < 20:
        return None

    sorted_dates = sorted(common_dates)
    y = np.array([fund_map[d] for d in sorted_dates], dtype=float)
    factor_names = list(factor_returns.keys())
    X = np.column_stack([
        [factor_maps[fn][d] for d in sorted_dates] for fn in factor_names
    ])
    # Adiciona intercepto
    X_design = np.column_stack([np.ones(len(sorted_dates)), X])

    # Solve OLS via lstsq (estável)
    coefs, residuals, rank, _sv = np.linalg.lstsq(X_design, y, rcond=None)
    alpha = float(coefs[0])
    betas = coefs[1:].tolist()

    # R² = 1 - SS_res / SS_tot
    y_pred = X_design @ coefs
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    abs_sum = sum(abs(b) for b in betas) or 1.0
    coef_list: list[StyleCoef] = [
        {
            "factor": fn,
            "beta": round(float(b), 4),
            "pct": round(abs(float(b)) / abs_sum * 100, 2),
        }
        for fn, b in zip(factor_names, betas)
    ]
    coef_list.sort(key=lambda c: -abs(c["beta"]))

    alpha_ann = ((1 + alpha) ** 252 - 1) * 100

    return StyleResult(
        cnpj="",  # caller preenche
        sample_size=len(sorted_dates),
        r_squared=round(r2, 4),
        alpha_daily=round(alpha, 6),
        alpha_annualized_pct=round(alpha_ann, 2),
        coefficients=coef_list,
        period={"start": sorted_dates[0].isoformat(), "end": sorted_dates[-1].isoformat()},
    )


# ── 2. Peer Ranking ────────────────────────────────────────────────────────────


def peer_ranking(
    funds_data: list[tuple[str, str | None, list[float]]],
    window_months: int = 6,
    top_n: int = 20,
    risk_free_daily: float = 0.0004,  # ~10% a.a.
) -> list[PeerEntry]:
    """Rankeia fundos pelo sharpe ratio.

    Args:
        funds_data: [(cnpj, denominacao, [retornos_diários_log]), ...]
        window_months: usado só para metadata (caller já filtrou janela).
        top_n: top-N a retornar.
        risk_free_daily: taxa livre de risco diária para cálculo de Sharpe.

    Returns:
        list[PeerEntry] ordenada por sharpe DESC.
    """
    entries: list[PeerEntry] = []
    for cnpj, denom, returns in funds_data:
        if not returns or len(returns) < 30:
            continue
        excess = [r - risk_free_daily for r in returns]
        mu = mean(excess)
        sigma = pstdev(excess)
        sharpe = (mu / sigma * (252**0.5)) if sigma > 1e-9 else 0.0
        # Total return (aproximação composta)
        total_return = (np.prod([1 + r for r in returns]) - 1) * 100
        vol_ann = sigma * (252**0.5) * 100

        entries.append(PeerEntry(
            cnpj=cnpj,
            denominacao=denom,
            rank=0,  # preenchido após sort
            sharpe=round(sharpe, 3),
            return_pct=round(float(total_return), 2),
            volatility_pct=round(vol_ann, 2),
            sample_size=len(returns),
        ))
    entries.sort(key=lambda e: -e["sharpe"])
    for i, e in enumerate(entries[:top_n], 1):
        e["rank"] = i
    return entries[:top_n]


# ── 3. NAV Anomaly Detection ───────────────────────────────────────────────────


def nav_anomalies(
    quota_series: list[tuple[date, float]],
    rolling_window: int = 30,
    threshold_sigma: float = 3.0,
) -> AnomaliesResult | None:
    """Detecta saltos > N σ no retorno diário da cota.

    Args:
        quota_series: [(data, valor_cota), ...] — ordem ASC.
        rolling_window: tamanho da janela para média/desvio.
        threshold_sigma: múltiplos de σ para sinalizar anomalia.

    Returns:
        AnomaliesResult com lista de pontos anômalos. None se dados insuf.
    """
    if len(quota_series) < rolling_window + 5:
        return None

    sorted_q = sorted(quota_series, key=lambda x: x[0])
    dates = [d for d, _ in sorted_q]
    quotas = [float(v) for _, v in sorted_q]
    # Retornos log diários
    returns: list[float | None] = [None]
    for i in range(1, len(quotas)):
        if quotas[i - 1] > 0 and quotas[i] > 0:
            returns.append(float(np.log(quotas[i] / quotas[i - 1])))
        else:
            returns.append(None)

    anomalies: list[NavAnomaly] = []
    for i in range(rolling_window, len(quotas)):
        r_i = returns[i]
        if r_i is None:
            continue
        window = [r for r in returns[i - rolling_window:i] if r is not None]
        if len(window) < rolling_window // 2:
            continue
        mu = mean(window)
        sigma = pstdev(window)
        if sigma < 1e-9:
            continue
        z = (r_i - mu) / sigma
        if abs(z) >= threshold_sigma:
            anomalies.append(NavAnomaly(
                data=dates[i].isoformat(),
                quota_value=round(quotas[i], 6),
                daily_return_pct=round(r_i * 100, 4),
                z_score=round(float(z), 2),
                rolling_mean_pct=round(mu * 100, 4),
                rolling_std_pct=round(sigma * 100, 4),
            ))

    return AnomaliesResult(
        cnpj="",  # caller preenche
        sample_size=len(quotas),
        threshold_sigma=threshold_sigma,
        rolling_window=rolling_window,
        anomalies=anomalies,
    )
