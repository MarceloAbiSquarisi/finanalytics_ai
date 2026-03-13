"""
OptimizerService — camada de aplicacao para otimizacao de parametros.

Responsabilidades:
  1. Buscar dados OHLC uma unica vez (evitar N chamadas a API)
  2. Executar grid_search em asyncio.to_thread (CPU-bound, nao bloqueia loop)
  3. Retornar OptimizationResult serializado

Design decision — asyncio.to_thread vs ProcessPoolExecutor:
  Grid search com 500 combinacoes roda em ~300ms (Python puro, sem numpy).
  asyncio.to_thread e suficiente — evita overhead de fork/pickling.
  Para grids muito grandes ou uso intenso, escalar para ProcessPoolExecutor
  com um pool de workers dedicado seria o proximo passo.
"""

from __future__ import annotations

import asyncio
import functools
from typing import TYPE_CHECKING, Any

import structlog

from finanalytics_ai.application.services.backtest_service import BacktestError
from finanalytics_ai.domain.backtesting.optimizer import (
    OptimizationObjective,
    OptimizationResult,
    grid_search,
)
from finanalytics_ai.domain.value_objects.money import Ticker

if TYPE_CHECKING:
    from finanalytics_ai.domain.ports.market_data import MarketDataProvider

logger = structlog.get_logger(__name__)


class OptimizerService:
    """
    Servico de otimizacao de parametros.

    Injecao de dependencia: recebe BrapiClient no construtor
    (mesmo padrao do BacktestService).
    """

    def __init__(self, market_data: MarketDataProvider) -> None:
        self._market = market_data

    async def optimize(
        self,
        ticker: str,
        strategy_name: str,
        range_period: str = "1y",
        initial_capital: float = 10_000.0,
        position_size: float = 1.0,
        commission_pct: float = 0.001,
        objective: str = "sharpe",
        top_n: int = 10,
        custom_space: dict[str, list[Any]] | None = None,
    ) -> OptimizationResult:
        """
        Executa grid search otimizando os parametros da estrategia.

        Fluxo:
          1. Valida objetivo
          2. Busca barras OHLC (1 chamada API)
          3. Valida quantidade minima de barras
          4. Executa grid_search em thread separada
          5. Loga resultado e retorna

        Parametros:
          objective:    "sharpe" | "return" | "calmar" | "win_rate" | "profit_factor"
          top_n:        Numero de melhores resultados a retornar (max 20)
          custom_space: Espaco de parametros customizado (opcional)
        """
        log = logger.bind(ticker=ticker, strategy=strategy_name, range=range_period, objective=objective)

        # Valida objetivo
        try:
            obj = OptimizationObjective(objective)
        except ValueError:
            raise BacktestError(
                f"Objetivo '{objective}' invalido. Opcoes: {[o.value for o in OptimizationObjective]}"
            ) from None

        top_n = min(top_n, 20)  # limite de seguranca

        log.info("optimizer.starting")

        # Busca dados OHLC uma unica vez
        bars = await self._market.get_ohlc_bars(Ticker(ticker), range_period=range_period)
        if not bars:
            raise BacktestError(f"Sem dados historicos para {ticker} no periodo {range_period}.")

        if len(bars) < 50:
            raise BacktestError(
                f"Dados insuficientes: {len(bars)} barras para {ticker}. "
                "Otimizacao requer pelo menos 50 barras (use 6mo ou mais)."
            )

        log.info("optimizer.data_loaded", bars=len(bars))

        # Executa grid_search em thread (CPU-bound)
        fn = functools.partial(
            grid_search,
            bars=bars,
            strategy_name=strategy_name,
            ticker=ticker,
            range_period=range_period,
            initial_capital=initial_capital,
            position_size=position_size,
            commission_pct=commission_pct,
            objective=obj,
            top_n=top_n,
            custom_space=custom_space,
        )

        try:
            result = await asyncio.to_thread(fn)
        except ValueError as exc:
            raise BacktestError(str(exc)) from exc

        log.info(
            "optimizer.done",
            total_runs=result.total_runs,
            valid_runs=result.valid_runs,
            best_score=result.best_score,
            best_params=result.best_params,
        )

        return result
