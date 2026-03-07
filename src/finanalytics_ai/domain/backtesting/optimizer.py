"""
Otimizacao de parametros via grid search.

Design decisions:

  Dados buscados UMA vez:
    O OptimizerService busca as barras OHLC uma unica vez e passa para
    grid_search. Evita N chamadas a API externa — crucial para grids grandes.

  CPU-bound puro:
    grid_search e sincrono. O service roda em asyncio.to_thread para nao
    bloquear o event loop da FastAPI.

  Limite de combinacoes:
    MAX_COMBINATIONS = 500 por request para proteger o servidor.
    Um grid RSI 4x3x3 = 36 combinacoes roda em ~50ms.

  Objetivos de otimizacao:
    SHARPE   — melhor retorno ajustado por risco (padrao)
    RETURN   — maior retorno absoluto (agressivo)
    CALMAR   — retorno / max drawdown (conservador)
    WIN_RATE — maior taxa de acerto
    PROFIT_F — maior profit factor

  Penalizacao por poucos trades:
    Resultados com < MIN_TRADES sao penalizados na ordenacao.
    Evita overfitting em parametros que geram 1-2 trades felizes.

  Heatmap data:
    Para estrategias com 2 parametros principais, retorna matriz 2D
    de scores para renderizacao como heatmap no frontend.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from finanalytics_ai.domain.backtesting.engine import BacktestMetrics, run_backtest
from finanalytics_ai.domain.backtesting.strategies.technical import get_strategy

MIN_TRADES     = 3     # minimo de trades para resultado ser valido
MAX_COMBINATIONS = 500 # limite hard para evitar abuse


# ── Objetivo de otimizacao ────────────────────────────────────────────────────

class OptimizationObjective(StrEnum):
    SHARPE    = "sharpe"
    RETURN    = "return"
    CALMAR    = "calmar"
    WIN_RATE  = "win_rate"
    PROFIT_F  = "profit_factor"


def _score(metrics: BacktestMetrics, objective: OptimizationObjective) -> float:
    """Extrai o valor do objetivo. Maior e sempre melhor."""
    match objective:
        case OptimizationObjective.SHARPE:
            return metrics.sharpe_ratio
        case OptimizationObjective.RETURN:
            return metrics.total_return_pct
        case OptimizationObjective.CALMAR:
            return metrics.calmar_ratio
        case OptimizationObjective.WIN_RATE:
            return metrics.win_rate_pct
        case OptimizationObjective.PROFIT_F:
            return metrics.profit_factor
    return metrics.sharpe_ratio


# ── Espacos de parametros ─────────────────────────────────────────────────────

# Cada entrada: nome_do_parametro -> lista de valores a testar
PARAM_SPACES: dict[str, dict[str, list[Any]]] = {
    "rsi": {
        "period":     [7, 10, 14, 21],
        "oversold":   [25.0, 30.0, 35.0],
        "overbought": [65.0, 70.0, 75.0],
    },
    "macd": {
        "fast":          [8, 10, 12, 15],
        "slow":          [20, 24, 26, 30],
        "signal_period": [7, 9, 11],
    },
    "combined": {
        "rsi_period":     [10, 14, 21],
        "rsi_oversold":   [25.0, 30.0, 35.0],
        "rsi_overbought": [65.0, 70.0, 75.0],
        "macd_fast":      [10, 12],
        "macd_slow":      [24, 26],
        "macd_signal":    [9],
    },
    "bollinger": {
        "period":  [10, 15, 20, 25],
        "std_dev": [1.5, 2.0, 2.5],
    },
    "ema_cross": {
        "fast": [5, 9, 13, 21],
        "slow": [13, 21, 34, 50],
    },
    "momentum": {
        "period":     [5, 10, 15, 20],
        "rsi_filter": [0.0, 55.0, 65.0, 75.0],
    },
}

# Dois parametros principais para o heatmap (por estrategia)
HEATMAP_AXES: dict[str, tuple[str, str]] = {
    "rsi":       ("period",    "oversold"),
    "macd":      ("fast",      "slow"),
    "combined":  ("rsi_period","rsi_oversold"),
    "bollinger": ("period",    "std_dev"),
    "ema_cross": ("fast",      "slow"),
    "momentum":  ("period",    "rsi_filter"),
}


# ── Resultado da otimizacao ───────────────────────────────────────────────────

@dataclass
class OptimizedRun:
    """Um resultado individual do grid search."""
    rank:      int
    params:    dict[str, Any]
    metrics:   BacktestMetrics
    score:     float
    is_valid:  bool  # True se >= MIN_TRADES

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank":     self.rank,
            "params":   self.params,
            "score":    round(self.score, 4),
            "is_valid": self.is_valid,
            "metrics":  self.metrics.to_dict(),
        }


@dataclass
class OptimizationResult:
    """Resultado completo do grid search."""
    ticker:         str
    strategy:       str
    range_period:   str
    objective:      str
    total_runs:     int
    valid_runs:     int
    top:            list[OptimizedRun]
    heatmap:        dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker":       self.ticker,
            "strategy":     self.strategy,
            "range_period": self.range_period,
            "objective":    self.objective,
            "total_runs":   self.total_runs,
            "valid_runs":   self.valid_runs,
            "top":          [r.to_dict() for r in self.top],
            "heatmap":      self.heatmap,
            "best_params":  self.best_params,
            "best_score":   round(self.best_score, 4),
        }

    @property
    def best_params(self) -> dict[str, Any]:
        return self.top[0].params if self.top else {}

    @property
    def best_score(self) -> float:
        return self.top[0].score if self.top else 0.0


# ── Grid Search ───────────────────────────────────────────────────────────────

def grid_search(
    bars:            list[dict[str, Any]],
    strategy_name:   str,
    ticker:          str           = "TICKER",
    range_period:    str           = "1y",
    initial_capital: float         = 10_000.0,
    position_size:   float         = 1.0,
    commission_pct:  float         = 0.001,
    objective:       OptimizationObjective = OptimizationObjective.SHARPE,
    top_n:           int           = 10,
    custom_space:    dict[str, list[Any]] | None = None,
) -> OptimizationResult:
    """
    Executa grid search sincronamente sobre os parametros da estrategia.

    Parametros:
      bars:          Barras OHLC ja buscadas (evitar N chamadas API)
      strategy_name: Nome da estrategia no registro
      custom_space:  Espaco customizado (sobrescreve o default)
      top_n:         Quantos melhores resultados retornar

    Retorna OptimizationResult com top N resultados + heatmap data.

    Design: penaliza runs com < MIN_TRADES empurrando-os para o final,
    mas ainda os inclui no resultado para o usuario ver o tradeoff.
    """
    space = custom_space or PARAM_SPACES.get(strategy_name, {})

    if not space:
        raise ValueError(
            f"Sem espaco de parametros para '{strategy_name}'. "
            f"Disponiveis: {list(PARAM_SPACES)}"
        )

    # Gera todas as combinacoes
    param_names  = list(space.keys())
    param_values = list(space.values())
    combos       = list(itertools.product(*param_values))

    # Filtra combinacoes invalidas (ex: ema_cross fast >= slow)
    combos = _filter_invalid(strategy_name, param_names, combos)

    if len(combos) > MAX_COMBINATIONS:
        raise ValueError(
            f"Grid muito grande: {len(combos)} combinacoes "
            f"(limite: {MAX_COMBINATIONS}). Reduza os espacos de parametros."
        )

    runs: list[OptimizationResult.__class__] = []
    raw_results: list[tuple[dict[str, Any], BacktestMetrics, float, bool]] = []

    for combo in combos:
        params = dict(zip(param_names, combo))
        try:
            strategy = get_strategy(strategy_name, params)
            result   = run_backtest(
                bars            = bars,
                strategy        = strategy,
                ticker          = ticker,
                initial_capital = initial_capital,
                position_size   = position_size,
                commission_pct  = commission_pct,
                range_period    = range_period,
            )
            m        = result.metrics
            is_valid = m.total_trades >= MIN_TRADES
            score    = _score(m, objective) if is_valid else -999.0
            raw_results.append((params, m, score, is_valid))
        except Exception:
            # Parametros invalidos (ex: fast > slow) — pula silenciosamente
            continue

    # Ordena: validos primeiro (por score desc), depois invalidos
    raw_results.sort(key=lambda x: (x[3], x[2]), reverse=True)

    valid_count = sum(1 for _, _, _, v in raw_results if v)

    top_runs: list[OptimizedRun] = []
    for rank, (params, metrics, score, is_valid) in enumerate(raw_results[:top_n], start=1):
        top_runs.append(OptimizedRun(
            rank=rank,
            params=params,
            metrics=metrics,
            score=score if is_valid else _score(metrics, objective),
            is_valid=is_valid,
        ))

    # Monta heatmap (2 parametros principais vs score)
    heatmap = _build_heatmap(
        strategy_name, param_names, raw_results, objective
    )

    return OptimizationResult(
        ticker       = ticker,
        strategy     = strategy_name,
        range_period = range_period,
        objective    = objective.value,
        total_runs   = len(raw_results),
        valid_runs   = valid_count,
        top          = top_runs,
        heatmap      = heatmap,
    )


def _filter_invalid(
    strategy: str,
    names: list[str],
    combos: list[tuple[Any, ...]],
) -> list[tuple[Any, ...]]:
    """Remove combinacoes logicamente invalidas antes do backtest."""
    filtered = []
    for combo in combos:
        p = dict(zip(names, combo))
        if strategy == "ema_cross":
            # fast deve ser menor que slow
            if p.get("fast", 0) >= p.get("slow", 1):
                continue
        if strategy == "macd":
            # fast deve ser menor que slow
            if p.get("fast", 0) >= p.get("slow", 1):
                continue
        filtered.append(combo)
    return filtered


def _build_heatmap(
    strategy:     str,
    param_names:  list[str],
    raw_results:  list[tuple[dict[str, Any], BacktestMetrics, float, bool]],
    objective:    OptimizationObjective,
) -> dict[str, Any]:
    """
    Monta dados de heatmap 2D para os dois parametros principais.

    Retorna:
      x_label, y_label: nomes dos eixos
      x_values, y_values: valores unicos de cada eixo
      matrix: lista de {x, y, score} para renderizacao
    """
    axes = HEATMAP_AXES.get(strategy)
    if not axes:
        return {}

    x_axis, y_axis = axes
    if x_axis not in param_names or y_axis not in param_names:
        return {}

    # Agrega: para cada (x, y), pega o melhor score dentre os valid runs
    # (os outros params foram variados mas fixamos os 2 eixos do heatmap)
    cell_scores: dict[tuple[Any, Any], list[float]] = {}
    for params, metrics, score, is_valid in raw_results:
        if not is_valid:
            continue
        x_val = params.get(x_axis)
        y_val = params.get(y_axis)
        if x_val is None or y_val is None:
            continue
        key = (x_val, y_val)
        if key not in cell_scores:
            cell_scores[key] = []
        cell_scores[key].append(_score(metrics, objective))

    if not cell_scores:
        return {}

    x_vals = sorted(set(k[0] for k in cell_scores))
    y_vals = sorted(set(k[1] for k in cell_scores))

    matrix = []
    for y in y_vals:
        for x in x_vals:
            scores = cell_scores.get((x, y), [])
            best   = max(scores) if scores else None
            matrix.append({
                "x":     x,
                "y":     y,
                "score": round(best, 4) if best is not None else None,
            })

    return {
        "x_label":  x_axis,
        "y_label":  y_axis,
        "x_values": x_vals,
        "y_values": y_vals,
        "matrix":   matrix,
        "objective": objective.value,
    }
