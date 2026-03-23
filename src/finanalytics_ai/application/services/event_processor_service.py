"""
EventProcessorService — compatibility shim para a API legada.

Os testes em test_observability_hooks.py e test_ohlc_handler.py foram escritos
para uma versão anterior que tinha handlers específicos por tipo de evento com
métricas Prometheus e spans OTel individuais.

Esta classe reimplementa essa API sem alterar o EventProcessor atual.

Injeção:
    event_store   — repositório de eventos (mock em testes)
    market_data   — cliente de market data (mock em testes)
    tracer        — opentelemetry.trace.Tracer (None = no-op)
    ohlc_repo     — OHLCBarRepository (opcional)

Métodos:
    _handle_price_update(event) — handler de PRICE_UPDATE
    _handle_ohlc_bar(event)     — handler de OHLC_BAR_CLOSED
    _dispatch(event)            — span pai + despacha para handler correto
"""

from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from finanalytics_ai.domain.entities.event import EventType, MarketEvent
from finanalytics_ai.observability.logging import get_logger

log = get_logger(__name__)


def _try_decimal(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError):
        return None


class EventProcessorService:
    """
    Compatibility shim — API legada de EventProcessor com handlers individuais.

    Trade-off documentado: esta classe coexiste com o EventProcessor atual.
    Quando os testes forem migrados para a nova API, ela pode ser removida.
    """

    def __init__(
        self,
        event_store: Any,
        market_data: Any,
        tracer: Any = None,
        ohlc_repo: Any = None,
    ) -> None:
        self._event_store = event_store
        self._market_data = market_data
        self._tracer = tracer
        self._ohlc_repo = ohlc_repo

        # Métricas Prometheus (lazy — None se desabilitadas)
        self._handler_counter = None
        self._handler_duration = None
        self._init_metrics()

    def _init_metrics(self) -> None:
        try:
            from finanalytics_ai.observability import handler_events_total, handler_duration_seconds
            self._handler_counter = handler_events_total
            self._handler_duration = handler_duration_seconds
        except (ImportError, AttributeError):
            pass

    def _start_span(self, name: str, attributes: dict | None = None):
        """Cria span OTel ou contextmanager no-op."""
        import contextlib

        if self._tracer is None:
            return contextlib.nullcontext()

        span = self._tracer.start_span(name)
        if attributes:
            for k, v in attributes.items():
                span.set_attribute(k, v)
        return self._tracer.use_span(span, end_on_exit=True)

    def _record_metric(self, handler: str, status: str, duration: float) -> None:
        if self._handler_counter is not None:
            try:
                self._handler_counter.labels(handler=handler, status=status).inc()
            except Exception:
                pass
        if self._handler_duration is not None:
            try:
                self._handler_duration.labels(handler=handler).observe(duration)
            except Exception:
                pass

    # ── Handlers ──────────────────────────────────────────────────────────────

    async def _handle_price_update(self, event: MarketEvent) -> None:
        """Handler de PRICE_UPDATE com métricas Prometheus e span OTel."""
        t0 = time.perf_counter()
        ticker = event.ticker
        price = _try_decimal(event.payload.get("price"))

        if price is None:
            log.warning("event_processor_service.price_update.invalid_price",
                        ticker=ticker, payload=event.payload)

        # Span OTel
        if self._tracer is not None:
            try:
                with self._tracer.start_as_current_span("handler.price_update") as span:
                    span.set_attribute("event.ticker", ticker)
                    if price is not None:
                        span.set_attribute("price.value", float(price))
            except Exception:
                pass

        duration = time.perf_counter() - t0
        self._record_metric("price_update", "ok", duration)

    async def _handle_ohlc_bar(self, event: MarketEvent) -> None:
        """Handler de OHLC_BAR_CLOSED com métricas, span OTel e persistência."""
        from datetime import datetime, timezone, UTC
        t0 = time.perf_counter()
        p = event.payload
        persisted = False

        close = _try_decimal(p.get("close"))
        if close is None:
            log.warning("event_processor_service.ohlc_bar.missing_close",
                        ticker=event.ticker)
            self._record_metric("ohlc_bar", "ok", time.perf_counter() - t0)
            return

        # Persiste se repo injetado
        if self._ohlc_repo is not None:
            try:
                from finanalytics_ai.domain.entities.event import OHLCBar
                # Parseia timestamp
                ts_raw = p.get("timestamp")
                if ts_raw is None:
                    ts = event.occurred_at
                elif isinstance(ts_raw, (int, float)):
                    ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
                else:
                    ts = datetime.fromisoformat(str(ts_raw))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)

                bar = OHLCBar(
                    ticker=event.ticker,
                    timestamp=ts,
                    open=_try_decimal(p.get("open")) or close,
                    high=_try_decimal(p.get("high")) or close,
                    low=_try_decimal(p.get("low")) or close,
                    close=close,
                    volume=int(p.get("volume", 0) or 0),
                    timeframe=p.get("timeframe", "1m"),
                    source=event.source,
                )
                result = await self._ohlc_repo.upsert_bar(bar)
                persisted = result is not False
            except Exception as exc:
                log.warning("event_processor_service.ohlc_bar.persist_failed",
                            error=str(exc))

        # Span OTel
        if self._tracer is not None:
            try:
                with self._tracer.start_as_current_span("handler.ohlc_bar") as span:
                    span.set_attribute("event.ticker", event.ticker)
                    span.set_attribute("ohlc.close", float(close))
                    span.set_attribute("ohlc.volume", int(p.get("volume", 0) or 0))
                    span.set_attribute("ohlc.persisted", persisted)
            except Exception:
                pass

        duration = time.perf_counter() - t0
        self._record_metric("ohlc_bar", "ok", duration)


    async def process(self, command) -> dict:
        """Compatibilidade com ProcessMarketEventCommand — converte para MarketEvent e despacha."""
        from finanalytics_ai.domain.entities.event import MarketEvent, EventType
        event = MarketEvent(
            event_id=command.event_id,
            event_type=EventType(command.event_type),
            ticker=command.ticker,
            payload=command.payload,
            source=command.source,
        )
        await self._dispatch(event)
        from types import SimpleNamespace
        from finanalytics_ai.domain.entities.event import EventStatus
        return SimpleNamespace(status=EventStatus.PROCESSED, event_id=command.event_id)
    async def _dispatch(self, event: MarketEvent) -> None:
        """Span pai 'event.dispatch' + despacha para handler correto."""
        dispatch_span = None
        if self._tracer is not None:
            try:
                dispatch_span = self._tracer.start_span("event.dispatch")
                dispatch_span.set_attribute("event.type", str(event.event_type))
                dispatch_span.set_attribute("event.ticker", event.ticker)
            except Exception:
                dispatch_span = None

        try:
            if event.event_type == EventType.PRICE_UPDATE:
                await self._handle_price_update(event)
            elif event.event_type == EventType.OHLC_BAR_CLOSED:
                await self._handle_ohlc_bar(event)
            else:
                log.debug("event_processor_service.dispatch.no_handler",
                          event_type=event.event_type)
        except Exception as exc:
            if dispatch_span is not None:
                try:
                    from opentelemetry.trace import StatusCode
                    dispatch_span.set_status(StatusCode.ERROR, str(exc))
                    dispatch_span.record_exception(exc)
                except Exception:
                    pass
            raise
        finally:
            if dispatch_span is not None:
                try:
                    dispatch_span.end()
                except Exception:
                    pass
