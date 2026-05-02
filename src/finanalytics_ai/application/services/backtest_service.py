"""
BacktestService — camada de aplicação para backtesting.

Responsabilidades:
  1. Buscar dados OHLC via BrapiClient
  2. Instanciar a estratégia correta via factory
  3. Delegar execução ao engine
  4. Retornar BacktestResult serializado

Design decisions:
  - Sem cache de resultados (dados históricos mudam pouco, mas backtests
    com parâmetros diferentes são sempre distintos — cache aqui seria
    complexo e de baixo benefício)
  - Timeout explícito de 30s na busca de dados
  - Levanta BacktestError (exceção de domínio) em vez de vazar
    exceções de infraestrutura para a camada de interface
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import structlog

from finanalytics_ai.domain.backtesting.engine import BacktestResult, run_backtest
from finanalytics_ai.domain.backtesting.strategies.technical import get_strategy
from finanalytics_ai.domain.value_objects.money import Ticker

if TYPE_CHECKING:
    from finanalytics_ai.domain.ports.market_data import MarketDataProvider
    from finanalytics_ai.infrastructure.database.repositories.delisted_tickers_repo import (
        DelistingInfo,
    )

# R5 step 2: resolver opcional de delisting info (survivorship bias)
DelistingResolver = Callable[[str], Awaitable["DelistingInfo | None"]]

logger = structlog.get_logger(__name__)


class BacktestError(Exception):
    """Erro de backtesting — encapsula falhas de dados ou configuração."""


class BacktestService:
    """
    Serviço de backtesting.

    Injeção de dependência explícita: recebe BrapiClient no construtor.
    """

    def __init__(
        self,
        market_data: MarketDataProvider,
        delisting_resolver: DelistingResolver | None = None,
    ) -> None:
        self._market = market_data
        # R5 step 2: lookup opcional de delisting info por ticker. Se None,
        # comportamento legacy (sem survivorship bias correction).
        self._delisting_resolver = delisting_resolver

    async def run(
        self,
        ticker: str,
        strategy_name: str,
        range_period: str = "3mo",
        initial_capital: float = 10_000.0,
        position_size: float = 1.0,
        commission_pct: float = 0.001,
        strategy_params: dict | None = None,
    ) -> BacktestResult:
        """
        Executa backtest completo.

        Parâmetros:
          ticker:          Ex: "PETR4"
          strategy_name:   "rsi" | "macd" | "combined"
          range_period:    "1mo" | "3mo" | "6mo" | "1y" | "2y" | "5y"
          initial_capital: Capital inicial em BRL
          position_size:   Fração do capital por trade (0.1 a 1.0)
          commission_pct:  Comissão por operação (0.001 = 0.1%)
          strategy_params: Parâmetros específicos da estratégia
        """
        log = logger.bind(ticker=ticker, strategy=strategy_name, range=range_period)

        # 1. Valida e instancia estratégia
        try:
            strategy = get_strategy(strategy_name, strategy_params)
        except ValueError as exc:
            raise BacktestError(str(exc)) from exc

        log.info("backtest.starting", strategy_params=strategy_params)

        # 2. Busca dados OHLC
        bars = await self._market.get_ohlc_bars(Ticker(ticker), range_period=range_period)
        if not bars:
            raise BacktestError(
                f"Sem dados históricos para {ticker} no período {range_period}. "
                "Verifique o ticker e o BRAPI_TOKEN."
            )

        if len(bars) < 30:
            raise BacktestError(
                f"Dados insuficientes: apenas {len(bars)} barras para {ticker}. "
                "Use um período maior (mínimo 3mo)."
            )

        log.info("backtest.data_loaded", bars=len(bars))

        # 2.5. R5 step 2 — survivorship bias: se ticker e' delisted, resolver
        # delisting_date + last_known_price p/ engine fazer force-close
        delisting_date = None
        last_known_price = None
        if self._delisting_resolver is not None:
            try:
                info = await self._delisting_resolver(ticker)
                if info is not None:
                    delisting_date = info.delisting_date
                    last_known_price = info.last_known_price
                    log.info(
                        "backtest.delisting_detected",
                        delisting_date=delisting_date.isoformat(),
                        last_known_price=last_known_price,
                        source=info.source,
                    )
            except Exception as exc:
                log.warning("backtest.delisting_resolver_failed", error=str(exc))

        # 3. Executa backtest
        result = run_backtest(
            bars=bars,
            strategy=strategy,
            ticker=ticker,
            initial_capital=initial_capital,
            position_size=position_size,
            commission_pct=commission_pct,
            range_period=range_period,
            delisting_date=delisting_date,
            last_known_price=last_known_price,
        )

        # Injeta params da estratégia no resultado
        result.params.update(getattr(strategy, "params", {}))

        log.info(
            "backtest.done",
            trades=result.metrics.total_trades,
            win_rate=result.metrics.win_rate_pct,
            total_return=result.metrics.total_return_pct,
            sharpe=result.metrics.sharpe_ratio,
        )

        return result
