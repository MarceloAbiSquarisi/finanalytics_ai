"""
Testes unitários — fintz_sync_worker.

Foco: integração entre sync de dataset e publicação de eventos.
Não testa I/O real (Fintz API, banco) — apenas o comportamento do orquestrador.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from finanalytics_ai.workers.fintz_sync_worker import (
    DatasetSyncResult,
    SyncSession,
    _publish_result,
    run_sync,
)


class TestDatasetSyncResult:
    def test_succeeded_true_when_no_error(self) -> None:
        r = DatasetSyncResult("cotacoes", rows_synced=100, errors=0, duration_s=1.0)
        assert r.succeeded is True

    def test_succeeded_false_when_error_type_set(self) -> None:
        r = DatasetSyncResult(
            "cotacoes", rows_synced=0, errors=1, duration_s=0.5,
            error_type="APIError", error_message="timeout",
        )
        assert r.succeeded is False


class TestSyncSession:
    def test_aggregates_rows_and_errors(self) -> None:
        s = SyncSession()
        s.results = [
            DatasetSyncResult("a", 100, 5, 1.0),
            DatasetSyncResult("b", 200, 0, 2.0),
        ]
        assert s.total_rows == 300
        assert s.total_errors == 5

    def test_failed_datasets_only_includes_errors(self) -> None:
        s = SyncSession()
        s.results = [
            DatasetSyncResult("ok_dataset", 100, 0, 1.0),
            DatasetSyncResult("bad_dataset", 0, 1, 0.5, "APIError", "timeout"),
        ]
        assert s.failed_datasets == ["bad_dataset"]

    def test_duration_is_positive(self) -> None:
        s = SyncSession()
        time.sleep(0.01)
        assert s.duration_s > 0


class TestPublishResult:
    async def test_success_calls_publish_completed(self) -> None:
        publisher = MagicMock()
        publisher.publish_fintz_sync_completed = AsyncMock(return_value=MagicMock())
        result = DatasetSyncResult("cotacoes", 500, 10, 3.0)

        await _publish_result(result, publisher)

        publisher.publish_fintz_sync_completed.assert_called_once_with(
            dataset="cotacoes",
            rows_synced=500,
            errors=10,
            duration_s=3.0,
        )

    async def test_failure_calls_publish_failed(self) -> None:
        publisher = MagicMock()
        publisher.publish_fintz_sync_failed = AsyncMock(return_value=MagicMock())
        result = DatasetSyncResult(
            "cotacoes", 0, 1, 0.5,
            error_type="APIError",
            error_message="HTTP 503",
        )

        await _publish_result(result, publisher)

        publisher.publish_fintz_sync_failed.assert_called_once_with(
            dataset="cotacoes",
            error_type="APIError",
            error_message="HTTP 503",
        )

    async def test_publish_exception_does_not_propagate(self) -> None:
        """Falha de publicação não deve abortar o sync (fire-and-forget)."""
        publisher = MagicMock()
        publisher.publish_fintz_sync_completed = AsyncMock(
            side_effect=Exception("DB connection refused")
        )
        result = DatasetSyncResult("cotacoes", 100, 0, 1.0)

        # Não deve lançar exceção
        await _publish_result(result, publisher)


class TestRunSync:
    async def test_returns_session_with_all_results(self) -> None:
        publisher_mock = MagicMock()
        publisher_mock.publish_fintz_sync_completed = AsyncMock(return_value=MagicMock())
        publisher_mock.publish_fintz_sync_failed = AsyncMock(return_value=MagicMock())

        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session_ctx)
        session_ctx.__aexit__ = AsyncMock(return_value=False)
        session_ctx.begin = MagicMock(return_value=session_ctx)
        session_factory = MagicMock(return_value=session_ctx)

        with patch(
            "finanalytics_ai.workers.fintz_sync_worker._sync_dataset",
            new=AsyncMock(side_effect=lambda ds, s: DatasetSyncResult(ds, 100, 0, 1.0)),
        ), patch(
            "finanalytics_ai.workers.fintz_sync_worker.EventPublisher",
            return_value=publisher_mock,
        ):
            sync_session = await run_sync(
                session_factory, MagicMock(), datasets=["cotacoes", "indicadores"]
            )

        assert len(sync_session.results) == 2
        assert sync_session.total_rows == 200
        assert sync_session.failed_datasets == []

    async def test_continues_after_partial_failure(self) -> None:
        """Falha em um dataset não aborta os outros — gather continua."""
        publisher_mock = MagicMock()
        publisher_mock.publish_fintz_sync_completed = AsyncMock(return_value=MagicMock())
        publisher_mock.publish_fintz_sync_failed = AsyncMock(return_value=MagicMock())

        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session_ctx)
        session_ctx.__aexit__ = AsyncMock(return_value=False)
        session_ctx.begin = MagicMock(return_value=session_ctx)
        session_factory = MagicMock(return_value=session_ctx)

        def _sync_side(ds: str, s: Any) -> DatasetSyncResult:
            if ds == "cotacoes":
                return DatasetSyncResult(ds, 0, 1, 0.5, "APIError", "timeout")
            return DatasetSyncResult(ds, 100, 0, 1.0)

        with patch(
            "finanalytics_ai.workers.fintz_sync_worker._sync_dataset",
            new=AsyncMock(side_effect=_sync_side),
        ), patch(
            "finanalytics_ai.workers.fintz_sync_worker.EventPublisher",
            return_value=publisher_mock,
        ):
            sync_session = await run_sync(
                session_factory, MagicMock(), datasets=["cotacoes", "indicadores"]
            )

        assert len(sync_session.results) == 2
        assert sync_session.failed_datasets == ["cotacoes"]
        assert sync_session.total_rows == 100  # só indicadores teve sucesso

    async def test_publishes_event_for_each_dataset(self) -> None:
        """Um evento é publicado por dataset — sucesso ou falha."""
        publisher_mock = MagicMock()
        publisher_mock.publish_fintz_sync_completed = AsyncMock(return_value=MagicMock())

        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session_ctx)
        session_ctx.__aexit__ = AsyncMock(return_value=False)
        session_ctx.begin = MagicMock(return_value=session_ctx)
        session_factory = MagicMock(return_value=session_ctx)

        datasets = ["cotacoes", "indicadores", "itens_contabeis"]

        with patch(
            "finanalytics_ai.workers.fintz_sync_worker._sync_dataset",
            new=AsyncMock(side_effect=lambda ds, s: DatasetSyncResult(ds, 10, 0, 0.1)),
        ), patch(
            "finanalytics_ai.workers.fintz_sync_worker.EventPublisher",
            return_value=publisher_mock,
        ):
            await run_sync(session_factory, MagicMock(), datasets=datasets)

        assert publisher_mock.publish_fintz_sync_completed.call_count == len(datasets)
