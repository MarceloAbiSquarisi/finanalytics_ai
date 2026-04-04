"""
finanalytics_ai.infrastructure.market_data.tick_anomaly_bridge
---------------------------------------------------------------
Bridge entre ProfitDLL (ticks em tempo real) e AnomalyService.

Fluxo:
    ProfitDLL tick → TickAnomalyBridge → AnomalyService.scan()
                                       → NotificationBus (SSE)

Como funciona:
    1. Cada tick e acumulado em uma janela OHLCV de 1 minuto por ticker
    2. A cada EVAL_INTERVAL segundos, consolida as janelas em barras
    3. Combina com historico do Fintz (fintz_cotacoes) para serie longa
    4. Roda AnomalyService.scan() nos tickers ativos
    5. Anomalias HIGH/MEDIUM publicadas no NotificationBus

Design decisions:
    - Janela de 1 minuto: suficiente para detectar spikes intraday
    - Serie minima: 30 barras (historico + ticks recentes)
    - Sem persistencia das janelas: dados em memoria, stateless
    - Rodar a cada 60s: balanco entre latencia e carga de CPU
    - Desacoplado do EventProcessorService: bridge separado, sem acoplamento
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Intervalo entre avaliacoes de anomalia (segundos)
EVAL_INTERVAL = 60

# Minimo de barras para rodar deteccao
MIN_BARS = 30

# Historico de janelas por ticker (max ultimas N)
MAX_WINDOWS = 120  # 2 horas de janelas de 1 minuto


class OHLCVWindow:
    """Janela OHLCV de 1 minuto para um ticker."""

    def __init__(self, ticker: str, timestamp: datetime) -> None:
        self.ticker = ticker
        self.timestamp = timestamp
        self.open: float | None = None
        self.high: float | None = None
        self.low: float | None = None
        self.close: float | None = None
        self.volume: float = 0.0
        self.tick_count: int = 0

    def update(self, price: float, volume: float = 0.0) -> None:
        if self.open is None:
            self.open = price
        self.high = max(self.high or price, price)
        self.low = min(self.low or price, price)
        self.close = price
        self.volume += volume
        self.tick_count += 1

    def to_bar(self) -> dict[str, Any] | None:
        """Converte para formato de barra OHLCV."""
        if self.close is None:
            return None
        o = self.open or self.close
        return {
            "time":   int(self.timestamp.timestamp()),
            "open":   o,
            "high":   self.high or self.close,
            "low":    self.low or self.close,
            "close":  self.close,
            "volume": self.volume,
        }


class TickAnomalyBridge:
    """
    Acumula ticks do ProfitDLL e detecta anomalias periodicamente.

    Uso:
        bridge = TickAnomalyBridge(anomaly_service, market_client, notification_bus)
        await bridge.start()

        # A cada tick do ProfitDLL:
        await bridge.on_tick(ticker, price, volume, timestamp)

        # Para:
        await bridge.stop()
    """

    def __init__(
        self,
        anomaly_service: Any,
        market_client: Any,
        notification_bus: Any | None = None,
    ) -> None:
        self._anomaly = anomaly_service
        self._market = market_client
        self._bus = notification_bus

        # Estado: janela atual por ticker
        self._current_window: dict[str, OHLCVWindow] = {}
        # Historico de janelas completadas por ticker
        self._windows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        # Historico Fintz por ticker (carregado uma vez)
        self._fintz_history: dict[str, list[dict[str, Any]]] = {}

        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Inicia o loop de avaliacao periodica."""
        self._running = True
        self._task = asyncio.create_task(self._eval_loop())
        logger.info("tick_anomaly_bridge.started", interval=EVAL_INTERVAL)

    async def stop(self) -> None:
        """Para o loop de avaliacao."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("tick_anomaly_bridge.stopped")

    async def on_tick(
        self,
        ticker: str,
        price: float,
        volume: float = 0.0,
        timestamp: datetime | None = None,
    ) -> None:
        """
        Processa um tick recebido do ProfitDLL.

        Acumula na janela atual de 1 minuto.
        Quando a janela expira, consolida e inicia nova janela.
        """
        if not price or price <= 0:
            return

        now = timestamp or datetime.now(timezone.utc)
        # Chave de minuto (trunca segundos)
        window_key = now.replace(second=0, microsecond=0)

        current = self._current_window.get(ticker)

        if current is None or current.timestamp != window_key:
            # Consolida janela anterior
            if current is not None:
                bar = current.to_bar()
                if bar:
                    self._windows[ticker].append(bar)
                    # Limita historico em memoria
                    if len(self._windows[ticker]) > MAX_WINDOWS:
                        self._windows[ticker] = self._windows[ticker][-MAX_WINDOWS:]
            # Inicia nova janela
            self._current_window[ticker] = OHLCVWindow(ticker, window_key)

        self._current_window[ticker].update(price, volume)

    async def _eval_loop(self) -> None:
        """Loop que avalia anomalias a cada EVAL_INTERVAL segundos."""
        while self._running:
            await asyncio.sleep(EVAL_INTERVAL)
            try:
                await self._evaluate()
            except Exception as exc:
                logger.error("tick_anomaly_bridge.eval_error", error=str(exc))

    async def _evaluate(self) -> None:
        """Executa deteccao de anomalias em todos os tickers ativos."""
        active_tickers = list(self._current_window.keys())
        if not active_tickers:
            return

        log = logger.bind(tickers=active_tickers)
        log.info("tick_anomaly_bridge.evaluating")

        # Monta series combinadas (historico Fintz + janelas recentes)
        ticker_bars: dict[str, list[dict[str, Any]]] = {}

        for ticker in active_tickers:
            # Carrega historico Fintz (cache em memoria)
            if ticker not in self._fintz_history:
                await self._load_fintz_history(ticker)

            fintz = self._fintz_history.get(ticker, [])
            recent = self._windows.get(ticker, [])

            # Adiciona janela atual (nao completa)
            current = self._current_window.get(ticker)
            if current:
                bar = current.to_bar()
                if bar:
                    recent = recent + [bar]

            # Combina: historico + recente (mais recente tem prioridade)
            combined = _merge_bars(fintz, recent)

            if len(combined) >= MIN_BARS:
                ticker_bars[ticker] = combined
            else:
                log.warning(
                    "tick_anomaly_bridge.insufficient_bars",
                    ticker=ticker,
                    bars=len(combined),
                )

        if not ticker_bars:
            return

        # Roda AnomalyService com as series combinadas
        try:
            # AnomalyService.scan_bars() aceita dict pre-carregado
            # Se nao existir, usa scan() normal (busca do banco)
            if hasattr(self._anomaly, "scan_bars"):
                result = await self._anomaly.scan_bars(ticker_bars)
            else:
                result = await self._anomaly.scan(
                    tickers=list(ticker_bars.keys()),
                    range_period="3mo",
                )

            await self._notify_anomalies(result)

        except Exception as exc:
            log.error("tick_anomaly_bridge.scan_error", error=str(exc))

    async def _load_fintz_history(self, ticker: str) -> None:
        """Carrega historico de 3 meses do Fintz para um ticker."""
        try:
            from finanalytics_ai.domain.value_objects.money import Ticker as TickerVO
            bars = await self._market.get_ohlc_bars(
                TickerVO(ticker), range_period="3mo"
            )
            self._fintz_history[ticker] = bars
            logger.info(
                "tick_anomaly_bridge.history_loaded",
                ticker=ticker,
                bars=len(bars),
            )
        except Exception as exc:
            logger.warning(
                "tick_anomaly_bridge.history_failed",
                ticker=ticker,
                error=str(exc),
            )
            self._fintz_history[ticker] = []

    async def _notify_anomalies(self, result: Any) -> None:
        """Publica anomalias HIGH/MEDIUM no NotificationBus."""
        if self._bus is None:
            return

        try:
            from finanalytics_ai.infrastructure.notifications import AlertNotification

            # result pode ser MultiAnomalyResult ou similar
            anomalies = getattr(result, "anomalies", [])
            for anomaly in anomalies:
                severity = getattr(anomaly, "severity", "LOW")
                if severity not in ("HIGH", "MEDIUM"):
                    continue

                ticker = getattr(anomaly, "ticker", "")
                detector = getattr(anomaly, "detector", "")
                value = getattr(anomaly, "value", 0.0)
                threshold = getattr(anomaly, "threshold", 0.0)
                description = getattr(anomaly, "description", "")

                notification = AlertNotification(
                    alert_id=f"anomaly_{ticker}_{detector}_{int(datetime.now().timestamp())}",
                    ticker=ticker,
                    alert_type=f"anomaly_{severity.lower()}",
                    message=f"[{severity}] {ticker}: {description}",
                    current_price=float(value),
                    threshold=float(threshold),
                    user_id="system",
                    triggered_at=datetime.now(timezone.utc).isoformat(),
                    context={
                        "detector": detector,
                        "severity": severity,
                        "source": "profit_dll_realtime",
                    },
                )
                await self._bus.broadcast(notification)
                logger.info(
                    "tick_anomaly_bridge.anomaly_notified",
                    ticker=ticker,
                    severity=severity,
                    detector=detector,
                )

        except Exception as exc:
            logger.warning("tick_anomaly_bridge.notify_error", error=str(exc))


def _merge_bars(
    historical: list[dict[str, Any]],
    recent: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Mescla barras historicas com barras recentes.
    Recentes tem prioridade (sobrescrevem por timestamp).
    Resultado ordenado por timestamp ASC.
    """
    seen: dict[int, dict[str, Any]] = {}
    for bar in historical:
        seen[bar.get("time", 0)] = bar
    for bar in recent:
        seen[bar.get("time", 0)] = bar
    return sorted(seen.values(), key=lambda b: b.get("time", 0))
