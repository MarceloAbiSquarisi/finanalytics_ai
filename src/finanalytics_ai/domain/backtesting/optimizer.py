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


# ── Walk-Forward Validation ───────────────────────────────────────────────────
#
# Metodologia:
#
#   O dataset e dividido em N janelas (folds). Cada fold tem:
#     - Janela IN-SAMPLE  (IS):  usado para otimizacao (grid search)
#     - Janela OUT-OF-SAMPLE (OOS): usado para validacao com os parametros
#                                    encontrados no IS
#
#   Diagrama para n_splits=3, anchored=False (rolling):
#     [IS1        ][OOS1]
#          [IS2        ][OOS2]
#               [IS3        ][OOS3]
#
#   Diagrama para anchored=True (expanding):
#     [IS1        ][OOS1]
#     [IS1 + IS2       ][OOS2]
#     [IS1 + IS2 + IS3      ][OOS3]
#
#   Anchored = True e mais conservador: treina com mais dados a cada fold,
#   mas tambem e mais pesado computacionalmente.
#
# Metricas de robustez:
#   - efficiency_ratio: media(OOS scores) / melhor IS score
#     > 0.5 indica boa generalizacao
#   - consistency: fracao de folds OOS com retorno positivo
#   - avg_oos_sharpe: Sharpe medio fora da amostra
#   - degradation: diferenca media entre IS score e OOS score por fold
#     Pequena degradacao indica parametros robustos


@dataclass
class WalkForwardFold:
    """Resultado de um fold do walk-forward."""
    fold:          int
    is_start_bar:  int
    is_end_bar:    int
    oos_start_bar: int
    oos_end_bar:   int
    is_bars:       int
    oos_bars:      int
    # Melhores params encontrados no IS
    best_params:   dict[str, Any]
    best_is_score: float
    best_is_trades: int
    # Performance OOS com os params do IS
    oos_metrics:   BacktestMetrics | None
    oos_score:     float
    oos_valid:     bool  # True se >= MIN_TRADES no OOS

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold":           self.fold,
            "is_bars":        self.is_bars,
            "oos_bars":       self.oos_bars,
            "best_params":    self.best_params,
            "best_is_score":  round(self.best_is_score, 4),
            "best_is_trades": self.best_is_trades,
            "oos_score":      round(self.oos_score, 4),
            "oos_valid":      self.oos_valid,
            "oos_metrics":    self.oos_metrics.to_dict() if self.oos_metrics else None,
        }


@dataclass
class WalkForwardResult:
    """Resultado completo da validacao walk-forward."""
    ticker:           str
    strategy:         str
    range_period:     str
    objective:        str
    n_splits:         int
    anchored:         bool
    total_bars:       int
    folds:            list[WalkForwardFold]
    # Metricas de robustez agregadas
    avg_oos_score:    float
    avg_is_score:     float
    efficiency_ratio: float   # avg OOS / melhor IS — quanto dos IS ganhos se mantem no OOS
    consistency:      float   # % de folds com OOS score > 0
    degradation:      float   # media(IS score - OOS score) — menor = melhor
    # Equity OOS concatenada (como se tivesse operado ao vivo)
    combined_equity:  list[dict[str, Any]]
    combined_return:  float

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker":           self.ticker,
            "strategy":         self.strategy,
            "range_period":     self.range_period,
            "objective":        self.objective,
            "n_splits":         self.n_splits,
            "anchored":         self.anchored,
            "total_bars":       self.total_bars,
            "folds":            [f.to_dict() for f in self.folds],
            "avg_oos_score":    round(self.avg_oos_score, 4),
            "avg_is_score":     round(self.avg_is_score, 4),
            "efficiency_ratio": round(self.efficiency_ratio, 4),
            "consistency":      round(self.consistency, 1),
            "degradation":      round(self.degradation, 4),
            "combined_equity":  self.combined_equity,
            "combined_return":  round(self.combined_return, 2),
        }


def walk_forward(
    bars:            list[dict[str, Any]],
    strategy_name:   str,
    ticker:          str           = "TICKER",
    range_period:    str           = "wf",
    initial_capital: float         = 10_000.0,
    position_size:   float         = 1.0,
    commission_pct:  float         = 0.001,
    objective:       OptimizationObjective = OptimizationObjective.SHARPE,
    n_splits:        int           = 3,
    oos_pct:         float         = 0.3,     # 30% de cada fold para OOS
    anchored:        bool          = False,   # True = expanding window
    custom_space:    dict[str, list[Any]] | None = None,
) -> WalkForwardResult:
    """
    Executa walk-forward validation sincronamente.

    Algoritmo:
      1. Divide bars em n_splits folds (IS + OOS por fold)
      2. Para cada fold:
         a. Otimiza parametros no IS via grid_search
         b. Aplica o melhor parametro no OOS
         c. Registra performance IS vs OOS
      3. Agrega metricas de robustez

    Parametros:
      n_splits:   Numero de folds (2 a 6)
      oos_pct:    Fracao OOS por fold (0.1 a 0.5)
      anchored:   False = rolling (mesmo tamanho IS), True = expanding
    """
    n_splits = max(2, min(6, n_splits))
    oos_pct  = max(0.1, min(0.5, oos_pct))

    n = len(bars)
    # Tamanho de cada fold (IS + OOS)
    fold_size = n // n_splits
    oos_size  = max(1, int(fold_size * oos_pct))
    is_size   = fold_size - oos_size

    if is_size < 30:
        raise ValueError(
            f"Janela IS muito pequena ({is_size} barras). "
            f"Use mais dados (>= 2y) ou menos splits ({n_splits})."
        )
    if oos_size < 10:
        raise ValueError(
            f"Janela OOS muito pequena ({oos_size} barras). "
            f"Aumente o periodo ou reduza n_splits."
        )

    folds: list[WalkForwardFold] = []
    combined_equity: list[dict[str, Any]] = []
    current_capital = initial_capital

    for split_idx in range(n_splits):
        oos_end   = n - (n_splits - 1 - split_idx) * fold_size
        oos_start = oos_end - oos_size

        if anchored:
            # Expanding: IS sempre começa na barra 0
            is_start = 0
        else:
            # Rolling: IS tem tamanho fixo
            is_start = max(0, oos_start - is_size)

        is_end = oos_start

        if is_end <= is_start or oos_end > n:
            continue

        is_bars_slice  = bars[is_start:is_end]
        oos_bars_slice = bars[oos_start:oos_end]

        # ── IS: otimiza parametros ────────────────────────────────────────────
        try:
            is_result = grid_search(
                bars            = is_bars_slice,
                strategy_name   = strategy_name,
                ticker          = ticker,
                range_period    = "is",
                initial_capital = initial_capital,
                position_size   = position_size,
                commission_pct  = commission_pct,
                objective       = objective,
                top_n           = 1,
                custom_space    = custom_space,
            )
            if not is_result.top:
                continue
            best_params   = is_result.best_params
            best_is_score = is_result.best_score
            best_is_trades = is_result.top[0].metrics.total_trades
        except Exception:
            continue

        # ── OOS: valida com os params do IS ───────────────────────────────────
        oos_metrics = None
        oos_score   = 0.0
        oos_valid   = False

        try:
            oos_strategy = get_strategy(strategy_name, best_params)
            oos_bt       = run_backtest(
                bars            = oos_bars_slice,
                strategy        = oos_strategy,
                ticker          = ticker,
                initial_capital = current_capital,   # capital atual (composto)
                position_size   = position_size,
                commission_pct  = commission_pct,
                range_period    = "oos",
            )
            oos_metrics = oos_bt.metrics
            oos_valid   = oos_metrics.total_trades >= MIN_TRADES
            oos_score   = _score(oos_metrics, objective) if oos_valid else 0.0

            # Equity OOS concatenada (capital composto entre folds)
            for pt in oos_bt.equity_curve:
                combined_equity.append({
                    "time":   pt["time"],
                    "equity": pt["equity"],
                    "fold":   split_idx + 1,
                })

            # Atualiza capital para o proximo fold (compounding)
            if oos_bt.equity_curve:
                current_capital = oos_bt.equity_curve[-1]["equity"]

        except Exception:
            pass

        folds.append(WalkForwardFold(
            fold          = split_idx + 1,
            is_start_bar  = is_start,
            is_end_bar    = is_end,
            oos_start_bar = oos_start,
            oos_end_bar   = oos_end,
            is_bars       = len(is_bars_slice),
            oos_bars      = len(oos_bars_slice),
            best_params   = best_params,
            best_is_score = best_is_score,
            best_is_trades = best_is_trades,
            oos_metrics   = oos_metrics,
            oos_score     = oos_score,
            oos_valid     = oos_valid,
        ))

    if not folds:
        raise ValueError("Nenhum fold valido gerado. Verifique o periodo e n_splits.")

    # ── Metricas de robustez ──────────────────────────────────────────────────
    valid_folds    = [f for f in folds if f.oos_valid]
    all_oos_scores = [f.oos_score for f in folds]
    all_is_scores  = [f.best_is_score for f in folds]

    avg_oos   = sum(all_oos_scores) / len(all_oos_scores) if all_oos_scores else 0.0
    avg_is    = sum(all_is_scores)  / len(all_is_scores)  if all_is_scores  else 0.0
    eff_ratio = avg_oos / avg_is if avg_is > 0 else 0.0
    consistency = (sum(1 for s in all_oos_scores if s > 0) / len(all_oos_scores) * 100
                   if all_oos_scores else 0.0)
    degradation = (sum(f.best_is_score - f.oos_score for f in folds) / len(folds)
                   if folds else 0.0)

    # Retorno total composto via equity OOS
    combined_return = ((current_capital - initial_capital) / initial_capital * 100
                       if initial_capital > 0 else 0.0)

    return WalkForwardResult(
        ticker           = ticker,
        strategy         = strategy_name,
        range_period     = range_period,
        objective        = objective.value,
        n_splits         = n_splits,
        anchored         = anchored,
        total_bars       = n,
        folds            = folds,
        avg_oos_score    = avg_oos,
        avg_is_score     = avg_is,
        efficiency_ratio = eff_ratio,
        consistency      = consistency,
        degradation      = degradation,
        combined_equity  = combined_equity,
        combined_return  = combined_return,
    )
