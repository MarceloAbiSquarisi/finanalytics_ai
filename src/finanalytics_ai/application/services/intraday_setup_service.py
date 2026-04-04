"""
finanalytics_ai.application.services.intraday_setup_service
------------------------------------------------------------
Deteccao automatica de setups intraday em tempo real.

Setups suportados (reutiliza estrategias do backtesting):
  - Setup 9.1 (Stormer): EMA9 > EMA21 + fecha acima da maxima anterior
  - Pin Bar:             Sombra longa (reversao)
  - Inside Bar:          Barra contida na anterior (compressao/explosao)
  - Gap and Go:          Gap na abertura com continuacao
  - Larry Williams:      Minima anterior em uptrend
  - Hilo Activator:      Cruzamento do indicador Hilo

Fluxo:
  1. Busca candles do periodo via MarketDataProvider (Fintz ou ProfitDLL)
  2. Para cada setup configurado, roda generate_signals()
  3. Se o ULTIMO candle tem sinal BUY/SELL -> setup ativo
  4. Publica no NotificationBus (SSE) se novo (nao repetir o mesmo setup)
  5. Retorna SetupScanResult para a API

Design:
  - Reusa estrategias existentes do backtesting (zero codigo duplicado)
  - Deteccao e sempre na ultima barra (tempo real)
  - Cache de 60s para evitar spam de alertas
  - Suporta multiplos timeframes: 5min, 15min, 60min, diario
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

from finanalytics_ai.domain.backtesting.engine import Signal
from finanalytics_ai.domain.value_objects.money import Ticker

logger = structlog.get_logger(__name__)

# Timeframes suportados -> range_period para busca de dados
_TIMEFRAME_TO_RANGE: dict[str, str] = {
    "5min":  "5d",
    "15min": "5d",
    "60min": "1mo",
    "diario": "3mo",
    "daily":  "3mo",
}

# Setups disponiveis e seus nomes amigaveis
AVAILABLE_SETUPS: dict[str, str] = {
    "setup_91":      "Setup 9.1 (Stormer)",
    "pin_bar":       "Pin Bar",
    "inside_bar":    "Inside Bar",
    "gap_and_go":    "Gap and Go",
    "larry_williams": "Larry Williams",
    "hilo_activator": "Hilo Activator",
    "macd":          "MACD Crossover",
    "rsi":           "RSI Reversal",
    "bollinger":     "Bollinger Bands",
    "ema_cross":     "EMA Cross",
}


@dataclass
class SetupAlert:
    """Um setup detectado em um ticker."""
    ticker: str
    setup_name: str
    setup_key: str
    signal: str          # "BUY" ou "SELL"
    timeframe: str
    detected_at: str
    last_close: float
    last_high: float
    last_low: float
    bar_index: int
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker":       self.ticker,
            "setup_name":   self.setup_name,
            "setup_key":    self.setup_key,
            "signal":       self.signal,
            "timeframe":    self.timeframe,
            "detected_at":  self.detected_at,
            "last_close":   self.last_close,
            "last_high":    self.last_high,
            "last_low":     self.last_low,
            "bar_index":    self.bar_index,
            **self.context,
        }


@dataclass
class SetupScanResult:
    """Resultado de um scan de setups em N tickers."""
    tickers_scanned: int
    setups_found: int
    alerts: list[SetupAlert]
    errors: list[dict[str, str]]
    scanned_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tickers_scanned": self.tickers_scanned,
            "setups_found":    self.setups_found,
            "alerts":          [a.to_dict() for a in self.alerts],
            "errors":          self.errors,
            "scanned_at":      self.scanned_at,
        }


class IntradaySetupService:
    """
    Servico de deteccao de setups intraday.

    market_data: MarketDataProvider (CompositeMarketDataClient)
    notification_bus: NotificationBus (opcional, para SSE)
    """

    def __init__(
        self,
        market_data: Any,
        notification_bus: Any | None = None,
    ) -> None:
        self._market = market_data
        self._bus = notification_bus
        # Cache: (ticker, setup_key, signal) -> ultima deteccao
        self._last_alert: dict[tuple, str] = {}

    async def scan(
        self,
        tickers: list[str],
        setups: list[str] | None = None,
        timeframe: str = "diario",
        notify: bool = True,
    ) -> SetupScanResult:
        """
        Escaneia N tickers buscando setups ativos.

        tickers:   Lista de tickers (ex: ["PETR4", "VALE3"])
        setups:    Lista de setups a verificar (None = todos)
        timeframe: "5min" | "15min" | "60min" | "diario"
        notify:    Se True, publica novos alertas no NotificationBus
        """
        if setups is None:
            setups = list(AVAILABLE_SETUPS.keys())

        # Filtra setups invalidos
        valid_setups = [s for s in setups if s in AVAILABLE_SETUPS]
        if not valid_setups:
            raise ValueError(f"Nenhum setup valido. Use: {list(AVAILABLE_SETUPS)}")

        range_period = _TIMEFRAME_TO_RANGE.get(timeframe, "3mo")
        now = datetime.now(timezone.utc).isoformat()

        log = logger.bind(tickers=tickers, setups=valid_setups, timeframe=timeframe)
        log.info("intraday_setup.scan.starting")

        alerts: list[SetupAlert] = []
        errors: list[dict[str, str]] = []

        # Busca bars em paralelo (max 5 simultaneos)
        sem = asyncio.Semaphore(5)

        async def _scan_ticker(ticker: str) -> None:
            async with sem:
                try:
                    bars = await self._market.get_ohlc_bars(
                        Ticker(ticker.upper()),
                        range_period=range_period,
                    )
                    if not bars or len(bars) < 10:
                        errors.append({
                            "ticker": ticker,
                            "error": f"Dados insuficientes: {len(bars or [])} barras",
                        })
                        return

                    ticker_alerts = _detect_setups(ticker, bars, valid_setups, timeframe, now)
                    alerts.extend(ticker_alerts)

                except Exception as exc:
                    logger.warning("intraday_setup.fetch_failed", ticker=ticker, error=str(exc))
                    errors.append({"ticker": ticker, "error": str(exc)})

        await asyncio.gather(*[_scan_ticker(t) for t in tickers])

        # Filtra alertas novos (evita repetir o mesmo setup)
        new_alerts = []
        for alert in alerts:
            key = (alert.ticker, alert.setup_key, alert.signal)
            if self._last_alert.get(key) != now[:16]:  # agrupa por minuto
                new_alerts.append(alert)
                self._last_alert[key] = now[:16]

        # Notifica via SSE
        if notify and new_alerts and self._bus:
            for alert in new_alerts:
                await self._notify(alert)

        result = SetupScanResult(
            tickers_scanned=len(tickers),
            setups_found=len(new_alerts),
            alerts=new_alerts,
            errors=errors,
            scanned_at=now,
        )

        log.info(
            "intraday_setup.scan.done",
            found=result.setups_found,
            errors=len(errors),
        )
        return result

    async def _notify(self, alert: SetupAlert) -> None:
        """Publica setup no NotificationBus (SSE)."""
        try:
            from finanalytics_ai.infrastructure.notifications import AlertNotification

            signal_emoji = "🟢" if alert.signal == "BUY" else "🔴"
            message = (
                f"{signal_emoji} [{alert.signal}] {alert.ticker} — "
                f"{alert.setup_name} ({alert.timeframe}) "
                f"| Close: R$ {alert.last_close:.2f}"
            )

            notification = AlertNotification(
                alert_id=f"setup_{alert.ticker}_{alert.setup_key}_{int(datetime.now().timestamp())}",
                ticker=alert.ticker,
                alert_type=f"setup_{alert.signal.lower()}",
                message=message,
                current_price=alert.last_close,
                threshold=0.0,
                user_id="system",
                triggered_at=alert.detected_at,
                context={
                    "setup_key":  alert.setup_key,
                    "setup_name": alert.setup_name,
                    "timeframe":  alert.timeframe,
                    "signal":     alert.signal,
                },
            )
            await self._bus.broadcast(notification)
            logger.info(
                "intraday_setup.notified",
                ticker=alert.ticker,
                setup=alert.setup_key,
                signal=alert.signal,
            )
        except Exception as exc:
            logger.warning("intraday_setup.notify_error", error=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Deteccao pura (sem I/O)
# ─────────────────────────────────────────────────────────────────────────────

def _detect_setups(
    ticker: str,
    bars: list[dict[str, Any]],
    setup_keys: list[str],
    timeframe: str,
    now: str,
) -> list[SetupAlert]:
    """
    Executa os setups sobre as barras e retorna alertas para a ultima barra.

    Reutiliza generate_signals() das estrategias de backtesting.
    So considera o ULTIMO sinal — detecta o setup mais recente.
    """
    from finanalytics_ai.domain.backtesting.strategies.technical import get_strategy

    alerts = []
    last_bar = bars[-1]

    try:
        close = float(last_bar.get("close", 0) or 0)
        high  = float(last_bar.get("high",  close) or close)
        low   = float(last_bar.get("low",   close) or close)
    except (TypeError, ValueError):
        return []

    for setup_key in setup_keys:
        try:
            strategy = get_strategy(setup_key)
            signals = strategy.generate_signals(bars)

            if not signals:
                continue

            last_signal = signals[-1]

            if last_signal == Signal.BUY:
                signal_str = "BUY"
            elif last_signal == Signal.SELL:
                signal_str = "SELL"
            else:
                continue  # HOLD - sem setup

            alerts.append(SetupAlert(
                ticker=ticker,
                setup_name=AVAILABLE_SETUPS.get(setup_key, setup_key),
                setup_key=setup_key,
                signal=signal_str,
                timeframe=timeframe,
                detected_at=now,
                last_close=close,
                last_high=high,
                last_low=low,
                bar_index=len(bars) - 1,
                context={"bars_analyzed": len(bars)},
            ))

        except Exception as exc:
            logger.debug(
                "intraday_setup.strategy_error",
                ticker=ticker,
                setup=setup_key,
                error=str(exc)[:100],
            )

    return alerts
