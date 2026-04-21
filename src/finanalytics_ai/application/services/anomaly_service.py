"""
AnomalyService — busca barras OHLCV de N tickers e executa detectores.

Design decisions:

  asyncio.to_thread para o calculo:
    Os 4 detectores sao CPU-bound puros (loops Python).
    Para N <= 20 tickers e 100 barras, o tempo e < 5ms no total,
    entao to_thread e conservador mas correto — mantem o event loop
    livre e e facilmente removivel se a performance for suficiente.

  Mesmo Semaphore(3) dos outros services:
    Consistencia arquitetural e protecao contra rate limit da BRAPI.

  Integracao com AlertEventBus (opcional):
    Se o bus estiver disponivel no app.state, anomalias HIGH sao
    publicadas como AlertTriggerResult no mesmo canal SSE dos alertas
    manuais. Isso reutiliza o frontend existente sem nova infra.
    O acoplamento e unidirecional: AnomalyService -> AlertEventBus,
    nunca o contrario.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from finanalytics_ai.domain.anomaly.engine import (
    AnomalyResult,
    DetectorConfig,
    MultiAnomalyResult,
    build_multi_anomaly_result,
)
from finanalytics_ai.domain.value_objects.money import Ticker

if TYPE_CHECKING:
    from finanalytics_ai.domain.ports.market_data import MarketDataProvider

logger = structlog.get_logger(__name__)

MAX_TICKERS = 20
MAX_CONCURRENT = 3


class AnomalyService:
    def __init__(self, market_data: MarketDataProvider) -> None:
        self._market = market_data

    async def scan(
        self,
        tickers: list[str],
        range_period: str = "3mo",
        config: DetectorConfig | None = None,
    ) -> MultiAnomalyResult:
        """
        Busca barras e detecta anomalias para N tickers.

        Parametros:
          tickers:      Lista de tickers (max MAX_TICKERS)
          range_period: Periodo historico para contexto (3mo, 6mo, 1y)
          config:       Parametros dos detectores (None = defaults)

        Retorna MultiAnomalyResult ordenado por severidade.
        """
        tickers = [t.upper().strip() for t in tickers if t.strip()][:MAX_TICKERS]
        if not tickers:
            raise ValueError("Informe pelo menos 1 ticker.")

        cfg = config or DetectorConfig()
        log = logger.bind(tickers=tickers, range=range_period)
        log.info("anomaly.scan.starting")

        sem = asyncio.Semaphore(MAX_CONCURRENT)

        async def _fetch(ticker: str) -> tuple[str, list[dict] | Exception]:
            async with sem:
                try:
                    bars = await self._market.get_ohlc_bars(
                        Ticker(ticker), range_period=range_period
                    )
                    return ticker, bars
                except Exception as exc:
                    log.warning("anomaly.fetch_failed", ticker=ticker, error=str(exc))
                    return ticker, exc

        raw = await asyncio.gather(*[_fetch(t) for t in tickers])

        ticker_bars: dict[str, list[dict]] = {}
        for t_name, fetch_result in raw:
            if isinstance(fetch_result, Exception):
                ticker_bars[t_name] = []  # analise retornara error
            else:
                ticker_bars[t_name] = fetch_result

        # CPU-bound: roda em thread para nao bloquear event loop
        result: MultiAnomalyResult = await asyncio.to_thread(
            build_multi_anomaly_result,
            ticker_bars,
            range_period,
            cfg,
        )

        log.info(
            "anomaly.scan.done",
            total=result.total_tickers,
            with_anomalies=result.tickers_with_anomalies,
            high_severity=result.high_severity_count,
        )

        # Métricas Prometheus
        try:
            from finanalytics_ai.metrics import record_anomaly_scan

            record_anomaly_scan(
                tickers_count=len(tickers),
                range_period=range_period,
                results=[r.to_dict() for r in result.results],
            )
        except Exception:
            pass  # metrics nunca quebram o fluxo principal

        return result

    async def scan_single(
        self,
        ticker: str,
        range_period: str = "3mo",
        config: DetectorConfig | None = None,
    ) -> AnomalyResult:
        """Escaneia um unico ticker. Convenience wrapper."""
        result = await self.scan([ticker], range_period, config)
        return (
            result.results[0]
            if result.results
            else AnomalyResult(ticker=ticker, bars_analyzed=0, anomalies=[], error="Sem resultado")
        )

    async def scan_bars(
        self,
        ticker_bars: dict,
    ) -> object:
        """
        Detecta anomalias a partir de barras pre-carregadas.
        Usado pelo TickAnomalyBridge (dados do ProfitDLL + Fintz).

        ticker_bars: {ticker: [{"time": int, "open": float, ...}]}
        """
        import asyncio

        from finanalytics_ai.domain.anomaly.engine import (
            DetectorConfig,
            build_multi_anomaly_result,
        )

        config = DetectorConfig()
        ticker_results = {}

        async def _detect(ticker: str, bars: list) -> None:
            try:
                result = await asyncio.to_thread(self._run_detectors, ticker, bars, config)
                ticker_results[ticker] = result
            except Exception as exc:
                logger.warning("anomaly.scan_bars.failed", ticker=ticker, error=str(exc))

        await asyncio.gather(*[_detect(t, b) for t, b in ticker_bars.items()])
        return build_multi_anomaly_result(ticker_results)

    def _run_detectors(
        self,
        ticker: str,
        bars: list,
        config: object,
    ) -> object:
        """Executa os 4 detectores de anomalia em uma serie de barras."""
        from finanalytics_ai.domain.anomaly.engine import detect_anomalies

        return detect_anomalies(ticker, bars, config)
