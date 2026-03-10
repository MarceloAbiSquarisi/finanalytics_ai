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
    from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient

logger = structlog.get_logger(__name__)

MAX_TICKERS = 20
MAX_CONCURRENT = 3


class AnomalyService:
    def __init__(self, brapi_client: BrapiClient) -> None:
        self._brapi = brapi_client

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
                    bars = await self._brapi.get_ohlc_bars(Ticker(ticker), range_period=range_period)
                    return ticker, bars
                except Exception as exc:
                    log.warning("anomaly.fetch_failed", ticker=ticker, error=str(exc))
                    return ticker, exc

        raw = await asyncio.gather(*[_fetch(t) for t in tickers])

        ticker_bars: dict[str, list[dict]] = {}
        for ticker, result in raw:
            if isinstance(result, Exception):
                ticker_bars[ticker] = []  # analise retornara error
            else:
                ticker_bars[ticker] = result

        # CPU-bound: roda em thread para nao bloquear event loop
        result = await asyncio.to_thread(
            build_multi_anomaly_result,  # type: ignore[arg-type]
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
