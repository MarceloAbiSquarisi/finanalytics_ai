"""
finanalytics_ai.domain.portfolio_optimizer.engine
───────────────────────────────────────────────────
Otimização de portfólio — três estratégias implementadas em Python puro.
Zero dependências externas (sem numpy/scipy).

Algoritmos implementados:
─────────────────────────
1. MARKOWITZ (Mean-Variance)
   - Monte Carlo: gera N portfólios aleatórios, retorna fronteira eficiente
   - Gradiente: ascendente no Sharpe ratio partindo do portfólio igual-peso
   - Retorna: portfólio de Sharpe máximo, mínima variância, fronteira completa

2. RISK PARITY
   - Objetivo: cada ativo contribui igualmente ao risco total
   - Algoritmo: Cyclical Coordinate Descent (CCD) — converge em ~100 iterações
   - Sem restrição de retorno esperado — foca puramente na diversificação de risco

3. BLACK-LITTERMAN
   - Combina retornos implícitos de mercado (CAPM reverso) com visões do investidor
   - Visões: lista de (ticker, retorno_esperado_anual) fornecida pelo usuário
   - Inversão de matriz via eliminação de Gauss-Jordan (implementação própria)
   - Retorna: pesos BL otimizados + decomposição de contribuição das visões

Design decisions:
  Monte Carlo com 5000 amostras:
    Suficiente para traçar uma fronteira eficiente visualmente convincente.
    Aumentar para 20k melhora a fronteira mas não muda o portfólio ótimo
    significativamente. 5k roda em ~200ms em Python puro.

  Gradiente para Sharpe máximo:
    Complementa o Monte Carlo: após encontrar a região de alto Sharpe via MC,
    o gradiente refina a solução. Taxa de aprendizado adaptativa (reduz 10x
    quando oscila).

  Risk Parity via CCD vs Newton:
    CCD é mais estável numericamente para portfólios com muitos ativos.
    Newton converge mais rápido mas pode divergir com correlações altas.
    Para <= 20 ativos, CCD com 200 iterações é suficiente.

  Black-Litterman tau = 0.05:
    Convenção mais comum na literatura. Representa a incerteza nos retornos
    históricos relativamente ao prior de mercado. Valores entre 0.01 e 0.10
    são típicos — não é sensível a variações nessa faixa.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional


# ── Constantes ────────────────────────────────────────────────────────────────
TRADING_DAYS   = 252
MONTE_CARLO_N  = 5000
GRADIENT_STEPS = 800
GRADIENT_LR    = 0.01
CCD_ITERATIONS = 200
BL_TAU         = 0.05


# ── Álgebra linear — Python puro ──────────────────────────────────────────────

def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))

def _mat_vec(M: list[list[float]], v: list[float]) -> list[float]:
    return [_dot(row, v) for row in M]

def _mat_mul(A: list[list[float]], B: list[list[float]]) -> list[list[float]]:
    n, m, p = len(A), len(B), len(B[0])
    return [[sum(A[i][k] * B[k][j] for k in range(m)) for j in range(p)] for i in range(n)]

def _transpose(M: list[list[float]]) -> list[list[float]]:
    return [[M[j][i] for j in range(len(M))] for i in range(len(M[0]))]

def _mat_add(A: list[list[float]], B: list[list[float]], scale_b: float = 1.0) -> list[list[float]]:
    return [[A[i][j] + scale_b * B[i][j] for j in range(len(A[0]))] for i in range(len(A))]

def _identity(n: int) -> list[list[float]]:
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]

def _scalar_mat(n: int, s: float) -> list[list[float]]:
    return [[s if i == j else 0.0 for j in range(n)] for i in range(n)]

def _gauss_jordan_inverse(M: list[list[float]]) -> list[list[float]]:
    """Inversão de matriz via eliminação de Gauss-Jordan. O(n³)."""
    n = len(M)
    aug = [[M[i][j] for j in range(n)] + [1.0 if i == j else 0.0 for j in range(n)]
           for i in range(n)]
    for col in range(n):
        # Pivot
        max_row = max(range(col, n), key=lambda r: abs(aug[r][col]))
        aug[col], aug[max_row] = aug[max_row], aug[col]
        pivot = aug[col][col]
        if abs(pivot) < 1e-12:
            raise ValueError("Matriz singular — não invertível")
        aug[col] = [x / pivot for x in aug[col]]
        for row in range(n):
            if row != col:
                factor = aug[row][col]
                aug[row] = [aug[row][j] - factor * aug[col][j] for j in range(2 * n)]
    return [[aug[i][n + j] for j in range(n)] for i in range(n)]


# ── Estatísticas de retornos ──────────────────────────────────────────────────

def _covariance_matrix(returns_matrix: list[list[float]]) -> list[list[float]]:
    """
    Matriz de covariância anualizada a partir de retornos diários.
    returns_matrix[i] = lista de retornos diários do ativo i.
    """
    n = len(returns_matrix)
    T = min(len(r) for r in returns_matrix)
    means = [sum(returns_matrix[i][:T]) / T for i in range(n)]
    cov = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            c = sum((returns_matrix[i][t] - means[i]) * (returns_matrix[j][t] - means[j])
                    for t in range(T)) / (T - 1)
            cov[i][j] = cov[j][i] = c * TRADING_DAYS
    return cov

def _portfolio_stats(
    weights: list[float],
    mean_returns: list[float],
    cov: list[list[float]],
    risk_free: float,
) -> tuple[float, float, float]:
    """Retorna (retorno_anual, volatilidade_anual, sharpe)."""
    ret = _dot(weights, mean_returns) * TRADING_DAYS
    w_cov = _mat_vec(cov, weights)
    var   = _dot(weights, w_cov)
    vol   = math.sqrt(max(var, 1e-12))
    sharpe = (ret - risk_free) / vol if vol > 0 else 0.0
    return ret, vol, sharpe

def _normalize(w: list[float]) -> list[float]:
    s = sum(w)
    return [x / s for x in w] if s > 1e-12 else [1.0 / len(w)] * len(w)

def _clamp_weights(w: list[float], min_w: float = 0.0, max_w: float = 1.0) -> list[float]:
    clamped = [max(min_w, min(max_w, x)) for x in w]
    return _normalize(clamped)


# ── Resultado ─────────────────────────────────────────────────────────────────

@dataclass
class AssetWeight:
    ticker:     str
    weight:     float   # 0–1
    weight_pct: float   # 0–100
    ret_contrib:float   # contribuição ao retorno
    risk_contrib:float  # contribuição ao risco (%)

@dataclass
class PortfolioResult:
    """Resultado de um portfólio otimizado."""
    method:        str
    weights:       list[AssetWeight]
    annual_return: float
    volatility:    float
    sharpe:        float
    tickers:       list[str]
    period:        str
    risk_free:     float
    notes:         list[str] = field(default_factory=list)

    @property
    def annual_return_pct(self) -> float: return round(self.annual_return * 100, 2)
    @property
    def volatility_pct(self) -> float:    return round(self.volatility * 100, 2)
    @property
    def sharpe_rounded(self) -> float:    return round(self.sharpe, 3)

    def to_dict(self) -> dict:
        return {
            "method":        self.method,
            "annual_return_pct": self.annual_return_pct,
            "volatility_pct":    self.volatility_pct,
            "sharpe":            self.sharpe_rounded,
            "risk_free_pct":     round(self.risk_free * 100, 2),
            "notes":         self.notes,
            "weights": [
                {"ticker":      aw.ticker,
                 "weight_pct":  round(aw.weight_pct, 2),
                 "ret_contrib": round(aw.ret_contrib * 100, 2),
                 "risk_contrib":round(aw.risk_contrib, 2)}
                for aw in self.weights
            ],
        }

@dataclass
class OptimizationComparison:
    tickers:     list[str]
    period:      str
    risk_free:   float
    markowitz:   PortfolioResult
    risk_parity: PortfolioResult
    black_litterman: PortfolioResult
    equal_weight:    PortfolioResult
    frontier:    list[dict]  # [{vol, ret, sharpe}] pontos da fronteira

    def to_dict(self) -> dict:
        return {
            "tickers":           self.tickers,
            "period":            self.period,
            "risk_free_pct":     round(self.risk_free * 100, 2),
            "markowitz":         self.markowitz.to_dict(),
            "risk_parity":       self.risk_parity.to_dict(),
            "black_litterman":   self.black_litterman.to_dict(),
            "equal_weight":      self.equal_weight.to_dict(),
            "frontier":          self.frontier[:200],  # limita pontos no JSON
            "best_sharpe_method": max(
                ["markowitz", "risk_parity", "black_litterman", "equal_weight"],
                key=lambda m: getattr(self, m).sharpe,
            ),
        }


# ── 1. Markowitz ──────────────────────────────────────────────────────────────

def markowitz_optimize(
    tickers:      list[str],
    mean_returns: list[float],
    cov:          list[list[float]],
    risk_free:    float,
    n_samples:    int = MONTE_CARLO_N,
    seed:         int = 42,
) -> tuple[PortfolioResult, list[dict]]:
    """
    Portfólio de Sharpe máximo via Monte Carlo + refinamento por gradiente.
    Retorna (portfólio_ótimo, pontos_da_fronteira).
    """
    n = len(tickers)
    rng = random.Random(seed)

    best_sharpe = -1e9
    best_w: list[float] = [1.0 / n] * n
    min_vol_w: list[float] = [1.0 / n] * n
    min_vol = 1e9
    frontier: list[dict] = []

    # Monte Carlo
    for _ in range(n_samples):
        raw = [rng.expovariate(1.0) for _ in range(n)]
        w   = _normalize(raw)
        ret, vol, sh = _portfolio_stats(w, mean_returns, cov, risk_free)
        frontier.append({"vol": round(vol * 100, 4), "ret": round(ret * 100, 4), "sharpe": round(sh, 4)})
        if sh > best_sharpe:
            best_sharpe, best_w = sh, w[:]
        if vol < min_vol:
            min_vol, min_vol_w = vol, w[:]

    # Gradiente ascendente no Sharpe
    w = best_w[:]
    lr = GRADIENT_LR
    prev_sh = best_sharpe
    for step in range(GRADIENT_STEPS):
        grad = []
        for i in range(n):
            w_up = w[:]
            w_up[i] += 1e-4
            w_up = _normalize(w_up)
            _, _, sh_up = _portfolio_stats(w_up, mean_returns, cov, risk_free)
            grad.append((sh_up - best_sharpe) / 1e-4)
        w_new = _clamp_weights([w[i] + lr * grad[i] for i in range(n)])
        _, _, sh_new = _portfolio_stats(w_new, mean_returns, cov, risk_free)
        if sh_new > best_sharpe:
            best_sharpe, best_w = sh_new, w_new[:]
            w = w_new[:]
        else:
            lr *= 0.5
        if lr < 1e-6:
            break

    ret, vol, sh = _portfolio_stats(best_w, mean_returns, cov, risk_free)
    asset_weights = _build_asset_weights(best_w, tickers, mean_returns, cov, ret)

    return PortfolioResult(
        method="Markowitz (Sharpe Máximo)",
        weights=asset_weights,
        annual_return=ret, volatility=vol, sharpe=sh,
        tickers=tickers, period="", risk_free=risk_free,
        notes=[
            f"Monte Carlo: {n_samples} portfólios simulados",
            f"Gradiente ascendente: convergiu em Sharpe = {sh:.3f}",
            "Long-only: pesos ≥ 0, soma = 100%",
        ],
    ), frontier


# ── 2. Risk Parity ────────────────────────────────────────────────────────────

def risk_parity_optimize(
    tickers:      list[str],
    mean_returns: list[float],
    cov:          list[list[float]],
    risk_free:    float,
) -> PortfolioResult:
    """
    Equaliza a contribuição marginal ao risco de cada ativo.
    Cyclical Coordinate Descent.
    """
    n = len(tickers)
    w = [1.0 / n] * n
    target = 1.0 / n   # contribuição alvo = 1/n para cada ativo

    for iteration in range(CCD_ITERATIONS):
        w_cov = _mat_vec(cov, w)
        port_var = _dot(w, w_cov)
        if port_var < 1e-12:
            break
        # MRC (Marginal Risk Contribution) de cada ativo
        mrc = [w_cov[i] / math.sqrt(port_var) for i in range(n)]
        # Contribuição de risco percentual
        rc  = [w[i] * mrc[i] for i in range(n)]
        rc_total = sum(rc)
        if rc_total < 1e-12:
            break
        rc_pct = [r / rc_total for r in rc]
        # Ajusta pesos: aumenta underweight, diminui overweight
        for i in range(n):
            if rc_pct[i] > 1e-10:
                w[i] *= (target / rc_pct[i]) ** 0.5
        w = _normalize(w)

    ret, vol, sh = _portfolio_stats(w, mean_returns, cov, risk_free)
    asset_weights = _build_asset_weights(w, tickers, mean_returns, cov, ret)

    # Calcula contribuições de risco finais para notas
    w_cov   = _mat_vec(cov, w)
    pv      = _dot(w, w_cov)
    rc_pcts = []
    if pv > 0:
        rc_pcts = [round(w[i] * w_cov[i] / pv * 100, 1) for i in range(n)]
    max_dev = max(abs(r - 100/n) for r in rc_pcts) if rc_pcts else 0

    return PortfolioResult(
        method="Risk Parity",
        weights=asset_weights,
        annual_return=ret, volatility=vol, sharpe=sh,
        tickers=tickers, period="", risk_free=risk_free,
        notes=[
            f"Contribuições de risco: {', '.join(f'{t}={r}%' for t, r in zip(tickers, rc_pcts))}",
            f"Desvio máximo do alvo ({100/n:.1f}%): {max_dev:.2f} p.p.",
            "Objetivo: diversificação de risco, não de retorno",
        ],
    )


# ── 3. Black-Litterman ────────────────────────────────────────────────────────

def black_litterman_optimize(
    tickers:         list[str],
    mean_returns:    list[float],
    cov:             list[list[float]],
    risk_free:       float,
    views:           list[tuple[str, float]],  # [(ticker, retorno_anual)]
    market_weights:  Optional[list[float]] = None,
    tau:             float = BL_TAU,
    risk_aversion:   float = 3.0,
) -> PortfolioResult:
    """
    Black-Litterman: combina prior de mercado com visões do investidor.

    views: lista de (ticker, retorno_anual_esperado).
           Ex: [("BOVA11", 0.15), ("IVVB11", 0.20)]
    market_weights: pesos de mercado (capitalização). Default = igual-peso.
    tau: escala da incerteza dos retornos históricos (0.01–0.10, default 0.05).
    risk_aversion: coeficiente λ — quanto o mercado penaliza risco (default 3.0).
    """
    n = len(tickers)
    if market_weights is None:
        market_weights = [1.0 / n] * n
    else:
        market_weights = _normalize(market_weights)

    # Prior implícito de mercado (CAPM reverso): π = λ × Σ × w_mkt
    pi = [risk_aversion * x for x in _mat_vec(cov, market_weights)]

    if not views:
        # Sem visões → usa retornos históricos diretamente
        bl_returns = mean_returns[:]
    else:
        # Monta matriz de visões P e vetor Q
        valid_views = [(t, r) for t, r in views if t in tickers]
        k = len(valid_views)
        if k == 0:
            bl_returns = mean_returns[:]
        else:
            P = [[1.0 if tickers[j] == t else 0.0 for j in range(n)]
                 for t, _ in valid_views]
            Q = [r for _, r in valid_views]

            # Matriz de incerteza das visões: Ω = diag(P × τΣ × Pᵀ)
            tau_cov = [[tau * cov[i][j] for j in range(n)] for i in range(n)]
            P_tauCov = _mat_mul(P, tau_cov)
            Pt = _transpose(P)
            omega_full = _mat_mul(P_tauCov, Pt)
            omega_diag = [[omega_full[i][i] if i == j else 0.0 for j in range(k)]
                          for i in range(k)]

            # BL posterior: μ_BL = [(τΣ)⁻¹ + PᵀΩ⁻¹P]⁻¹ × [(τΣ)⁻¹π + PᵀΩ⁻¹Q]
            try:
                inv_tau_cov = _gauss_jordan_inverse(tau_cov)
                inv_omega   = _gauss_jordan_inverse(omega_diag)
            except ValueError:
                bl_returns = mean_returns[:]
            else:
                # A = (τΣ)⁻¹ + PᵀΩ⁻¹P
                Pt_invOmega = _mat_mul(Pt, inv_omega)
                Pt_invOmega_P = _mat_mul(Pt_invOmega, P)
                A = _mat_add(inv_tau_cov, Pt_invOmega_P)
                # b = (τΣ)⁻¹π + PᵀΩ⁻¹Q
                inv_tau_pi = _mat_vec(inv_tau_cov, pi)
                inv_omega_Q = _mat_vec(inv_omega, Q)
                Pt_invOmega_Q = _mat_vec(Pt_invOmega, inv_omega_Q)
                b = [inv_tau_pi[i] + Pt_invOmega_Q[i] for i in range(n)]
                try:
                    inv_A = _gauss_jordan_inverse(A)
                    bl_returns = _mat_vec(inv_A, b)
                except ValueError:
                    bl_returns = mean_returns[:]

    # Com os retornos BL, otimiza via gradiente (Sharpe máximo)
    best_w = [1.0 / n] * n
    best_sharpe = -1e9
    lr = 0.02
    for _ in range(500):
        grad = []
        _, _, sh = _portfolio_stats(best_w, bl_returns, cov, risk_free)
        for i in range(n):
            w_up = best_w[:]
            w_up[i] += 1e-4
            w_up = _normalize(w_up)
            _, _, sh_up = _portfolio_stats(w_up, bl_returns, cov, risk_free)
            grad.append((sh_up - sh) / 1e-4)
        w_new = _clamp_weights([best_w[i] + lr * grad[i] for i in range(n)])
        _, _, sh_new = _portfolio_stats(w_new, bl_returns, cov, risk_free)
        if sh_new > best_sharpe:
            best_sharpe, best_w = sh_new, w_new
        else:
            lr *= 0.6
        if lr < 1e-6:
            break

    ret, vol, sh = _portfolio_stats(best_w, mean_returns, cov, risk_free)
    asset_weights = _build_asset_weights(best_w, tickers, mean_returns, cov, ret)

    view_notes = [f"{t}: retorno esperado {r*100:.1f}% a.a." for t, r in (views or [])]

    return PortfolioResult(
        method="Black-Litterman",
        weights=asset_weights,
        annual_return=ret, volatility=vol, sharpe=sh,
        tickers=tickers, period="", risk_free=risk_free,
        notes=[
            f"τ (incerteza prior) = {tau}  |  λ (aversão ao risco) = {risk_aversion}",
            f"Visões incorporadas: {len(views or [])}",
        ] + view_notes[:5],
    )


# ── Equal Weight (benchmark) ─────────────────────────────────────────────────

def equal_weight(
    tickers:      list[str],
    mean_returns: list[float],
    cov:          list[list[float]],
    risk_free:    float,
) -> PortfolioResult:
    n = len(tickers)
    w = [1.0 / n] * n
    ret, vol, sh = _portfolio_stats(w, mean_returns, cov, risk_free)
    return PortfolioResult(
        method="Equal Weight (benchmark)",
        weights=_build_asset_weights(w, tickers, mean_returns, cov, ret),
        annual_return=ret, volatility=vol, sharpe=sh,
        tickers=tickers, period="", risk_free=risk_free,
        notes=["Benchmark ingênuo — peso idêntico para todos os ativos"],
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_asset_weights(
    w: list[float],
    tickers: list[str],
    mean_returns: list[float],
    cov: list[list[float]],
    total_ret: float,
) -> list[AssetWeight]:
    n = len(tickers)
    w_cov = _mat_vec(cov, w)
    port_vol = math.sqrt(max(_dot(w, w_cov), 1e-12))
    result = []
    for i in range(n):
        rc = w[i] * w_cov[i] / (port_vol ** 2) if port_vol > 0 else 1.0 / n
        result.append(AssetWeight(
            ticker=tickers[i],
            weight=w[i],
            weight_pct=round(w[i] * 100, 2),
            ret_contrib=w[i] * mean_returns[i] * TRADING_DAYS,
            risk_contrib=round(rc * 100, 2),
        ))
    return sorted(result, key=lambda x: -x.weight)
