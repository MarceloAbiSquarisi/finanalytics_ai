"""
Comparativo multi-ticker — dominio puro.

Responsabilidade: agregar N OptimizationResult em uma visao consolidada.

Design decisions:

  Sem I/O aqui:
    Este modulo nao conhece BrapiClient nem FastAPI.
    Recebe uma lista de OptimizationResult ja calculados
    e produz MultiTickerResult — testavel sem mocks.

  Ranking por score normalizado:
    Cada ticker recebe um score absoluto (melhor params do grid search)
    E um score normalizado 0-100 relativo ao melhor do grupo.
    Permite comparar estrategias em ativos com magnitudes diferentes.

  Metricas de consistencia:
    avg_score:   media dos melhores scores (qualidade geral da estrategia)
    hit_rate:    % de tickers onde a estrategia gerou trades validos
    score_std:   desvio padrao dos scores (robustez — menor = mais consistente)
    best_ticker / worst_ticker: extremos do ranking

  Correlation insight:
    Se hit_rate < 50%, a estrategia nao e adequada para esse conjunto de ativos.
    Se score_std > avg_score, os resultados sao muito inconsistentes.

  Sem limite de tickers aqui:
    O limite (MAX_TICKERS = 10) e aplicado na camada de servico/rota.
    Este modulo e agnóstico ao tamanho do input.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from finanalytics_ai.domain.backtesting.optimizer import OptimizationResult

MAX_TICKERS = 10  # limite aplicado no service/rota


@dataclass
class TickerRanking:
    """Resultado de um ticker no comparativo."""

    rank: int
    ticker: str
    best_score: float
    score_pct: float  # 0-100, relativo ao melhor do grupo
    best_params: dict[str, Any]
    total_runs: int
    valid_runs: int
    top_metrics: dict[str, Any]  # metricas do melhor run
    has_valid: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "ticker": self.ticker,
            "best_score": round(self.best_score, 4),
            "score_pct": round(self.score_pct, 1),
            "best_params": self.best_params,
            "total_runs": self.total_runs,
            "valid_runs": self.valid_runs,
            "top_metrics": self.top_metrics,
            "has_valid": self.has_valid,
        }


@dataclass
class MultiTickerResult:
    """Resultado consolidado do comparativo multi-ticker."""

    strategy: str
    range_period: str
    objective: str
    tickers: list[str]
    rankings: list[TickerRanking]
    # Metricas de consistencia da estrategia no grupo
    avg_score: float
    score_std: float
    hit_rate: float  # % tickers com runs validos
    best_ticker: str
    worst_ticker: str
    # Melhor conjunto de parametros consenso (mais frequente entre top-1 de cada ticker)
    consensus_params: dict[str, Any]
    errors: list[dict[str, str]]  # tickers que falharam com motivo

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "range_period": self.range_period,
            "objective": self.objective,
            "tickers": self.tickers,
            "rankings": [r.to_dict() for r in self.rankings],
            "avg_score": round(self.avg_score, 4),
            "score_std": round(self.score_std, 4),
            "hit_rate": round(self.hit_rate, 1),
            "best_ticker": self.best_ticker,
            "worst_ticker": self.worst_ticker,
            "consensus_params": self.consensus_params,
            "errors": self.errors,
            "total_tickers": len(self.tickers),
            "failed_tickers": len(self.errors),
        }


def build_multi_ticker_result(
    results: list[tuple[str, OptimizationResult | Exception]],
    strategy: str,
    range_period: str,
    objective: str,
) -> MultiTickerResult:
    """
    Agrega resultados de N tickers em um MultiTickerResult.

    Parametros:
      results: lista de (ticker, OptimizationResult | Exception)
               Exception representa falha na busca/otimizacao daquele ticker

    Algoritmo:
      1. Separa sucessos de erros
      2. Extrai best_score de cada OptimizationResult
      3. Normaliza scores para 0-100 relativo ao maximo
      4. Ordena por score desc
      5. Calcula metricas de consistencia
      6. Encontra parametros consenso
    """
    successes: list[tuple[str, OptimizationResult]] = []
    errors: list[dict[str, str]] = []

    for ticker, result in results:
        if isinstance(result, Exception):
            errors.append({"ticker": ticker, "error": str(result)})
        else:
            successes.append((ticker, result))

    tickers = [t for t, _ in results]

    if not successes:
        return MultiTickerResult(
            strategy=strategy,
            range_period=range_period,
            objective=objective,
            tickers=tickers,
            rankings=[],
            avg_score=0.0,
            score_std=0.0,
            hit_rate=0.0,
            best_ticker="",
            worst_ticker="",
            consensus_params={},
            errors=errors,
        )

    # Extrai scores e build raw rankings
    raw: list[tuple[str, float, OptimizationResult]] = []
    for ticker, opt_result in successes:
        raw.append((ticker, opt_result.best_score, opt_result))

    raw.sort(key=lambda x: x[1], reverse=True)

    max_score = raw[0][1] if raw else 1.0
    min_score = raw[-1][1] if raw else 0.0
    score_range = max_score - min_score if max_score != min_score else 1.0

    rankings: list[TickerRanking] = []
    for rank, (ticker, score, opt_result) in enumerate(raw, start=1):
        score_pct = ((score - min_score) / score_range * 100) if raw else 0.0
        # Se o ticker esta no topo absoluto, força 100
        if rank == 1:
            score_pct = 100.0

        top = opt_result.top[0] if opt_result.top else None
        rankings.append(
            TickerRanking(
                rank=rank,
                ticker=ticker,
                best_score=score,
                score_pct=round(score_pct, 1),
                best_params=opt_result.best_params,
                total_runs=opt_result.total_runs,
                valid_runs=opt_result.valid_runs,
                top_metrics=top.metrics.to_dict() if top else {},
                has_valid=opt_result.valid_runs > 0,
            )
        )

    # Metricas de consistencia
    scores = [r.best_score for r in rankings]
    avg_score = sum(scores) / len(scores) if scores else 0.0
    variance = sum((s - avg_score) ** 2 for s in scores) / len(scores) if scores else 0.0
    score_std = math.sqrt(variance)
    hit_rate = sum(1 for r in rankings if r.has_valid) / len(rankings) * 100 if rankings else 0.0

    best_ticker = rankings[0].ticker if rankings else ""
    worst_ticker = rankings[-1].ticker if rankings else ""

    # Parametros consenso: valor mais frequente para cada parametro entre top-1 de cada ticker
    consensus_params = _find_consensus_params([r.best_params for r in rankings if r.has_valid])

    return MultiTickerResult(
        strategy=strategy,
        range_period=range_period,
        objective=objective,
        tickers=tickers,
        rankings=rankings,
        avg_score=avg_score,
        score_std=score_std,
        hit_rate=hit_rate,
        best_ticker=best_ticker,
        worst_ticker=worst_ticker,
        consensus_params=consensus_params,
        errors=errors,
    )


def _find_consensus_params(params_list: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Encontra o valor mais frequente de cada parametro entre os melhores runs.

    Para parametros numericos continuos (float), agrupa por valor exato
    (os espacos de parametros sao discretos, entao isso e correto).

    Em caso de empate, retorna o maior valor (mais conservador para
    parametros como oversold, mais agressivo para period — aceitavel como
    heuristica de consenso).
    """
    if not params_list:
        return {}

    all_keys: set[str] = set()
    for p in params_list:
        all_keys.update(p.keys())

    consensus: dict[str, Any] = {}
    for key in all_keys:
        values = [p[key] for p in params_list if key in p]
        if not values:
            continue
        # Conta frequencia
        freq: dict[Any, int] = {}
        for v in values:
            freq[v] = freq.get(v, 0) + 1
        # Pega o mais frequente; em empate, o maior
        max_freq = max(freq.values())
        candidates = [v for v, c in freq.items() if c == max_freq]
        consensus[key] = max(candidates)

    return consensus
