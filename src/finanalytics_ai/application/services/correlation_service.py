"""
CorrelationService — busca barras OHLC de N tickers e calcula correlacao.

Design decisions:

  Mesmo padrao do MultiTickerService:
    asyncio.gather + Semaphore(3) para nao estourar rate limit da BRAPI.
    Falhas por ticker capturadas individualmente — partial success.

  Rolling window adaptativo:
    Se common_bars < rolling_window * 2, o window e reduzido automaticamente
    para max(10, common_bars // 3). Isso evita graficos de correlacao rolante
    vazios quando o periodo selecionado e curto.

  Ticker como str aqui:
    BrapiClient.get_ohlc_bars aceita Ticker (value object), mas o servico
    recebe strings crus do usuario. A conversao str -> Ticker e feita aqui,
    garantindo validacao antes da chamada HTTP.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from finanalytics_ai.application.services.backtest_service import BacktestError
from finanalytics_ai.domain.correlation.engine import (
    CorrelationResult,
    build_correlation_result,
)
from finanalytics_ai.domain.value_objects.money import Ticker

if TYPE_CHECKING:
    from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient

logger = structlog.get_logger(__name__)

MAX_TICKERS = 10
MAX_CONCURRENT = 3
MIN_BARS = 10


class CorrelationService:
    def __init__(self, brapi_client: BrapiClient) -> None:
        self._brapi = brapi_client

    async def compute(
        self,
        tickers: list[str],
        range_period: str = "1y",
        rolling_window: int = 30,
    ) -> CorrelationResult:
        """
        Busca OHLC de N tickers e calcula correlacao.

        Parametros:
          tickers:        Lista de tickers (max MAX_TICKERS)
          range_period:   Periodo historico (6mo, 1y, 2y, 5y)
          rolling_window: Janela para correlacao rolante (dias)

        Retorna CorrelationResult com matriz, pares e rolling por par.
        """
        # Valida e normaliza
        tickers = [t.upper().strip() for t in tickers if t.strip()]
        if not tickers:
            raise BacktestError("Informe pelo menos 2 tickers.")
        if len(tickers) < 2:
            raise BacktestError("Correlacao requer pelo menos 2 tickers.")
        if len(tickers) > MAX_TICKERS:
            raise BacktestError(
                f"Maximo de {MAX_TICKERS} tickers por analise. Recebidos: {len(tickers)}."
            )

        log = logger.bind(tickers=tickers, range=range_period, window=rolling_window)
        log.info("correlation.starting")

        sem = asyncio.Semaphore(MAX_CONCURRENT)

        async def _fetch(ticker: str) -> tuple[str, list[dict] | Exception]:
            async with sem:
                try:
                    bars = await self._brapi.get_ohlc_bars(
                        Ticker(ticker), range_period=range_period
                    )
                    if len(bars) < MIN_BARS:
                        raise BacktestError(
                            f"Dados insuficientes: {len(bars)} barras (minimo {MIN_BARS})."
                        )
                    return ticker, bars
                except Exception as exc:
                    log.warning("correlation.ticker_failed", ticker=ticker, error=str(exc))
                    return ticker, exc

        raw = await asyncio.gather(*[_fetch(t) for t in tickers])

        bars_map: dict[str, list[dict]] = {}
        errors: list[dict[str, str]] = []
        for ticker, result in raw:
            if isinstance(result, Exception):
                errors.append({"ticker": ticker, "error": str(result)})
            else:
                bars_map[ticker] = result

        if len(bars_map) < 2:
            raise BacktestError(
                f"Dados validos para apenas {len(bars_map)} ticker(s). "
                "Correlacao requer pelo menos 2."
            )

        # Adapta rolling window se dados sao curtos
        # Estimativa conservadora: cada ticker tem ~common_bars dias
        # Usamos min bars entre os tickers como proxy
        min_bars = min(len(b) for b in bars_map.values())
        effective_window = rolling_window
        if min_bars < rolling_window * 2:
            effective_window = max(10, min_bars // 3)
            log.info(
                "correlation.window_adapted",
                original=rolling_window,
                effective=effective_window,
                min_bars=min_bars,
            )

        result = build_correlation_result(
            bars_map=bars_map,
            range_period=range_period,
            rolling_window=effective_window,
            errors=errors,
        )

        log.info(
            "correlation.done",
            tickers_ok=len(bars_map),
            tickers_failed=len(errors),
            common_bars=result.common_bars,
            diversification=result.diversification_score,
        )

        return result
