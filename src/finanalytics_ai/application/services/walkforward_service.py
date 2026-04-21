"""
WalkForwardService - camada de aplicacao para walk-forward validation.

Mesmo padrao do OptimizerService:
  - Busca barras OHLC uma vez
  - Executa walk_forward em asyncio.to_thread (CPU-bound)
  - Valida parametros de entrada

Requisito minimo de dados por periodo:
  Cada fold IS precisa de pelo menos 30 barras.
  Com n_splits=3 e oos_pct=0.3 e 120 barras:
    fold_size = 40, oos_size = 12, is_size = 28 -> valido
  Com n_splits=5 e 60 barras:
    fold_size = 12, oos_size = 4, is_size = 8 -> muito pequeno -> erro
"""

from __future__ import annotations

import asyncio
import functools
from typing import TYPE_CHECKING, Any

import structlog

from finanalytics_ai.application.services.backtest_service import BacktestError
from finanalytics_ai.domain.backtesting.optimizer import (
    OptimizationObjective,
    WalkForwardResult,
    walk_forward,
)
from finanalytics_ai.domain.value_objects.money import Ticker

if TYPE_CHECKING:
    from finanalytics_ai.domain.ports.market_data import MarketDataProvider

logger = structlog.get_logger(__name__)


class WalkForwardService:
    def __init__(self, market_data: MarketDataProvider) -> None:
        self._market = market_data

    async def run(
        self,
        ticker: str,
        strategy_name: str,
        range_period: str = "2y",
        initial_capital: float = 10_000.0,
        position_size: float = 1.0,
        commission_pct: float = 0.001,
        objective: str = "sharpe",
        n_splits: int = 3,
        oos_pct: float = 0.3,
        anchored: bool = False,
        custom_space: dict[str, list[Any]] | None = None,
    ) -> WalkForwardResult:

        log = logger.bind(
            ticker=ticker, strategy=strategy_name, n_splits=n_splits, objective=objective
        )

        try:
            obj = OptimizationObjective(objective)
        except ValueError:
            raise BacktestError(
                f"Objetivo '{objective}' invalido. Opcoes: {[o.value for o in OptimizationObjective]}"
            ) from None

        n_splits = max(2, min(6, n_splits))

        log.info("walkforward.starting")

        bars = await self._market.get_ohlc_bars(Ticker(ticker), range_period=range_period)
        if not bars:
            raise BacktestError(f"Sem dados historicos para {ticker} no periodo {range_period}.")

        min_bars = n_splits * 40  # ~40 barras por fold minimo
        if len(bars) < min_bars:
            raise BacktestError(
                f"Dados insuficientes: {len(bars)} barras para {n_splits} splits. "
                f"Minimo recomendado: {min_bars} barras. Use 1y ou 2y."
            )

        log.info("walkforward.data_loaded", bars=len(bars))

        fn = functools.partial(
            walk_forward,
            bars=bars,
            strategy_name=strategy_name,
            ticker=ticker,
            range_period=range_period,
            initial_capital=initial_capital,
            position_size=position_size,
            commission_pct=commission_pct,
            objective=obj,
            n_splits=n_splits,
            oos_pct=oos_pct,
            anchored=anchored,
            custom_space=custom_space,
        )

        try:
            result = await asyncio.to_thread(fn)
        except ValueError as exc:
            raise BacktestError(str(exc)) from exc

        log.info(
            "walkforward.done",
            folds=len(result.folds),
            avg_oos_score=result.avg_oos_score,
            efficiency=result.efficiency_ratio,
            consistency=result.consistency,
            combined_return=result.combined_return,
        )

        return result
