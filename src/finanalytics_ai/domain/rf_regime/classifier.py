"""
RF Regime Classifier — detecta regime da curva DI e recomenda indexador.

Algoritmo (MVP determinístico — HMM fica pra Sprint 2):

  1. Lê últimas 252 barras de `rates_features_daily` (1 ano).
  2. Calcula slope_2y_10y atual + delta 20d + z-score vs janela 252d.
  3. Classifica em 4 regimes:
       - INVERSION  : slope_2y_10y < INV_THRESHOLD (curva invertida)
       - STEEPENING : slope > 0 E delta_20d > +1σ (abrindo)
       - FLATTENING : slope ≥ 0 E delta_20d < -1σ (achatando)
       - NORMAL     : nenhum dos acima
  4. Mapeia regime → recomendação de indexador.

Output: dict tipado com regime atual, score (intensidade), histórico,
recomendação textual e alocação sugerida.
"""

from __future__ import annotations

from datetime import date
from statistics import mean, pstdev
from typing import Literal, TypedDict

# ── Thresholds (em pontos decimais — slope é taxa fracionária) ───────────────

# Inversão considerada quando slope < -0.5 ponto base (-0.005 em decimal)
INV_THRESHOLD = -0.005
# Z-score para considerar movimento significativo (1 desvio padrão)
DELTA_Z_THRESHOLD = 1.0
# Janela para delta de 20 dias úteis (~1 mês)
DELTA_WINDOW_DAYS = 20
# Janela para z-score (~1 ano)
Z_WINDOW_DAYS = 252

Regime = Literal["NORMAL", "STEEPENING", "FLATTENING", "INVERSION"]


# ── Tipos ──────────────────────────────────────────────────────────────────────


class RegimePoint(TypedDict):
    dia: str
    slope_2y_10y: float
    regime: Regime


class RegimeAllocation(TypedDict):
    cdi: float           # % alocação em pós-fixado (CDI)
    pre_curto: float     # % em prefixado curto (1-3 anos)
    ipca_longo: float    # % em IPCA+ longo (5+ anos)


class RegimeRecommendation(TypedDict):
    regime: Regime
    headline: str
    rationale: str
    suggested_allocation: RegimeAllocation


class RegimeResult(TypedDict):
    regime: Regime
    score: float                         # intensidade [0,1]: quanto mais alto, mais "puro" o regime
    slope_2y_10y: float
    slope_2y_10y_delta_20d: float
    slope_z_score: float
    last_date: str
    sample_size: int
    history: list[RegimePoint]            # série diária do regime nos últimos N dias
    recommendation: RegimeRecommendation


# ── Algoritmo ──────────────────────────────────────────────────────────────────


def classify_regime(
    slope: float,
    delta: float,
    z_score: float,
) -> tuple[Regime, float]:
    """Classifica o regime atual + score [0,1] de intensidade.

    Hierarquia:
      1. INVERSION (slope absoluto manda)
      2. STEEPENING / FLATTENING (movimento manda — z-score)
      3. NORMAL (default)
    """
    # 1. Inversão é o mais "absoluto" — slope negativo significa curva invertida
    if slope < INV_THRESHOLD:
        # score: quanto mais negativo, mais intenso (cap em -0.03 = 3pp negativo)
        intensity = min(abs(slope) / 0.03, 1.0)
        return "INVERSION", round(intensity, 3)

    # 2. Movimento direcional — z-score do delta
    if z_score > DELTA_Z_THRESHOLD:
        # Steepening: curva abrindo. Score = z-score normalizado em [1, 3]
        intensity = min((z_score - DELTA_Z_THRESHOLD) / 2.0, 1.0)
        return "STEEPENING", round(max(intensity, 0.3), 3)

    if z_score < -DELTA_Z_THRESHOLD:
        # Flattening: curva achatando
        intensity = min((-z_score - DELTA_Z_THRESHOLD) / 2.0, 1.0)
        return "FLATTENING", round(max(intensity, 0.3), 3)

    return "NORMAL", round(1.0 - abs(z_score) / DELTA_Z_THRESHOLD, 3)


def compute_z_score(values: list[float]) -> float:
    """Z-score do último valor vs janela. Robusto a samples pequenos."""
    if len(values) < 5:
        return 0.0
    last = values[-1]
    mu = mean(values)
    sigma = pstdev(values)
    return (last - mu) / sigma if sigma > 1e-9 else 0.0


# ── Recomendações por regime ──────────────────────────────────────────────────

# Cada regime mapeia para uma alocação sugerida (% que somam 100)
ALLOCATIONS: dict[Regime, RegimeAllocation] = {
    "INVERSION": {"cdi": 70, "pre_curto": 20, "ipca_longo": 10},
    "FLATTENING": {"cdi": 20, "pre_curto": 20, "ipca_longo": 60},
    "STEEPENING": {"cdi": 30, "pre_curto": 50, "ipca_longo": 20},
    "NORMAL":     {"cdi": 30, "pre_curto": 30, "ipca_longo": 40},
}

HEADLINES: dict[Regime, str] = {
    "INVERSION":  "🔻 Curva invertida — privilegiar pós-fixado (CDI)",
    "STEEPENING": "📈 Curva abrindo — favorece prefixado curto",
    "FLATTENING": "📉 Curva achatando — favorece IPCA+ longo",
    "NORMAL":     "⚖️ Curva balanceada — alocação diversificada",
}

RATIONALES: dict[Regime, str] = {
    "INVERSION": (
        "Slope 2y-10y < 0 indica que o mercado precifica corte de juros à frente. "
        "Não é momento para alongar prazo prefixado — duração longa pode performar "
        "depois do início do ciclo. Hoje, manter pós-fixado limita o risco."
    ),
    "STEEPENING": (
        "Curva abrindo (steepening) significa que mercado precifica inflação "
        "maior à frente. Prefixado curto se beneficia de rolagem mais alta sem "
        "exposição à duration longa que sofreria com novas altas de juros."
    ),
    "FLATTENING": (
        "Curva achatando (flattening) com slope caindo: mercado precifica desaceleração "
        "ou cortes nominais. IPCA+ longo se beneficia tanto da queda do juro real "
        "quanto da inflação implícita preservada — duration vira ativo."
    ),
    "NORMAL": (
        "Curva em formato típico (slope levemente positivo, sem stress). "
        "Alocação balanceada entre os 3 indexadores reduz erro de timing — "
        "rotação só faz sentido em regimes claros."
    ),
}


def build_recommendation(regime: Regime) -> RegimeRecommendation:
    return {
        "regime": regime,
        "headline": HEADLINES[regime],
        "rationale": RATIONALES[regime],
        "suggested_allocation": ALLOCATIONS[regime],
    }


# ── Pipeline principal ────────────────────────────────────────────────────────


def analyze_regime(
    rows: list[tuple[date, float | None]],
    history_days: int = 90,
) -> RegimeResult | None:
    """Recebe `[(dia, slope_2y_10y), ...]` ordenado ASC e retorna análise.

    Retorna None se dados insuficientes (<30 rows não-nulas).
    """
    # Filtra rows válidas (slope não-nulo)
    valid = [(d, float(s)) for d, s in rows if s is not None]
    if len(valid) < 30:
        return None

    slopes = [s for _, s in valid]
    last_slope = slopes[-1]

    # Delta 20d: slope_hoje - slope_20d_atras
    if len(slopes) >= DELTA_WINDOW_DAYS + 1:
        delta_20d = last_slope - slopes[-(DELTA_WINDOW_DAYS + 1)]
    else:
        delta_20d = 0.0

    # Z-score do delta 20d em janela de 252d (rolling deltas)
    if len(slopes) >= DELTA_WINDOW_DAYS + 30:
        # Calcula histórico de deltas 20d
        deltas_history = [
            slopes[i] - slopes[i - DELTA_WINDOW_DAYS]
            for i in range(DELTA_WINDOW_DAYS, len(slopes))
        ]
        # Pega janela de até 252 deltas mais recentes
        window = deltas_history[-Z_WINDOW_DAYS:] if len(deltas_history) > Z_WINDOW_DAYS else deltas_history
        z_score = compute_z_score(window)
    else:
        z_score = 0.0

    regime, score = classify_regime(last_slope, delta_20d, z_score)

    # Histórico dos últimos N dias com regime classificado por dia
    # (usa janela rolling — para cada ponto, classifica com info disponível até ali)
    hist_start = max(0, len(valid) - history_days)
    history: list[RegimePoint] = []
    for i in range(hist_start, len(valid)):
        d_i, s_i = valid[i]
        # Para histórico, usa só o slope absoluto (sem recomputar z-score por ponto — caro)
        if s_i < INV_THRESHOLD:
            r_i = "INVERSION"
        elif i >= DELTA_WINDOW_DAYS and (slopes[i] - slopes[i - DELTA_WINDOW_DAYS]) > 0.003:
            r_i = "STEEPENING"
        elif i >= DELTA_WINDOW_DAYS and (slopes[i] - slopes[i - DELTA_WINDOW_DAYS]) < -0.003:
            r_i = "FLATTENING"
        else:
            r_i = "NORMAL"
        history.append({
            "dia": d_i.isoformat(),
            "slope_2y_10y": round(s_i, 6),
            "regime": r_i,
        })

    return {
        "regime": regime,
        "score": score,
        "slope_2y_10y": round(last_slope, 6),
        "slope_2y_10y_delta_20d": round(delta_20d, 6),
        "slope_z_score": round(z_score, 3),
        "last_date": valid[-1][0].isoformat(),
        "sample_size": len(valid),
        "history": history,
        "recommendation": build_recommendation(regime),
    }
