"""
Testes unitários para o worker standalone (main.py).

Foco: _process_event e run_event_worker.
Nenhum I/O real — database, fila e alertas são todos mocked.

Cobertura:
  - Evento processado com sucesso (PRICE_UPDATE)
  - Evento com EventProcessingError → não propaga, worker continua
  - PRICE_UPDATE aciona AlertService.evaluate_price()
  - Evento de outro tipo NÃO aciona AlertService
  - Tipo desconhecido (não MarketEvent) é descartado com warning
  - run_event_worker consome múltiplos eventos em sequência
  - CancelledError propaga (shutdown graceful)
  - Falha no AlertService não derruba o worker (degradação graceful)
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from finanalytics_ai.domain.entities.event import EventStatus, EventType, MarketEvent
from finanalytics_ai.exceptions import EventProcessingError
from finanalytics_ai.main import WorkerDeps, _process_event, run_event_worker


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def price_event() -> MarketEvent:
    return MarketEvent(
        event_id="evt-worker-001",
        event_type=EventType.PRICE_UPDATE,
        ticker="PETR4",
        payload={"price": "32.50"},
        source="brapi",
    )


@pytest.fixture
def ohlc_event() -> MarketEvent:
    return MarketEvent(
        event_id="evt-worker-002",
        event_type=EventType.OHLC_BAR_CLOSED,
        ticker="VALE3",
        payload={"open": 70.0, "close": 72.0},
        source="brapi",
    )


@pytest.fixture
def mock_alert_service() -> AsyncMock:
    svc = AsyncMock()
    svc.evaluate_price.return_value = 0
    return svc


@pytest.fixture
def mock_brapi() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def worker_deps(
    mock_brapi: AsyncMock,
    mock_alert_service: AsyncMock,
) -> WorkerDeps:
    from finanalytics_ai.infrastructure.queue.event_queue import InMemoryEventQueue

    return WorkerDeps(
        queue=InMemoryEventQueue(),
        brapi=mock_brapi,  # type: ignore[arg-type]
        alert_service=mock_alert_service,  # type: ignore[arg-type]
    )


def _make_session_ctx(mock_store: AsyncMock) -> object:
    """
    Cria um asynccontextmanager que retorna mock_store como session.
    Usado para substituir get_session() nos testes.
    """

    @asynccontextmanager
    async def _ctx() -> AsyncGenerator[AsyncMock, None]:
        yield AsyncMock()  # session não é usada diretamente pelo worker

    return _ctx


# ── _process_event ─────────────────────────────────────────────────────────────


class TestProcessEvent:
    @pytest.mark.asyncio
    async def test_price_update_processed_and_alerts_evaluated(
        self,
        price_event: MarketEvent,
        worker_deps: WorkerDeps,
        mock_alert_service: AsyncMock,
    ) -> None:
        """Evento PRICE_UPDATE deve processar e chamar evaluate_price."""
        mock_processed = price_event.mark_processed()

        with (
            patch("finanalytics_ai.main.get_session", _make_session_ctx(AsyncMock())),
            patch(
                "finanalytics_ai.main.SQLEventStore",
                return_value=AsyncMock(),
            ),
            patch(
                "finanalytics_ai.main.EventProcessorService.process",
                new_callable=AsyncMock,
                return_value=mock_processed,
            ),
        ):
            await _process_event(worker_deps, price_event)

        mock_alert_service.evaluate_price.assert_awaited_once_with("PETR4", 32.50)

    @pytest.mark.asyncio
    async def test_ohlc_event_does_not_trigger_alerts(
        self,
        ohlc_event: MarketEvent,
        worker_deps: WorkerDeps,
        mock_alert_service: AsyncMock,
    ) -> None:
        """OHLC_BAR_CLOSED não deve acionar AlertService."""
        mock_processed = ohlc_event.mark_processed()

        with (
            patch("finanalytics_ai.main.get_session", _make_session_ctx(AsyncMock())),
            patch("finanalytics_ai.main.SQLEventStore", return_value=AsyncMock()),
            patch(
                "finanalytics_ai.main.EventProcessorService.process",
                new_callable=AsyncMock,
                return_value=mock_processed,
            ),
        ):
            await _process_event(worker_deps, ohlc_event)

        mock_alert_service.evaluate_price.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_processing_error_is_absorbed(
        self,
        price_event: MarketEvent,
        worker_deps: WorkerDeps,
    ) -> None:
        """EventProcessingError não deve propagar — worker deve continuar."""
        with (
            patch("finanalytics_ai.main.get_session", _make_session_ctx(AsyncMock())),
            patch("finanalytics_ai.main.SQLEventStore", return_value=AsyncMock()),
            patch(
                "finanalytics_ai.main.EventProcessorService.process",
                new_callable=AsyncMock,
                side_effect=EventProcessingError(
                    message="falha simulada",
                    context={"event_id": price_event.event_id},
                ),
            ),
        ):
            # não deve levantar
            await _process_event(worker_deps, price_event)

    @pytest.mark.asyncio
    async def test_alert_failure_does_not_crash_worker(
        self,
        price_event: MarketEvent,
        worker_deps: WorkerDeps,
        mock_alert_service: AsyncMock,
    ) -> None:
        """Falha no AlertService não deve propagar — degradação graceful."""
        mock_alert_service.evaluate_price.side_effect = RuntimeError("redis down")
        mock_processed = price_event.mark_processed()

        with (
            patch("finanalytics_ai.main.get_session", _make_session_ctx(AsyncMock())),
            patch("finanalytics_ai.main.SQLEventStore", return_value=AsyncMock()),
            patch(
                "finanalytics_ai.main.EventProcessorService.process",
                new_callable=AsyncMock,
                return_value=mock_processed,
            ),
        ):
            # não deve levantar mesmo com AlertService quebrando
            await _process_event(worker_deps, price_event)

    @pytest.mark.asyncio
    async def test_unexpected_type_is_discarded(
        self,
        worker_deps: WorkerDeps,
    ) -> None:
        """Objeto que não é MarketEvent deve ser descartado silenciosamente."""
        await _process_event(worker_deps, {"tipo": "errado"})  # type: ignore[arg-type]
        worker_deps.alert_service.evaluate_price.assert_not_awaited()  # type: ignore[union-attr]


# ── run_event_worker ───────────────────────────────────────────────────────────


class TestRunEventWorker:
    @pytest.mark.asyncio
    async def test_processes_multiple_events_in_sequence(
        self,
        price_event: MarketEvent,
        worker_deps: WorkerDeps,
    ) -> None:
        """Worker deve consumir todos os eventos enfileirados antes do cancel."""
        processed: list[str] = []

        async def fake_process(deps: WorkerDeps, event: object) -> None:
            if isinstance(event, MarketEvent):
                processed.append(event.event_id)

        # Enfileirar 3 eventos
        for i in range(3):
            e = MarketEvent(
                event_id=f"evt-seq-{i}",
                event_type=EventType.PRICE_UPDATE,
                ticker="PETR4",
                payload={"price": "10.0"},
                source="test",
            )
            await worker_deps.queue.enqueue(e)

        with patch("finanalytics_ai.main._process_event", side_effect=fake_process):
            task = asyncio.create_task(run_event_worker(worker_deps))
            # Dar tempo para consumir os 3 eventos
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert len(processed) == 3
        assert processed == ["evt-seq-0", "evt-seq-1", "evt-seq-2"]

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_for_shutdown(
        self,
        worker_deps: WorkerDeps,
    ) -> None:
        """CancelledError deve propagar — é o mecanismo de shutdown graceful."""
        task = asyncio.create_task(run_event_worker(worker_deps))
        await asyncio.sleep(0.01)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_unhandled_exception_per_event_does_not_stop_worker(
        self,
        worker_deps: WorkerDeps,
    ) -> None:
        """Exceção inesperada em um evento não deve parar o loop do worker."""
        call_count = 0

        async def fake_process(deps: WorkerDeps, event: object) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("erro inesperado no evento 1")
            # evento 2 processa normalmente

        for i in range(2):
            e = MarketEvent(
                event_id=f"evt-err-{i}",
                event_type=EventType.PRICE_UPDATE,
                ticker="PETR4",
                payload={"price": "10.0"},
                source="test",
            )
            await worker_deps.queue.enqueue(e)

        with patch("finanalytics_ai.main._process_event", side_effect=fake_process):
            task = asyncio.create_task(run_event_worker(worker_deps))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Ambos os eventos foram tentados — o erro no 1 não parou o loop
        assert call_count == 2
