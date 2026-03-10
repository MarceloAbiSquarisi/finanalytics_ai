"""
Motor de correlacao entre ativos — dominio puro, sem I/O.

Responsabilidades:
  1. Extrair series de retornos diarios a partir de barras OHLC
  2. Alinhar series por timestamp (inner join)
  3. Calcular matriz de correlacao de Pearson
  4. Calcular correlacao rolante entre pares
  5. Derivar metricas de diversificacao

Design decisions:

  Retornos ao inves de precos:
    Correlacionar precos brutos e enganoso — series nao-estacionarias tendem
    a ter correlacoes altas apenas por crescimento conjunto. Retornos diarios
    (r_t = (p_t / p_{t-1}) - 1) sao estacionarios e refletem co-movimento real.
    Referencia: "The Misbehavior of Markets" (Mandelbrot), cap. 4.

  Inner join por timestamp:
    Ativos diferentes podem ter dias sem negociacao (feriados, halts).
    Usamos apenas os timestamps presentes em TODOS os tickers para garantir
    que a correlacao e calculada sobre os mesmos dias.
    Trade-off: perde-se dados — mas evita viés de comparar dias diferentes.

  Pearson puro (sem pandas/numpy):
    Stdlib only para manter a lib sem dependencias pesadas no dominio.
    Performance e adequada para N <= 15 tickers com <= 500 barras.
    Para N > 20 ou uso intensivo, a troca para numpy seria trivial.

  Score de diversificacao:
    Definido como 1 - avg(|correlacoes| excluindo diagonal).
    Varia de 0 (todos perfeitamente correlacionados) a 1 (todos ortogonais).
    Nao e uma metrica academica rigorosa, mas intuitiva para o usuario.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# ── Tipos ──────────────────────────────────────────────────────────────────────

ReturnSeries = list[float]  # serie de retornos diarios de um ativo
AlignedMatrix = dict[str, ReturnSeries]  # {ticker: [r_t0, r_t1, ...]} mesmo comprimento


# ── Extracao de retornos ───────────────────────────────────────────────────────


def extract_returns(bars: list[dict[str, Any]]) -> dict[int, float]:
    """
    Converte barras OHLC em retornos diarios indexados por timestamp.

    Retorna {timestamp: retorno_percentual} — apenas dias com retorno calculavel
    (a partir da segunda barra).

    Retorno: percentual simples (nao log-retorno) para facilitar interpretacao.
    """
    if len(bars) < 2:
        return {}

    result: dict[int, float] = {}
    for i in range(1, len(bars)):
        prev_close = bars[i - 1]["close"]
        curr_close = bars[i]["close"]
        ts = bars[i]["time"]

        if prev_close and prev_close != 0:
            result[ts] = (curr_close - prev_close) / prev_close * 100.0

    return result


def align_returns(
    series_map: dict[str, dict[int, float]],
) -> tuple[list[int], AlignedMatrix]:
    """
    Alinha series de retornos de multiplos tickers pelo timestamp (inner join).

    Retorna:
      timestamps: lista ordenada de timestamps comuns a todos os tickers
      aligned:    {ticker: [retornos na ordem dos timestamps]}

    Se nao ha timestamps em comum, retorna ([], {}).
    """
    if not series_map:
        return [], {}

    # Timestamps comuns (inner join)
    common_ts: set[int] = set.intersection(*[set(series.keys()) for series in series_map.values()])

    if not common_ts:
        return [], {}

    timestamps = sorted(common_ts)
    aligned: AlignedMatrix = {
        ticker: [series[ts] for ts in timestamps] for ticker, series in series_map.items()
    }
    return timestamps, aligned


# ── Pearson correlation ───────────────────────────────────────────────────────


def _pearson(x: ReturnSeries, y: ReturnSeries) -> float:
    """
    Coeficiente de correlacao de Pearson entre duas series de mesmo comprimento.

    Retorna 0.0 se uma das series e constante (desvio padrao zero)
    para evitar divisao por zero.
    """
    n = len(x)
    if n < 2:
        return 0.0

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x))
    std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y))

    if std_x == 0.0 or std_y == 0.0:
        return 0.0

    return cov / (std_x * std_y)


def correlation_matrix(aligned: AlignedMatrix) -> dict[str, dict[str, float]]:
    """
    Calcula a matriz de correlacao de Pearson NxN.

    Retorna {ticker_a: {ticker_b: pearson}} com:
      - diagonal = 1.0
      - matrix[a][b] == matrix[b][a] (simetrica)
    """
    tickers = list(aligned.keys())
    matrix: dict[str, dict[str, float]] = {}

    for i, ta in enumerate(tickers):
        matrix[ta] = {}
        for j, tb in enumerate(tickers):
            if i == j:
                matrix[ta][tb] = 1.0
            elif j < i:
                # Aproveita simetria — ja calculado
                matrix[ta][tb] = matrix[tb][ta]
            else:
                matrix[ta][tb] = round(_pearson(aligned[ta], aligned[tb]), 4)

    return matrix


# ── Correlacao rolante ────────────────────────────────────────────────────────


def rolling_correlation(
    series_a: ReturnSeries,
    series_b: ReturnSeries,
    timestamps: list[int],
    window: int = 30,
) -> list[dict[str, Any]]:
    """
    Calcula correlacao de Pearson rolante entre dois ativos.

    Para cada ponto t >= window, calcula Pearson sobre [t-window, t).

    Retorna lista de {time, correlation} pronta para o frontend (Chart.js).
    """
    n = len(series_a)
    if n != len(series_b) or n < window:
        return []

    result: list[dict[str, Any]] = []
    for i in range(window, n + 1):
        window_a = series_a[i - window : i]
        window_b = series_b[i - window : i]
        corr = _pearson(window_a, window_b)
        result.append(
            {
                "time": timestamps[i - 1],
                "correlation": round(corr, 4),
            }
        )

    return result


# ── Resultados ────────────────────────────────────────────────────────────────


@dataclass
class CorrelationPair:
    ticker_a: str
    ticker_b: str
    correlation: float
    label: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            self.label = f"{self.ticker_a} / {self.ticker_b}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker_a": self.ticker_a,
            "ticker_b": self.ticker_b,
            "correlation": self.correlation,
            "label": self.label,
        }


@dataclass
class CorrelationResult:
    """Resultado completo da analise de correlacao."""

    tickers: list[str]
    range_period: str
    common_bars: int  # dias em comum (inner join)
    matrix: dict[str, dict[str, float]]  # NxN Pearson
    most_correlated: list[CorrelationPair]  # top-3 pares mais correlacionados
    least_correlated: list[CorrelationPair]  # top-3 pares menos correlacionados
    diversification_score: float  # 0-1, maior = mais diversificado
    rolling_pairs: dict[str, list[dict]]  # {"A/B": [{time, correlation}]}
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tickers": self.tickers,
            "range_period": self.range_period,
            "common_bars": self.common_bars,
            "matrix": self.matrix,
            "most_correlated": [p.to_dict() for p in self.most_correlated],
            "least_correlated": [p.to_dict() for p in self.least_correlated],
            "diversification_score": round(self.diversification_score, 3),
            "rolling_pairs": self.rolling_pairs,
            "errors": self.errors,
            "total_tickers": len(self.tickers),
            "failed_tickers": len(self.errors),
        }


# ── Factory principal ─────────────────────────────────────────────────────────


def build_correlation_result(
    bars_map: dict[str, list[dict[str, Any]]],  # {ticker: [bars]}
    range_period: str,
    rolling_window: int = 30,
    errors: list[dict[str, str]] | None = None,
) -> CorrelationResult:
    """
    Constroi CorrelationResult a partir de barras OHLC de multiplos tickers.

    Fluxo:
      1. Extrai retornos de cada ticker
      2. Alinha por timestamp (inner join)
      3. Calcula matriz de correlacao
      4. Deriva pares mais/menos correlacionados
      5. Calcula correlacao rolante para todos os pares
      6. Calcula score de diversificacao
    """
    errors = errors or []
    tickers = list(bars_map.keys())

    # 1. Extrai retornos
    returns_map: dict[str, dict[int, float]] = {
        ticker: extract_returns(bars) for ticker, bars in bars_map.items()
    }

    # 2. Alinha
    timestamps, aligned = align_returns(returns_map)
    common_bars = len(timestamps)

    if not aligned or common_bars < 2:
        return CorrelationResult(
            tickers=tickers,
            range_period=range_period,
            common_bars=0,
            matrix={},
            most_correlated=[],
            least_correlated=[],
            diversification_score=0.0,
            rolling_pairs={},
            errors=errors,
        )

    # 3. Matriz
    matrix = correlation_matrix(aligned)

    # 4. Pares ordenados por |correlacao| (excluindo diagonal)
    pairs: list[CorrelationPair] = []
    ticker_list = list(aligned.keys())
    for i in range(len(ticker_list)):
        for j in range(i + 1, len(ticker_list)):
            ta, tb = ticker_list[i], ticker_list[j]
            pairs.append(
                CorrelationPair(
                    ticker_a=ta,
                    ticker_b=tb,
                    correlation=matrix[ta][tb],
                )
            )

    pairs_by_corr = sorted(pairs, key=lambda p: p.correlation, reverse=True)
    most_correlated = pairs_by_corr[:3]
    least_correlated = list(reversed(pairs_by_corr[-3:]))

    # 5. Correlacao rolante por par (todos os pares)
    rolling_pairs: dict[str, list[dict]] = {}
    for pair in pairs:
        series_a = aligned[pair.ticker_a]
        series_b = aligned[pair.ticker_b]
        key = f"{pair.ticker_a}/{pair.ticker_b}"
        rolling_pairs[key] = rolling_correlation(series_a, series_b, timestamps, window=rolling_window)

    # 6. Score de diversificacao
    off_diagonal = [abs(p.correlation) for p in pairs]
    diversification_score = 1.0 - (sum(off_diagonal) / len(off_diagonal)) if off_diagonal else 0.0

    return CorrelationResult(
        tickers=tickers,
        range_period=range_period,
        common_bars=common_bars,
        matrix=matrix,
        most_correlated=most_correlated,
        least_correlated=least_correlated,
        diversification_score=max(0.0, min(1.0, diversification_score)),
        rolling_pairs=rolling_pairs,
        errors=errors,
    )
