"""
finanalytics_ai.application.ml.rates_features

Features de renda fixa puras (sem I/O) para alimentar modelos cross-asset.

Padrão idêntico ao feature_pipeline.py: funções puras → testáveis em unidade.
Consumidores lêem yield_curves / breakeven_inflation no TimescaleDB e passam
listas de dicts para estas funções.

Referência: Melhorias/melhorias_renda_fixa.md §1.3 + §2.2.
"""
from __future__ import annotations

import math
from typing import Any


# ── Busca em curva (lista de dicts {dias_uteis, taxa_aa}) ─────────────────────

def _sorted_curve(curve: list[dict], key_du: str = "dias_uteis", key_tx: str = "taxa_aa") -> list[tuple[int, float]]:
    pairs: list[tuple[int, float]] = []
    for r in curve:
        du = r.get(key_du)
        tx = r.get(key_tx)
        if du is None or tx is None:
            continue
        try:
            pairs.append((int(du), float(tx)))
        except (TypeError, ValueError):
            continue
    pairs.sort(key=lambda x: x[0])
    return pairs


def taxa_em_vertice(curve: list[dict], du: int,
                    key_du: str = "dias_uteis", key_tx: str = "taxa_aa") -> float | None:
    """
    Interpolação flat-forward (convenção 252 d.u./ano) para obter a taxa no
    vértice `du`. Fora do range: clamp ao extremo mais próximo.

    flat-forward: fatores de desconto compostos interpolados linearmente
    na forma (1+tax)^(du/252). Padrão ANBIMA.
    """
    pairs = _sorted_curve(curve, key_du, key_tx)
    if not pairs:
        return None
    if du <= pairs[0][0]:
        return pairs[0][1]
    if du >= pairs[-1][0]:
        return pairs[-1][1]

    # Encontra par (du_a, tx_a), (du_b, tx_b) que cercam du
    for i in range(1, len(pairs)):
        du_b, tx_b = pairs[i]
        if du_b >= du:
            du_a, tx_a = pairs[i - 1]
            # Flat-forward (taxas em %): converter para decimal
            r_a = tx_a / 100.0
            r_b = tx_b / 100.0
            # fator composto em du_a e du_b
            f_a = (1 + r_a) ** (du_a / 252.0)
            f_b = (1 + r_b) ** (du_b / 252.0)
            # Interpolação linear no logaritmo do fator
            lam = (du - du_a) / (du_b - du_a)
            log_f = (1 - lam) * math.log(f_a) + lam * math.log(f_b)
            f = math.exp(log_f)
            r = f ** (252.0 / du) - 1
            return r * 100.0
    return None


def slope(curve: list[dict], du_short: int, du_long: int) -> float | None:
    """Inclinação: taxa(du_long) − taxa(du_short). Curva normal > 0."""
    a = taxa_em_vertice(curve, du_short)
    b = taxa_em_vertice(curve, du_long)
    if a is None or b is None:
        return None
    return b - a


def butterfly(curve: list[dict], du_curto: int, du_medio: int, du_longo: int) -> float | None:
    """Curvatura butterfly: taxa(curto) + taxa(longo) − 2·taxa(médio).
    Positivo = curva "mais convexa" que linear no vértice médio."""
    a = taxa_em_vertice(curve, du_curto)
    m = taxa_em_vertice(curve, du_medio)
    b = taxa_em_vertice(curve, du_longo)
    if any(x is None for x in (a, m, b)):
        return None
    return a + b - 2 * m


def breakeven_em_vertice(be_curve: list[dict], du: int) -> float | None:
    """Lookup simples em breakeven_inflation. Usa mesma interpolação flat-forward
    (breakeven é taxa anualizada composta)."""
    return taxa_em_vertice(be_curve, du, key_du="dias_uteis", key_tx="breakeven_aa")


# ── Nelson-Siegel ──────────────────────────────────────────────────────────────

def nelson_siegel_fit(vertice_du: list[int], taxa_pct: list[float]) -> dict[str, float] | None:
    """
    Ajuste Nelson-Siegel paramétrico:
        y(t) = β0 + β1 · (1 − e^-λt)/(λt) + β2 · [(1 − e^-λt)/(λt) − e^-λt]

    β0 = level (longo prazo), β1 = slope (curto vs longo), β2 = curvature (meio).
    t em anos (du / 252).
    """
    try:
        import numpy as np  # type: ignore
        from scipy.optimize import curve_fit  # type: ignore
    except Exception:
        return None

    pairs = [(du, tx) for du, tx in zip(vertice_du, taxa_pct) if du and tx is not None]
    if len(pairs) < 4:
        return None
    vs = np.array([p[0] for p in pairs], dtype=float) / 252.0
    ts = np.array([p[1] for p in pairs], dtype=float)

    def ns(t, b0, b1, b2, lam):
        # Evita divisão por zero em t muito pequeno
        t = np.maximum(t, 1e-6)
        term1 = (1 - np.exp(-lam * t)) / (lam * t)
        term2 = term1 - np.exp(-lam * t)
        return b0 + b1 * term1 + b2 * term2

    try:
        params, _ = curve_fit(ns, vs, ts, p0=[ts.mean(), -1.0, 1.0, 0.5], maxfev=5000)
    except Exception:
        return None
    return {
        "beta0":  float(params[0]),
        "beta1":  float(params[1]),
        "beta2":  float(params[2]),
        "lambda": float(params[3]),
    }


# ── F2: Time Series Momentum (Moskowitz et al. 2012) ─────────────────────────

def tsmom_signal(taxa_series: list[float],
                 lookback_dias: int = 63,
                 vol_target: float = 0.10,
                 vol_clip_upper: float = 2.0) -> float | None:
    """
    TSMOM na taxa DI1. Retorna sinal escalonado por vol-target (range -2..+2).

    Lógica: sinal = sign(retorno_lookback) * (vol_target / vol_hist_realized).
    No DI1 a taxa é cotada inversamente ao preço — o sinal aqui é na taxa:
      sinal > 0 → taxa tem subido (short DI1)
      sinal < 0 → taxa tem caído (long DI1)

    Consumidor (execution) inverte o signal conforme direção desejada.

    taxa_series deve estar ordenada ASC (mais recente no final).
    Retorna None se série muito curta.
    """
    if len(taxa_series) < lookback_dias + 1:
        return None
    t_now  = taxa_series[-1]
    t_back = taxa_series[-lookback_dias - 1]
    if t_back is None or t_back == 0:
        return None
    ret_lb = (t_now - t_back) / abs(t_back)

    # Volatilidade histórica anualizada dos retornos 1d
    returns_1d: list[float] = []
    for i in range(max(1, len(taxa_series) - 63), len(taxa_series)):
        prev = taxa_series[i - 1]
        curr = taxa_series[i]
        if prev and prev != 0:
            returns_1d.append((curr - prev) / abs(prev))
    if len(returns_1d) < 20:
        return None
    mean = sum(returns_1d) / len(returns_1d)
    var = sum((r - mean) ** 2 for r in returns_1d) / max(1, len(returns_1d) - 1)
    vol_ann = (var ** 0.5) * (252 ** 0.5)
    if vol_ann <= 0:
        return None
    escala = min(vol_clip_upper, vol_target / max(vol_ann, 0.01))
    sign = 1.0 if ret_lb > 0 else (-1.0 if ret_lb < 0 else 0.0)
    return sign * escala


# ── F3: Carry (Koijen, Moskowitz, Pedersen, Vrugt 2018) ──────────────────────

def carry_ntnb_over_cdi(taxa_ntnb_real_aa: float, cdi_aa: float) -> float:
    """Carry real NTN-B: taxa real - CDI (ambas em % a.a.). Em juro alto
    tipicamente negativo; sinal para reversão quando muito negativo."""
    return float(taxa_ntnb_real_aa) - float(cdi_aa)


def carry_roll_down(taxa_longa: float, taxa_curta: float,
                    du_longo: int, du_curto: int) -> float | None:
    """Roll-down: ganho anualizado por 'descer' na curva inclinada positivamente.
    Curva positiva → roll-down > 0 (carry favorável ao long)."""
    if du_longo == du_curto:
        return None
    return (taxa_longa - taxa_curta) / (du_longo - du_curto) * du_curto


# ── F4: Value via z-score do histórico ─────────────────────────────────────────

def value_zscore(taxa_atual: float, historico: list[float]) -> float | None:
    """Z-score da taxa atual vs histórico. Z > +1 = 'barato' (taxa alta),
    Z < -1 = 'caro' (taxa baixa). Assumimos que reversão à média domina no
    horizonte de semanas-meses (Asness et al. 2013)."""
    clean = [float(x) for x in historico if x is not None]
    if len(clean) < 20:
        return None
    mu = sum(clean) / len(clean)
    var = sum((x - mu) ** 2 for x in clean) / max(1, len(clean) - 1)
    std = var ** 0.5
    if std == 0:
        return None
    return (float(taxa_atual) - mu) / std


def value_breakeven_vs_focus(breakeven_aa: float, focus_ipca_aa: float) -> float:
    """Value via inflação implícita − expectativa Focus. Positivo = mercado
    precifica inflação acima do consenso → oportunidade de vender inflação
    implícita (long pré + short IPCA)."""
    return float(breakeven_aa) - float(focus_ipca_aa)


# ── F5: Combinação Value + Momentum (Asness et al. 2013) ──────────────────────

def value_momentum_combined(value_z: float | None,
                            momentum_z: float | None,
                            weight_value: float = 0.5,
                            weight_momentum: float = 0.5) -> float | None:
    """Value + momentum com pesos iguais. Correlação negativa V-M (~-0.4 a -0.6)
    → combinação reduz vol sem derrubar retorno (√2 Sharpe boost)."""
    if value_z is None or momentum_z is None:
        return None
    return weight_value * value_z + weight_momentum * momentum_z


# ── F6: Butterfly e FRA (Litterman & Scheinkman 1991) ────────────────────────

def butterfly_duration_neutral(taxa_curta: float, taxa_media: float, taxa_longa: float,
                               du_curto: int, du_medio: int, du_longo: int) -> float | None:
    """Butterfly duration-neutral: taxa_media - (w1·taxa_curta + w2·taxa_longa).
    w1, w2 garantem duration neutra. Positivo = corpo caro → short corpo,
    long wings. Negativo = corpo barato → inverso."""
    du_range = du_longo - du_curto
    if du_range == 0:
        return None
    w1 = (du_longo - du_medio) / du_range
    w2 = (du_medio - du_curto) / du_range
    return taxa_media - (w1 * taxa_curta + w2 * taxa_longa)


def fra_implied(taxa_longa_aa: float, taxa_curta_aa: float,
                du_longo: int, du_curto: int) -> float | None:
    """FRA: taxa forward implícita no intervalo [du_curto, du_longo].
    Taxas em % a.a. (decimal = /100 internamente)."""
    if du_longo <= du_curto:
        return None
    r_long  = taxa_longa_aa / 100.0
    r_short = taxa_curta_aa / 100.0
    fator_longo = (1 + r_long) ** (du_longo / 252.0)
    fator_curto = (1 + r_short) ** (du_curto / 252.0)
    du_fra = du_longo - du_curto
    fator_fra = fator_longo / fator_curto
    taxa_fra = fator_fra ** (252.0 / du_fra) - 1
    return taxa_fra * 100.0


# ── PCA da curva (histórico multi-day) ────────────────────────────────────────

def yield_curve_pca(yield_matrix: list[list[float]], n_components: int = 3) -> dict[str, Any] | None:
    """
    Decompõe uma matriz (n_dias × n_vertices) nos 3 fatores clássicos:
      PC1 = level (movimento paralelo)
      PC2 = slope (inclinação)
      PC3 = curvature (curvatura)

    Retorna:
      components: lista com os loadings de cada PC (n_components × n_vertices)
      explained_variance: fração explicada por PC
      factors: matriz (n_dias × n_components) — scores por dia
    """
    try:
        import numpy as np  # type: ignore
        from sklearn.decomposition import PCA  # type: ignore
    except Exception:
        return None
    if not yield_matrix or len(yield_matrix) < n_components + 1:
        return None
    X = np.asarray(yield_matrix, dtype=float)
    if X.shape[1] < n_components:
        return None
    pca = PCA(n_components=n_components)
    factors = pca.fit_transform(X)
    return {
        "components":         [row.tolist() for row in pca.components_],
        "explained_variance": pca.explained_variance_ratio_.tolist(),
        "factors":            factors.tolist(),
    }


# ── Feature builder (consumido por build_features_daily / XGBoost factor) ────

def build_rate_features(curve_pre: list[dict], curve_ipca: list[dict],
                        breakeven: list[dict] | None = None) -> dict[str, float | None]:
    """
    Features cross-asset consumidas pelo XGBoostFactorModel / LightGBM DI1.

    Inputs: listas de dicts com {dias_uteis, taxa_aa} para pre, e {dias_uteis,
    taxa_real_aa} para ipca (convertido internamente). breakeven opcional com
    {dias_uteis, breakeven_aa}.

    Returns dict com as features em § melhorias_renda_fixa.md §1.3.
    """
    # Normaliza para que ambas as curvas usem 'taxa_aa' como rate
    curve_ipca_norm = [
        {"dias_uteis": r["dias_uteis"], "taxa_aa": r.get("taxa_real_aa", r.get("taxa_aa"))}
        for r in curve_ipca if r.get("dias_uteis") is not None
    ]

    feats: dict[str, float | None] = {
        # Pré-fixada
        "slope_2y_10y":         slope(curve_pre, 504,  2520),
        "slope_1y_5y":          slope(curve_pre, 252,  1260),
        "curvatura_butterfly":  butterfly(curve_pre, 252, 1260, 2520),
        "taxa_pre_3m":          taxa_em_vertice(curve_pre, 63),
        "taxa_pre_1y":          taxa_em_vertice(curve_pre, 252),
        "taxa_pre_5y":          taxa_em_vertice(curve_pre, 1260),

        # IPCA (taxa real)
        "taxa_real_1y":         taxa_em_vertice(curve_ipca_norm, 252),
        "taxa_real_5y":         taxa_em_vertice(curve_ipca_norm, 1260),

        # Breakeven (inflação implícita em %)
        "breakeven_1y":         None,
        "breakeven_2y":         None,
        "breakeven_5y":         None,
    }

    if breakeven:
        feats["breakeven_1y"] = breakeven_em_vertice(breakeven, 252)
        feats["breakeven_2y"] = breakeven_em_vertice(breakeven, 504)
        feats["breakeven_5y"] = breakeven_em_vertice(breakeven, 1260)

    # Nelson-Siegel (pré) — 4 fatores paramétricos
    ns = nelson_siegel_fit(
        [r["dias_uteis"] for r in curve_pre if r.get("taxa_aa") is not None],
        [r["taxa_aa"]    for r in curve_pre if r.get("taxa_aa") is not None],
    )
    if ns:
        feats["ns_level"]     = ns["beta0"]
        feats["ns_slope"]     = ns["beta1"]
        feats["ns_curvature"] = ns["beta2"]
        feats["ns_lambda"]    = ns["lambda"]
    else:
        feats["ns_level"] = feats["ns_slope"] = feats["ns_curvature"] = feats["ns_lambda"] = None

    return feats
