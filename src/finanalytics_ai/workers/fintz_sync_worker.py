"""
Worker de sincronizacao Fintz -- Sprint I: integracao real com FintzSyncService.

Substitui o stub _sync_dataset pela chamada real.
Idempotencia garantida pelo SHA-256 por dataset no fintz_sync_log.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finanalytics_ai.application.services.event_publisher import EventPublisher
from finanalytics_ai.config import Settings, get_settings
from finanalytics_ai.container import (
    bootstrap,
    build_engine,
    build_session_factory,
    build_timescale_writer,
)
from finanalytics_ai.observability.logging import get_logger

log = get_logger(__name__)


@dataclass
class DatasetSyncResult:
    dataset: str
    rows_synced: int
    errors: int
    duration_s: float
    status: str = "ok"
    error_type: str | None = None
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status in ("ok", "skip")


@dataclass
class SyncSession:
    started_at: float = field(default_factory=time.perf_counter)
    results: list[DatasetSyncResult] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(r.rows_synced for r in self.results)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.status == "ok")

    @property
    def skip_count(self) -> int:
        return sum(1 for r in self.results if r.status == "skip")

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.results if r.status == "error")

    @property
    def failed_datasets(self) -> list[str]:
        return [r.dataset for r in self.results if r.status == "error"]

    @property
    def duration_s(self) -> float:
        return time.perf_counter() - self.started_at




async def _publish_result(result, publisher) -> None:
    try:
        if result.succeeded:
            await publisher.publish_fintz_sync_completed(dataset=result.dataset, rows_synced=result.rows_synced, errors=result.errors, duration_s=result.duration_s)
        else:
            await publisher.publish_fintz_sync_failed(dataset=result.dataset, error_type=result.error_type or 'UnknownError', error_message=result.error_message or '')
    except Exception:
        pass

async def run_sync(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    datasets: list[str] | None = None,
) -> SyncSession:
    """
    Executa sync completo via FintzSyncService real.

    datasets=None -> ALL_DATASETS (80 datasets).
    datasets=['cotacoes_ohlc', ...] -> apenas esses.
    """
    from finanalytics_ai.application.services.fintz_sync_service import FintzSyncService
    from finanalytics_ai.infrastructure.adapters.fintz_client import FintzClient
    from finanalytics_ai.infrastructure.database.repositories.fintz_repo import FintzRepo
    from finanalytics_ai.domain.fintz.entities import ALL_DATASETS

    sync_session = SyncSession()

    # Filtra specs se especificados
    if datasets:
        specs = [s for s in ALL_DATASETS if s.key in datasets]
        if not specs:
            log.warning("fintz_sync.no_matching_datasets", requested=datasets)
            return sync_session
    else:
        specs = None  # FintzSyncService usa ALL_DATASETS por padrao

    log.info(
        "fintz_sync.started",
        total_datasets=len(specs) if specs else len(ALL_DATASETS),
    )

    ts_writer = build_timescale_writer(settings)

    async with FintzClient(
        api_key=settings.fintz_api_key,
        base_url=settings.fintz_base_url,
        api_timeout_s=30.0,
        link_timeout_s=300.0,
        max_retries=3,
    ) as client:
        async with session_factory() as db_session:
            async with db_session.begin():
                publisher = EventPublisher(db_session)
                service = FintzSyncService(
                    client=client,
                    repo=FintzRepo(),
                    event_publisher=publisher,
                    timescale_writer=ts_writer,
                    datasets=specs,
                )
                summary = await service.sync_all()

    # Converte summary -> SyncSession
    for key, result in summary.get("datasets", {}).items():
        status = result.get("status", "error")
        sync_session.results.append(DatasetSyncResult(
            dataset=key,
            rows_synced=result.get("rows", 0),
            errors=0 if status != "error" else 1,
            duration_s=0.0,
            status=status,
            error_type="SyncError" if status == "error" else None,
            error_message=result.get("error") if status == "error" else None,
        ))

    log.info(
        "fintz_sync.session_completed",
        ok=sync_session.ok_count,
        skip=sync_session.skip_count,
        error=sync_session.error_count,
        total_rows=sync_session.total_rows,
        duration_s=round(sync_session.duration_s, 2),
        failed=sync_session.failed_datasets[:5],
    )

    return sync_session


async def run_once(settings: Settings, datasets: list[str] | None = None) -> None:
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    try:
        await run_sync(session_factory, settings, datasets=datasets)
    finally:
        await engine.dispose()


async def run_scheduled(stop_event: asyncio.Event, settings: Settings) -> None:
    import datetime
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    log.info("fintz_sync_worker.scheduled_started")

    while not stop_event.is_set():
        now = datetime.datetime.now(
            tz=datetime.timezone(datetime.timedelta(hours=-3))
        )
        target = now.replace(hour=22, minute=5, second=0, microsecond=0)
        if now >= target:
            target += datetime.timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        log.info(
            "fintz_sync.next_run",
            next_run=target.isoformat(),
            wait_minutes=round(wait_seconds / 60, 1),
        )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=wait_seconds)
        except asyncio.TimeoutError:
            pass

        if stop_event.is_set():
            break

        log.info("fintz_sync.running_scheduled")
        try:
            await run_sync(session_factory, settings)
        except Exception as exc:
            log.exception("fintz_sync.scheduled_error", error=str(exc))

    await engine.dispose()
    log.info("fintz_sync_worker.stopped")


def main() -> None:
    settings = get_settings()
    bootstrap(settings)
    stop_event = asyncio.Event()

    def _handle_signal(sig: int, _: Any) -> None:
        log.info("fintz_sync_worker.signal_received", signal=sig)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    if os.getenv("RUN_ONCE", "false").lower() == "true":
        # Suporta filtro de datasets via variavel de ambiente
        # ex: SYNC_DATASETS="cotacoes_ohlc,indicador_ROE"
        ds_env = os.getenv("SYNC_DATASETS", "")
        datasets = [d.strip() for d in ds_env.split(",") if d.strip()] or None
        log.info("fintz_sync_worker.run_once_mode", datasets=datasets or "all")
        asyncio.run(run_once(settings, datasets=datasets))
    else:
        asyncio.run(run_scheduled(stop_event, settings))


if __name__ == "__main__":
    main()

