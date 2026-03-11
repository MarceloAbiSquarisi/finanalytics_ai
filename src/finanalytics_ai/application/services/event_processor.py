"""
EventProcessorService — orquestra o processamento assíncrono de eventos.

Design decision: O serviço de aplicação NÃO conhece detalhes de I/O.
Ele recebe as dependências (ports) via construtor — Injeção de Dependência
manual. Isso permite testar com mocks sem nenhum framework de DI.

Idempotência: antes de processar, verifica se event_id já existe no store.
Resiliência: tenacity com retry para erros transitórios.
Logging: structlog com context binding por evento.
Observabilidade: spans OTel em cada handler + métricas Prometheus por tipo.

--- Tracer opcional ---
O tracer é injetado via construtor como `tracer: Tracer | None = None`.
Quando None, o código cria spans no-op (OTel garante isso via
trace.get_tracer("noop") quando nenhum provider está configurado).
Isso permite que testes unitários rodem sem OTel configurado e que
o serviço seja usado sem observabilidade em ambientes simplificados.
"""

from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

import structlog
from opentelemetry import trace
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from finanalytics_ai.domain.entities.event import EventStatus, EventType, MarketEvent
from finanalytics_ai.exceptions import (
    EventProcessingError,
    TransientError,
)
from finanalytics_ai.observability import (
    event_processing_duration_seconds,
    events_processed_total,
    handler_duration_seconds,
    handler_events_total,
)

if TYPE_CHECKING:
    from finanalytics_ai.application.commands.process_event import ProcessMarketEventCommand
    from finanalytics_ai.domain.ports.event_store import EventStore
    from finanalytics_ai.domain.ports.market_data import MarketDataProvider
    from finanalytics_ai.domain.ports.news_sentiment_repository import NewsSentimentRepository
    from finanalytics_ai.domain.ports.ohlc_repository import OHLCBarRepository
    from finanalytics_ai.domain.ports.sentiment_analyzer import SentimentAnalyzer

logger = structlog.get_logger(__name__)

# Tracer de fallback: no-op quando OTel não está configurado
_noop_tracer = trace.get_tracer("finanalytics_ai.noop")


class EventProcessorService:
    """
    Processa eventos de mercado com:
    - Idempotência via event_id
    - Retry com backoff exponencial para erros transitórios
    - Logging estruturado por evento
    - Métricas Prometheus por tipo e status
    - Spans OpenTelemetry por handler (quando tracer injetado)
    """

    def __init__(
        self,
        event_store: EventStore,
        market_data: MarketDataProvider,
        max_retry_attempts: int = 3,
        tracer: trace.Tracer | None = None,
        ohlc_repo: OHLCBarRepository | None = None,
        sentiment_analyzer: SentimentAnalyzer | None = None,
        news_repo: NewsSentimentRepository | None = None,
    ) -> None:
        self._store = event_store
        self._market_data = market_data
        self._max_retries = max_retry_attempts
        # Tracer opcional — usa no-op se não fornecido
        self._tracer: trace.Tracer = tracer or _noop_tracer
        # OHLCBarRepository opcional — backward compat e testes sem DB
        self._ohlc_repo = ohlc_repo
        # Sentimento — opcional; sem analisador, handler loga e segue
        self._sentiment_analyzer = sentiment_analyzer
        self._news_repo = news_repo

    async def process(self, command: ProcessMarketEventCommand) -> MarketEvent:
        """
        Ponto de entrada principal. Garante idempotência e resiliência.

        Returns: MarketEvent com status final (PROCESSED | SKIPPED | FAILED)
        Raises: EventProcessingError se esgotar as tentativas
        """
        log = logger.bind(
            event_id=command.event_id,
            event_type=command.event_type,
            ticker=command.ticker,
        )

        # ── 1. Idempotência ──────────────────────────────────────────────────
        if await self._store.exists(command.event_id):
            log.info("event.skipped.duplicate")
            events_processed_total.labels(event_type=command.event_type, status="skipped").inc()
            existing = await self._store.find_by_id(command.event_id)
            if existing is None:
                raise EventProcessingError(
                    message="Evento duplicado mas não encontrado no store",
                    context={"event_id": command.event_id},
                )
            return existing

        # ── 2. Persiste como PENDING ─────────────────────────────────────────
        event = MarketEvent(
            event_id=command.event_id,
            event_type=EventType(command.event_type),
            ticker=command.ticker,
            payload=command.payload,
            source=command.source,
        )
        await self._store.save(event)
        log.info("event.received")

        # ── 3. Processa com retry ────────────────────────────────────────────
        timer = event_processing_duration_seconds.labels(event_type=command.event_type)
        with timer.time():
            try:
                processed = await self._process_with_retry(event, log)
            except Exception as exc:
                event.mark_failed(str(exc))
                await self._store.update_status(event.event_id, EventStatus.FAILED, str(exc))
                events_processed_total.labels(event_type=command.event_type, status="failed").inc()
                log.error("event.failed", error=str(exc))
                raise EventProcessingError(
                    message=f"Falha ao processar evento {command.event_id}",
                    context={"event_id": command.event_id, "error": str(exc)},
                ) from exc

        events_processed_total.labels(event_type=command.event_type, status="processed").inc()
        log.info("event.processed")
        return processed

    async def _process_with_retry(self, event: MarketEvent, log: structlog.BoundLogger) -> MarketEvent:
        """Retry com backoff exponencial apenas para TransientError."""
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type(TransientError),
            reraise=True,
        ):
            with attempt:
                await self._store.update_status(event.event_id, EventStatus.PROCESSING)
                result = await self._dispatch(event)
                processed = result.mark_processed()
                await self._store.update_status(event.event_id, EventStatus.PROCESSED)
                return processed

        # nunca atingido — tenacity reraise=True
        raise EventProcessingError(message="Retry esgotado", context={"event_id": event.event_id})

    async def _dispatch(self, event: MarketEvent) -> MarketEvent:
        """
        Despacha para o handler específico por tipo, envolvendo em span OTel.

        O span "event.dispatch" é o pai de todos os spans de handler.
        Atributos rastreados: event_id, event_type, ticker.
        """
        with self._tracer.start_as_current_span(
            "event.dispatch",
            attributes={
                "event.id": event.event_id,
                "event.type": event.event_type.value,
                "event.ticker": event.ticker,
            },
        ) as span:
            try:
                match event.event_type:
                    case EventType.PRICE_UPDATE:
                        await self._handle_price_update(event)
                    case EventType.OHLC_BAR_CLOSED:
                        await self._handle_ohlc_bar(event)
                    case EventType.NEWS_PUBLISHED:
                        await self._handle_news(event)
                    case _:
                        logger.warning("event.unhandled", event_type=event.event_type)
                        span.set_attribute("event.unhandled", True)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR, str(exc))
                raise

        return event

    async def _handle_price_update(self, event: MarketEvent) -> None:
        """
        Processa atualização de preço.

        Responsabilidades:
        - Valida e normaliza o campo price do payload
        - Registra métricas de latência e contagem
        - Emite span OTel com ticker e price como atributos
        - Hook point para alertas e stop loss (via AlertService no worker)

        Design decision: a avaliação de alertas NÃO acontece aqui.
        Ela é responsabilidade do worker (_process_event em main.py),
        que chama AlertService após o processamento do evento.
        Isso mantém EventProcessorService agnóstico sobre o AlertService,
        evitando dependência circular e facilitando testes.
        """
        _start = time.perf_counter()
        handler_name = "price_update"

        with self._tracer.start_as_current_span(
            "handler.price_update",
            attributes={"event.ticker": event.ticker},
        ) as span:
            try:
                raw_price = event.payload.get("price")
                price: Decimal | None = None
                if raw_price is not None:
                    try:
                        price = Decimal(str(raw_price))
                        span.set_attribute("price.value", float(price))
                    except InvalidOperation:
                        logger.warning(
                            "price_update.invalid_price",
                            ticker=event.ticker,
                            raw=str(raw_price),
                        )

                logger.debug(
                    "price.updated",
                    ticker=event.ticker,
                    price=str(price) if price is not None else None,
                )
                handler_events_total.labels(handler=handler_name, status="ok").inc()

            except Exception as exc:
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR, str(exc))
                handler_events_total.labels(handler=handler_name, status="error").inc()
                raise
            finally:
                handler_duration_seconds.labels(handler=handler_name).observe(time.perf_counter() - _start)

    async def _handle_ohlc_bar(self, event: MarketEvent) -> None:
        """
        Processa barra OHLC fechada e persiste via OHLCBarRepository.

        Responsabilidades:
        - Valida e extrai campos OHLC do payload
        - Persiste barra com idempotência (ON CONFLICT DO NOTHING)
        - Registra métricas e emite span com atributos OHLC

        Payload esperado:
          open, high, low, close: float/str (obrigatório)
          volume: float/str (opcional)
          timestamp: ISO8601 str ou epoch int (opcional, default=occurred_at)
          timeframe: str (opcional, default="1m")

        Design decision: ohlc_repo é opcional — quando None o handler loga
        mas não falha. Isso permite rodar o worker sem DB de OHLC configurado
        (ex: modo leve sem storage de séries temporais). O span registra
        ohlc.persisted=False para rastreabilidade de qual path foi usado.
        """
        from datetime import UTC, datetime
        from decimal import Decimal, InvalidOperation

        _start = time.perf_counter()
        handler_name = "ohlc_bar"

        with self._tracer.start_as_current_span(
            "handler.ohlc_bar",
            attributes={"event.ticker": event.ticker},
        ) as span:
            try:
                payload = event.payload

                # ── Extrai campos obrigatórios ────────────────────────────────
                def _to_decimal(key: str) -> Decimal | None:
                    val = payload.get(key)
                    if val is None:
                        return None
                    try:
                        return Decimal(str(val))
                    except InvalidOperation:
                        logger.warning("ohlc.invalid_field", field=key, value=str(val))
                        return None

                close = _to_decimal("close")
                open_ = _to_decimal("open")
                high = _to_decimal("high")
                low = _to_decimal("low")
                volume = _to_decimal("volume") or Decimal("0")
                timeframe = str(payload.get("timeframe", "1m"))

                # ── Extrai timestamp ──────────────────────────────────────────
                ts_raw = payload.get("timestamp")
                if isinstance(ts_raw, str):
                    try:
                        bar_ts = datetime.fromisoformat(ts_raw)
                        if bar_ts.tzinfo is None:
                            bar_ts = bar_ts.replace(tzinfo=UTC)
                    except ValueError:
                        bar_ts = event.occurred_at
                elif isinstance(ts_raw, (int, float)):
                    bar_ts = datetime.fromtimestamp(ts_raw, tz=UTC)
                else:
                    bar_ts = event.occurred_at

                # ── Span attributes ───────────────────────────────────────────
                if close is not None:
                    span.set_attribute("ohlc.close", float(close))
                if volume:
                    span.set_attribute("ohlc.volume", float(volume))
                span.set_attribute("ohlc.timeframe", timeframe)

                # ── Persiste via repository ───────────────────────────────────
                persisted = False
                if self._ohlc_repo is not None and all(v is not None for v in (open_, high, low, close)):
                    from finanalytics_ai.domain.entities.event import OHLCBar

                    bar = OHLCBar(
                        ticker=event.ticker,
                        timestamp=bar_ts,
                        timeframe=timeframe,
                        open=open_,  # type: ignore[arg-type]
                        high=high,  # type: ignore[arg-type]
                        low=low,  # type: ignore[arg-type]
                        close=close,  # type: ignore[arg-type]
                        volume=volume,
                        source=event.source,
                    )
                    persisted = await self._ohlc_repo.upsert_bar(bar)
                    span.set_attribute("ohlc.persisted", persisted)
                else:
                    span.set_attribute("ohlc.persisted", False)
                    if self._ohlc_repo is None:
                        logger.debug("ohlc.bar.repo_not_configured", ticker=event.ticker)
                    else:
                        logger.warning(
                            "ohlc.bar.missing_fields",
                            ticker=event.ticker,
                            has_open=open_ is not None,
                            has_high=high is not None,
                            has_low=low is not None,
                            has_close=close is not None,
                        )

                logger.debug(
                    "ohlc.bar.processed",
                    ticker=event.ticker,
                    timeframe=timeframe,
                    close=str(close) if close is not None else None,
                    persisted=persisted,
                )
                handler_events_total.labels(handler=handler_name, status="ok").inc()

            except Exception as exc:
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR, str(exc))
                handler_events_total.labels(handler=handler_name, status="error").inc()
                raise
            finally:
                handler_duration_seconds.labels(handler=handler_name).observe(time.perf_counter() - _start)

    async def _handle_news(self, event: MarketEvent) -> None:
        """
        Processa evento de notícia publicada com análise de sentimento.

        Fluxo:
        1. Extrai headline e source do payload.
        2. Se sentiment_analyzer injetado E headline presente: analisa sentimento.
        3. Se news_repo injetado: persiste resultado (idempotência por event_id).
        4. Emite span com score e label para observabilidade.

        Design decisions:
        - Análise de sentimento é opcional: sem analyzer, handler completa normalmente.
          Isso preserva backward compat e permite deploy incremental.
        - Falha no analyzer nunca propaga: retorna NewsSentiment.neutral() internamente.
          O span registra se sentimento foi analisado ou não via "news.sentiment_analyzed".
        - Persistência também é opcional: sem news_repo, sentimento é apenas logado.
          Útil para dev local sem DB.
        """
        _start = time.perf_counter()
        handler_name = "news"

        with self._tracer.start_as_current_span(
            "handler.news",
            attributes={"event.ticker": event.ticker},
        ) as span:
            try:
                payload = event.payload
                headline = str(payload.get("headline", ""))
                source = str(payload.get("source", event.source))

                if headline:
                    span.set_attribute("news.headline_length", len(headline))
                span.set_attribute("news.source", source)

                logger.debug(
                    "news.received",
                    ticker=event.ticker,
                    headline=headline[:80] if headline else None,
                    source=source,
                )

                # ── Análise de sentimento ──────────────────────────────────
                sentiment_analyzed = False
                if self._sentiment_analyzer is not None and headline:
                    sentiment = await self._sentiment_analyzer.analyze(
                        event_id=event.event_id,
                        ticker=event.ticker,
                        headline=headline,
                        source=source,
                    )
                    sentiment_analyzed = True

                    span.set_attribute("news.sentiment.score", sentiment.score)
                    span.set_attribute("news.sentiment.label", str(sentiment.label))
                    span.set_attribute("news.sentiment.model", sentiment.model)

                    logger.info(
                        "news.sentiment.analyzed",
                        ticker=event.ticker,
                        score=sentiment.score,
                        label=str(sentiment.label),
                        model=sentiment.model,
                        is_actionable=sentiment.is_actionable,
                    )

                    # ── Persistência ──────────────────────────────────────
                    if self._news_repo is not None:
                        inserted = await self._news_repo.save(sentiment)
                        span.set_attribute("news.sentiment.persisted", inserted)
                        if not inserted:
                            logger.debug(
                                "news.sentiment.duplicate",
                                event_id=event.event_id,
                            )
                else:
                    if not headline:
                        logger.warning(
                            "news.no_headline",
                            ticker=event.ticker,
                            event_id=event.event_id,
                        )

                span.set_attribute("news.sentiment_analyzed", sentiment_analyzed)
                handler_events_total.labels(handler=handler_name, status="ok").inc()

            except Exception as exc:
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR, str(exc))
                handler_events_total.labels(handler=handler_name, status="error").inc()
                raise
            finally:
                handler_duration_seconds.labels(handler=handler_name).observe(time.perf_counter() - _start)
