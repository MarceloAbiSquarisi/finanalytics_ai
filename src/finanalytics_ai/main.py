"""
FinAnalytics AI — Entrypoint principal (worker standalone).

Responsabilidade: consumir eventos da fila e processá-los com
EventProcessorService — idempotência, retry e persistência incluídos.

Arquitetura de dependências (sem frameworks mágicos):
  main()
    └── constrói BrapiClient, AlertService, NotificationBus
    └── passa WorkerDeps para run_event_worker
          └── por evento: abre AsyncSession → constrói SQLEventStore
                         → chama EventProcessorService.process()
                         → se PRICE_UPDATE: chama AlertService.evaluate_price()

Design decisions:
  - WorkerDeps como dataclass: todas as deps explícitas, sem globals.
  - Sessão por evento (não por worker): isola transações; um evento com
    erro não bloqueia os seguintes. Custo de abertura de conexão é
    amortizado pelo pool do SQLAlchemy.
  - AlertService é singleton (construído uma vez): mantém o NotificationBus
    com os subscribers SSE ativos durante toda a vida do processo.
  - _handle_price_update chama AlertService diretamente no handler de domínio:
    evita uma segunda fila e mantém a latência de alerta baixa.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from dataclasses import dataclass

import structlog
from opentelemetry import trace

from finanalytics_ai.application.commands.process_event import ProcessMarketEventCommand
from finanalytics_ai.application.services.alert_service import AlertService
from finanalytics_ai.application.services.event_processor import EventProcessorService
from finanalytics_ai.config import get_settings
from finanalytics_ai.domain.entities.event import EventType
from finanalytics_ai.exceptions import EventProcessingError
from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient
from finanalytics_ai.infrastructure.database.connection import (
    close_engine,
    get_engine,
    get_session,
)
from finanalytics_ai.infrastructure.database.repositories.event_store_repo import SQLEventStore
from finanalytics_ai.infrastructure.database.repositories.news_sentiment_repo import (
    SQLNewsSentimentRepository,
)
from finanalytics_ai.infrastructure.database.repositories.ohlc_bar_repo import SQLOHLCBarRepository
from finanalytics_ai.infrastructure.notifications import get_notification_bus
from finanalytics_ai.infrastructure.queue.event_queue import InMemoryEventQueue
from finanalytics_ai.infrastructure.sentiment.mock_analyzer import MockSentimentAnalyzer
from finanalytics_ai.logging_config import configure_logging
from finanalytics_ai.observability import setup_metrics, setup_tracing

logger = structlog.get_logger(__name__)


@dataclass
class WorkerDeps:
    """
    Dependências do worker agrupadas em um objeto explícito.

    Design decision: dataclass em vez de parâmetros avulsos.
    Facilita adicionar deps futuras (ex: OHLCRepository) sem
    mudar a assinatura de run_event_worker.
    """

    queue: InMemoryEventQueue
    brapi: BrapiClient
    alert_service: AlertService
    tracer: trace.Tracer | None = None
    ohlc_repo_factory: type[SQLOHLCBarRepository] = SQLOHLCBarRepository
    news_repo_factory: type[SQLNewsSentimentRepository] = SQLNewsSentimentRepository
    sentiment_analyzer_factory: type[MockSentimentAnalyzer] = MockSentimentAnalyzer


async def _process_event(deps: WorkerDeps, event: object) -> None:
    """
    Processa um único evento com sessão e transação próprias.

    Fluxo:
      1. Constrói ProcessMarketEventCommand a partir do MarketEvent da fila
      2. Abre sessão async → constrói SQLEventStore
      3. Chama EventProcessorService.process() (idempotência + retry incluídos)
      4. Se PRICE_UPDATE: chama AlertService.evaluate_price() (sem nova sessão —
         AlertService gerencia a sua internamente via session_factory)
    """
    from finanalytics_ai.domain.entities.event import MarketEvent

    if not isinstance(event, MarketEvent):
        logger.warning("event.worker.unexpected_type", got=type(event).__name__)
        return

    log = logger.bind(
        event_id=event.event_id,
        event_type=event.event_type,
        ticker=event.ticker,
    )

    command = ProcessMarketEventCommand(
        event_id=event.event_id,
        event_type=event.event_type.value,
        ticker=event.ticker,
        payload=event.payload,
        source=event.source,
        occurred_at=event.occurred_at,
    )

    async with get_session() as session:
        store = SQLEventStore(session)
        ohlc_repo = deps.ohlc_repo_factory(session)
        news_repo = deps.news_repo_factory(session)
        sentiment_analyzer = deps.sentiment_analyzer_factory()
        processor = EventProcessorService(
            event_store=store,
            market_data=deps.brapi,
            tracer=deps.tracer,
            ohlc_repo=ohlc_repo,
            sentiment_analyzer=sentiment_analyzer,
            news_repo=news_repo,
        )
        try:
            result = await processor.process(command)
            log.info("event.worker.processed", status=result.status)
        except EventProcessingError as exc:
            # EventProcessorService já persistiu o status FAILED e logou
            # Relogamos aqui para contexto do worker
            log.error("event.worker.processing_failed", error=str(exc))
            return

    # ── Hooks pós-processamento (fora da transação principal) ─────────────────
    # Rodamos fora do bloco get_session() para não manter a sessão aberta
    # durante chamadas potencialmente lentas (avaliação de alertas com I/O)
    if event.event_type == EventType.PRICE_UPDATE:
        price = event.payload.get("price")
        if price is not None:
            try:
                triggered = await deps.alert_service.evaluate_price(event.ticker, float(price))
                if triggered:
                    log.info("alerts.triggered", count=triggered, price=price)
            except Exception as exc:
                # Alertas nunca derrubam o worker — degradação graceful
                log.warning("alert.evaluation.failed", error=str(exc))


async def run_event_worker(deps: WorkerDeps) -> None:
    """
    Loop principal do worker — consome eventos da fila indefinidamente.

    Resiliente: exceções por evento são capturadas e logadas;
    o loop continua para o próximo evento.
    Shutdown: asyncio.CancelledError propaga normalmente (não capturada aqui).
    """
    logger.info("event.worker.started")
    while True:
        event = await deps.queue.dequeue()
        try:
            await _process_event(deps, event)
        except asyncio.CancelledError:
            raise  # propaga para shutdown graceful
        except Exception as exc:
            logger.error(
                "event.worker.unhandled_error",
                error=str(exc),
                event_id=getattr(event, "event_id", "unknown"),
            )
        finally:
            deps.queue.task_done()


async def main() -> None:
    configure_logging()
    settings = get_settings()

    logger.info(
        "finanalytics_ai.starting",
        env=settings.app_env,
        version="0.1.0",
    )

    # ── Observabilidade ───────────────────────────────────────────────────────
    tracer = setup_tracing()  # retorna Tracer configurado para injeção
    if settings.metrics_enabled:
        setup_metrics()

    # ── Infraestrutura ────────────────────────────────────────────────────────
    get_engine()  # inicializa pool; falha aqui se DATABASE_URL inválida
    logger.info("database.pool.ready")

    queue = InMemoryEventQueue()
    brapi = BrapiClient()

    # AlertService é singleton: mantém NotificationBus com subscribers SSE
    bus = get_notification_bus()
    alert_service = AlertService(
        session_factory=get_session,
        notification_bus=bus,
    )
    logger.info("alert_service.ready")

    deps = WorkerDeps(queue=queue, brapi=brapi, alert_service=alert_service, tracer=tracer)

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("finanalytics_ai.shutdown_requested")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    worker_task = asyncio.create_task(run_event_worker(deps))

    logger.info("finanalytics_ai.ready")
    await stop_event.wait()

    # ── Shutdown ordenado ─────────────────────────────────────────────────────
    worker_task.cancel()
    with contextlib.suppress(TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(worker_task, timeout=10.0)

    await brapi.close()
    await close_engine()
    logger.info("finanalytics_ai.stopped")


if __name__ == "__main__":
    asyncio.run(main())
