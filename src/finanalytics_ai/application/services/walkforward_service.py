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
    from finanalytics_ai.infrastructure.database.repositories.backtest_repo import (
        BacktestResultRepository,
    )

logger = structlog.get_logger(__name__)


class WalkForwardService:
    def __init__(
        self,
        market_data: MarketDataProvider,
        result_repo: BacktestResultRepository | None = None,
    ) -> None:
        self._market = market_data
        self._result_repo = result_repo

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

        # R5: persistencia idempotente. Strategy fica `wf:<name>` para nao
        # colidir com runs grid_search no historico. Metricas agregadas vem
        # dos folds OOS (a parte "honesta" do walk-forward).
        if self._result_repo is not None and result.folds:
            try:
                await self._persist(
                    result=result,
                    ticker=ticker,
                    strategy_name=strategy_name,
                    range_period=range_period,
                    initial_capital=initial_capital,
                    objective=objective,
                    n_splits=n_splits,
                    oos_pct=oos_pct,
                    anchored=anchored,
                    log=log,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("walkforward.persist_failed", error=str(exc))

        return result

    async def _persist(
        self,
        *,
        result: WalkForwardResult,
        ticker: str,
        strategy_name: str,
        range_period: str,
        initial_capital: float,
        objective: str,
        n_splits: int,
        oos_pct: float,
        anchored: bool,
        log: Any,
    ) -> None:
        """
        UPSERT em backtest_results com payload sintetico compativel com
        BacktestResultRepository.save_run (que extrai metrics de top[0]).

        Config hash inclui parametros do walk-forward (n_splits, oos_pct,
        anchored) — re-rodar com mesmo setup atualiza a mesma row.
        """
        from finanalytics_ai.infrastructure.database.repositories.backtest_repo import (
            compute_config_hash,
        )

        # Agregado OOS: media de sharpe/win_rate/profit_factor dos folds validos +
        # soma de trades + worst-case drawdown. Conservador para tomada de decisao.
        oos_folds = [f for f in result.folds if f.oos_metrics is not None]
        if not oos_folds:
            return

        total_trades = sum(f.oos_metrics.total_trades for f in oos_folds)
        avg_sharpe = sum(f.oos_metrics.sharpe_ratio for f in oos_folds) / len(oos_folds)
        max_dd = max(f.oos_metrics.max_drawdown_pct for f in oos_folds)
        avg_win_rate = sum(f.oos_metrics.win_rate_pct for f in oos_folds) / len(oos_folds)
        avg_pf = sum(f.oos_metrics.profit_factor for f in oos_folds) / len(oos_folds)

        wf_params = {
            "n_splits": n_splits,
            "oos_pct": round(oos_pct, 4),
            "anchored": bool(anchored),
        }
        config_hash = compute_config_hash(
            ticker=ticker,
            strategy=f"wf:{strategy_name}",
            range_period=range_period,
            start_date=None,
            end_date=None,
            initial_capital=initial_capital,
            objective=objective,
            slippage_applied=True,
            params=wf_params,
        )

        synthetic = {
            "ticker": ticker,
            "strategy": f"wf:{strategy_name}",
            "top": [
                {
                    "rank": 1,
                    "params": wf_params,
                    "score": result.avg_oos_score,
                    "is_valid": True,
                    "metrics": {
                        "total_return_pct": result.combined_return,
                        "sharpe_ratio": avg_sharpe,
                        "max_drawdown_pct": max_dd,
                        "win_rate_pct": avg_win_rate,
                        "profit_factor": avg_pf,
                        "calmar_ratio": (
                            (result.combined_return / max_dd) if max_dd > 0 else 0.0
                        ),
                        "total_trades": total_trades,
                    },
                }
            ],
            "bars_count": result.total_bars,
            "best_params": wf_params,
            # DSR agregado dos OOS folds — preenche colunas escalares do
            # backtest_results via repo.save_run.
            "deflated_sharpe": result.deflated_sharpe,
            "walkforward": result.to_dict(),  # payload completo p/ drilldown
        }

        await self._result_repo.save_run(
            config_hash=config_hash,
            ticker=ticker,
            strategy=f"wf:{strategy_name}",
            full_result=synthetic,
            range_period=range_period,
            initial_capital=initial_capital,
            objective=objective,
            slippage_applied=True,
            params=wf_params,
        )
        log.info("walkforward.persisted", config_hash=config_hash[:12])
