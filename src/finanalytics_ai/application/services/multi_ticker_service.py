"""
MultiTickerService — compara a mesma estrategia em N tickers.

Design decisions:

  Paralelismo com Semaforo:
    asyncio.gather executa os N tickers de forma concorrente.
    Um asyncio.Semaphore(MAX_CONCURRENT) limita requests simultaneas
    a BRAPI para evitar rate-limiting (BRAPI tem limite ~5 req/s no plano free).
    MAX_CONCURRENT = 3 e conservador mas seguro.

  Falhas nao propagam:
    Se um ticker falha (BRAPI indisponivel, ticker invalido, dados insuficientes),
    o erro e capturado e incluido em MultiTickerResult.errors.
    Os demais tickers continuam sendo processados normalmente.
    Principio: partial success > all-or-nothing para comparativos.

  Otimizacao vs backtest simples:
    O servico usa OptimizerService.optimize() para cada ticker.
    Isso roda o grid search e retorna o melhor conjunto de parametros.
    Alternativa seria usar BacktestService.run() com params fixos —
    mais rapido mas sem a otimizacao por ticker.
    Escolhemos otimizar para que o comparativo seja justo:
    cada ativo usa seus melhores parametros, nao um conjunto generico.

  Limite de tickers:
    MAX_TICKERS = 10 aplicado aqui e na rota.
    Com MAX_CONCURRENT=3 e ~2s por ticker, 10 tickers = ~7s de resposta.
    Aceitavel para um endpoint analitico nao critico.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from finanalytics_ai.application.services.backtest_service import BacktestError
from finanalytics_ai.application.services.optimizer_service import OptimizerService
from finanalytics_ai.domain.backtesting.multi_ticker import (
    MAX_TICKERS,
    MultiTickerResult,
    build_multi_ticker_result,
)
from finanalytics_ai.domain.backtesting.optimizer import OptimizationObjective, OptimizationResult

if TYPE_CHECKING:
    from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient

logger = structlog.get_logger(__name__)

MAX_CONCURRENT = 3  # requests simultaneas a BRAPI


class MultiTickerService:
    def __init__(self, brapi_client: BrapiClient) -> None:
        self._brapi = brapi_client
        self._optimizer = OptimizerService(brapi_client)

    async def compare(
        self,
        tickers: list[str],
        strategy_name: str,
        range_period: str = "1y",
        initial_capital: float = 10_000.0,
        position_size: float = 1.0,
        commission_pct: float = 0.001,
        objective: str = "sharpe",
        top_n: int = 5,
    ) -> MultiTickerResult:
        """
        Otimiza a mesma estrategia em N tickers e retorna ranking comparativo.

        Parametros:
          tickers:  Lista de tickers (max MAX_TICKERS)
          top_n:    Melhores resultados por ticker no grid search interno

        Fluxo:
          1. Valida inputs
          2. Executa otimizacao em paralelo (semaforo MAX_CONCURRENT)
          3. Agrega resultados em MultiTickerResult
        """
        # Valida
        tickers = [t.upper().strip() for t in tickers if t.strip()]
        if not tickers:
            raise BacktestError("Informe pelo menos 1 ticker.")
        if len(tickers) > MAX_TICKERS:
            raise BacktestError(
                f"Maximo de {MAX_TICKERS} tickers por comparativo. Recebidos: {len(tickers)}."
            )

        try:
            OptimizationObjective(objective)
        except ValueError:
            raise BacktestError(
                f"Objetivo '{objective}' invalido. Opcoes: {[o.value for o in OptimizationObjective]}"
            ) from None

        log = logger.bind(
            tickers=tickers,
            strategy=strategy_name,
            range=range_period,
            objective=objective,
        )
        log.info("multi_ticker.starting")

        sem = asyncio.Semaphore(MAX_CONCURRENT)

        async def _optimize_one(ticker: str) -> tuple[str, OptimizationResult | Exception]:
            async with sem:
                try:
                    result = await self._optimizer.optimize(
                        ticker=ticker,
                        strategy_name=strategy_name,
                        range_period=range_period,
                        initial_capital=initial_capital,
                        position_size=position_size,
                        commission_pct=commission_pct,
                        objective=objective,
                        top_n=top_n,
                    )
                    return ticker, result
                except Exception as exc:
                    log.warning("multi_ticker.ticker_failed", ticker=ticker, error=str(exc))
                    return ticker, exc

        # Executa todos em paralelo
        raw = await asyncio.gather(*[_optimize_one(t) for t in tickers])

        result = build_multi_ticker_result(
            results=list(raw),
            strategy=strategy_name,
            range_period=range_period,
            objective=objective,
        )

        log.info(
            "multi_ticker.done",
            tickers_ok=len(result.rankings),
            tickers_failed=len(result.errors),
            best_ticker=result.best_ticker,
            avg_score=result.avg_score,
            hit_rate=result.hit_rate,
        )

        return result
