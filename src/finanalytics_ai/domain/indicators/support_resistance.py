"""
Support / Resistance — 3 algoritmos canônicos em Python puro.

Métodos implementados:
  1. **Swing High/Low (lookback)** — pivots de N barras de cada lado, agrupados
     em clusters por proximidade percentual. Conta "toques" como medida de força.
  2. **Classic Pivot Points** — PP, R1-R3, S1-S3 derivados do high/low/close
     do período anterior. Tradicional em DT (níveis para o próximo pregão).
  3. **Williams Fractals (5-bar)** — Bill Williams: ponto i é fractal de alta se
     high[i] > high[i±1] E high[i±1] > high[i±2]; análogo para baixas.

Design:
  - Python puro (zero deps), seguindo `technical.py`.
  - Funções puras: list[float] in → dict tipado out.
  - Tolerante a None/NaN nos inputs (skip silencioso).

Uso (exemplo):
    bars = [{"time":..., "high":..., "low":..., "close":...}, ...]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]

    swings = compute_swing_levels(highs, lows, lookback=5, cluster_pct=0.005)
    pivots = compute_classic_pivots(highs[-1], lows[-1], closes[-1])
    fractals = compute_williams_fractals(highs, lows)
"""

from __future__ import annotations

from typing import Literal, TypedDict


# ── TYPE ALIASES ──────────────────────────────────────────────────────────────


class Level(TypedDict):
    price: float
    kind: Literal["support", "resistance", "pivot"]
    strength: int  # 1=fraco; ≥2 = mais toques (swing) ou pivote relevante (classic)
    bar_index: int  # índice da barra original (último toque), ou -1 se agregado


class SwingLevelsResult(TypedDict):
    supports: list[Level]
    resistances: list[Level]
    lookback: int
    cluster_pct: float


class ClassicPivotsResult(TypedDict):
    pp: float
    r1: float
    r2: float
    r3: float
    s1: float
    s2: float
    s3: float
    levels: list[Level]  # versão lista (todos os 7) para iteração no frontend


class FractalPoint(TypedDict):
    bar_index: int
    price: float


class WilliamsFractalsResult(TypedDict):
    up_fractals: list[FractalPoint]  # resistências locais
    down_fractals: list[FractalPoint]  # suportes locais


# ── 1. SWING HIGH/LOW COM CLUSTERIZAÇÃO ───────────────────────────────────────


def compute_swing_levels(
    highs: list[float],
    lows: list[float],
    lookback: int = 5,
    cluster_pct: float = 0.005,
) -> SwingLevelsResult:
    """Detecta swing highs/lows e agrupa em níveis por proximidade percentual.

    Args:
        highs: série de máximas das barras.
        lows: série de mínimas das barras.
        lookback: quantas barras para cada lado precisam ser menores (alta) /
            maiores (baixa). Maior = menos pontos, mais robustos. Default 5.
        cluster_pct: tolerância para considerar dois swings o mesmo nível
            (ex: 0.005 = 0.5%). Default 0.5%.

    Returns:
        SwingLevelsResult com supports e resistances ordenados por preço.
        strength = nº de toques no cluster (≥1).
    """
    if len(highs) < 2 * lookback + 1 or len(lows) != len(highs):
        return SwingLevelsResult(
            supports=[], resistances=[], lookback=lookback, cluster_pct=cluster_pct
        )

    swing_highs: list[tuple[int, float]] = []  # (bar_index, price)
    swing_lows: list[tuple[int, float]] = []

    n = len(highs)
    for i in range(lookback, n - lookback):
        h = highs[i]
        lo = lows[i]
        if h is None or lo is None:
            continue
        # Swing high: maior em [i-lookback, i+lookback]
        is_swing_high = True
        is_swing_low = True
        for j in range(i - lookback, i + lookback + 1):
            if j == i:
                continue
            if highs[j] is not None and highs[j] >= h:
                is_swing_high = False
            if lows[j] is not None and lows[j] <= lo:
                is_swing_low = False
            if not is_swing_high and not is_swing_low:
                break
        if is_swing_high:
            swing_highs.append((i, h))
        if is_swing_low:
            swing_lows.append((i, lo))

    resistances = _cluster_levels(swing_highs, cluster_pct, "resistance")
    supports = _cluster_levels(swing_lows, cluster_pct, "support")

    return SwingLevelsResult(
        supports=sorted(supports, key=lambda x: x["price"]),
        resistances=sorted(resistances, key=lambda x: x["price"]),
        lookback=lookback,
        cluster_pct=cluster_pct,
    )


def _cluster_levels(
    points: list[tuple[int, float]],
    cluster_pct: float,
    kind: Literal["support", "resistance"],
) -> list[Level]:
    """Agrupa pontos próximos em clusters; retorna preço médio + nº de toques."""
    if not points:
        return []
    # Ordena por preço para clusterização linear
    sorted_pts = sorted(points, key=lambda x: x[1])
    clusters: list[list[tuple[int, float]]] = []
    current: list[tuple[int, float]] = [sorted_pts[0]]
    for idx, price in sorted_pts[1:]:
        # Compara contra preço médio do cluster atual
        cluster_avg = sum(p[1] for p in current) / len(current)
        if abs(price - cluster_avg) / max(abs(cluster_avg), 1e-9) <= cluster_pct:
            current.append((idx, price))
        else:
            clusters.append(current)
            current = [(idx, price)]
    clusters.append(current)

    return [
        Level(
            price=round(sum(p[1] for p in cl) / len(cl), 4),
            kind=kind,
            strength=len(cl),
            bar_index=max(p[0] for p in cl),  # último toque
        )
        for cl in clusters
    ]


# ── 2. CLASSIC PIVOT POINTS ───────────────────────────────────────────────────


def compute_classic_pivots(
    prev_high: float, prev_low: float, prev_close: float
) -> ClassicPivotsResult:
    """Pivot points clássicos a partir de high/low/close do período anterior.

    Fórmulas:
        PP  = (H + L + C) / 3
        R1  = 2*PP - L         S1 = 2*PP - H
        R2  = PP + (H - L)     S2 = PP - (H - L)
        R3  = H + 2*(PP - L)   S3 = L - 2*(H - PP)

    Uso típico: passar high/low/close do dia anterior para projetar níveis
    do próximo pregão.
    """
    h, lo, c = float(prev_high), float(prev_low), float(prev_close)
    pp = (h + lo + c) / 3.0
    r1 = 2 * pp - lo
    s1 = 2 * pp - h
    r2 = pp + (h - lo)
    s2 = pp - (h - lo)
    r3 = h + 2 * (pp - lo)
    s3 = lo - 2 * (h - pp)

    levels: list[Level] = [
        Level(price=round(s3, 4), kind="support", strength=3, bar_index=-1),
        Level(price=round(s2, 4), kind="support", strength=2, bar_index=-1),
        Level(price=round(s1, 4), kind="support", strength=1, bar_index=-1),
        Level(price=round(pp, 4), kind="pivot", strength=2, bar_index=-1),
        Level(price=round(r1, 4), kind="resistance", strength=1, bar_index=-1),
        Level(price=round(r2, 4), kind="resistance", strength=2, bar_index=-1),
        Level(price=round(r3, 4), kind="resistance", strength=3, bar_index=-1),
    ]

    return ClassicPivotsResult(
        pp=round(pp, 4),
        r1=round(r1, 4),
        r2=round(r2, 4),
        r3=round(r3, 4),
        s1=round(s1, 4),
        s2=round(s2, 4),
        s3=round(s3, 4),
        levels=levels,
    )


# ── 3. WILLIAMS FRACTALS (5-bar) ──────────────────────────────────────────────


def compute_williams_fractals(
    highs: list[float], lows: list[float]
) -> WilliamsFractalsResult:
    """Bill Williams 5-bar fractals.

    Up Fractal em i (resistência local):
        high[i-2] < high[i-1] < high[i] > high[i+1] > high[i+2]

    Down Fractal em i (suporte local):
        low[i-2] > low[i-1] > low[i] < low[i+1] < low[i+2]

    Returns:
        WilliamsFractalsResult com listas de pontos (bar_index, price).
        Os 2 primeiros e 2 últimos índices nunca são fractais (faltam vizinhos).
    """
    n = len(highs)
    if n < 5 or len(lows) != n:
        return WilliamsFractalsResult(up_fractals=[], down_fractals=[])

    up: list[FractalPoint] = []
    down: list[FractalPoint] = []

    for i in range(2, n - 2):
        h = highs[i]
        lo = lows[i]
        if h is not None and all(highs[j] is not None for j in range(i - 2, i + 3)):
            if highs[i - 2] < highs[i - 1] < h > highs[i + 1] > highs[i + 2]:
                up.append(FractalPoint(bar_index=i, price=round(h, 4)))
        if lo is not None and all(lows[j] is not None for j in range(i - 2, i + 3)):
            if lows[i - 2] > lows[i - 1] > lo < lows[i + 1] < lows[i + 2]:
                down.append(FractalPoint(bar_index=i, price=round(lo, 4)))

    return WilliamsFractalsResult(up_fractals=up, down_fractals=down)
