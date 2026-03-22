"""
Worker de sincronização Fintz — integrado com EventPublisher.

Este arquivo é a versão do fintz_sync_worker existente com a integração
do pipeline de eventos. Cada dataset processado publica um Event que
o event_worker vai consumir assincronamente.

Ciclo completo:
    fintz_sync_worker          event_worker
    ─────────────────          ─────────────
    sync dataset           →   publica FINTZ_SYNC_COMPLETED
    captura erro           →   publica FINTZ_SYNC_FAILED
                               ↓ (poll 5s)
                               FintzSyncCompletedRule.apply()
                               FintzSyncFailedRule.apply()
                               persiste resultado

Por que assincrono (não síncrono no mesmo loop)?
    O sync de um dataset pode levar dezenas de segundos (download de parquet).
    Processar as regras de negócio nesse mesmo loop bloquearia o próximo
    dataset. Com eventos assíncronos, o worker de sync e o worker de eventos
    são independentes e escaláveis separadamente.

Integração com o código existente:
    O FintzSyncService original (application/services/fintz_sync_service.py)
    deve chamar EventPublisher ao final de _sync_dataset().
    Este arquivo mostra o padrão a ser seguido.
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
from finanalytics_ai.container import bootstrap, build_engine, build_session_factory
from finanalytics_ai.exceptions import ExternalServiceError, TransientExternalServiceError
from finanalytics_ai.observability.logging import get_logger

log = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Value objects
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class DatasetSyncResult:
    """Resultado imutável do sync de um dataset.

    Separado do domínio de eventos propositalmente: é um DTO interno
    do worker, não uma entidade de domínio.
    """

    dataset: str
    rows_synced: int
    errors: int
    duration_s: float
    error_type: str | None = None
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error_type is None


@dataclass
class SyncSession:
    """Estado acumulado de uma sessão de sync completa."""

    started_at: float = field(default_factory=time.perf_counter)
    results: list[DatasetSyncResult] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(r.rows_synced for r in self.results)

    @property
    def total_errors(self) -> int:
        return sum(r.errors for r in self.results)

    @property
    def failed_datasets(self) -> list[str]:
        return [r.dataset for r in self.results if not r.succeeded]

    @property
    def duration_s(self) -> float:
        return time.perf_counter() - self.started_at


# ──────────────────────────────────────────────────────────────────────────────
# Integração: FintzSyncService → EventPublisher
# ──────────────────────────────────────────────────────────────────────────────


async def _publish_result(
    result: DatasetSyncResult,
    publisher: EventPublisher,
) -> None:
    """Publica o resultado de um dataset como evento no pipeline.

    Chamado após cada dataset — sucesso ou falha.
    Fire-and-forget: falha de publicação não aborta o sync.
    (O dataset já foi sincronizado; perder o evento é preferível a reverter o sync.)
    """
    try:
        if result.succeeded:
            await publisher.publish_fintz_sync_completed(
                dataset=result.dataset,
                rows_synced=result.rows_synced,
                errors=result.errors,
                duration_s=result.duration_s,
            )
        else:
            await publisher.publish_fintz_sync_failed(
                dataset=result.dataset,
                error_type=result.error_type or "UnknownError",
                error_message=result.error_message or "Sem mensagem de erro",
            )
    except Exception:
        # Falha de publicação não deve interromper o sync
        log.exception(
            "event_publish_failed_non_fatal",
            dataset=result.dataset,
            succeeded=result.succeeded,
        )


async def _sync_dataset(
    dataset: str,
    settings: Settings,
) -> DatasetSyncResult:
    """Stub de sync de um dataset.

    Na implementação real, este método:
    1. Chama FintzClient para baixar o parquet.
    2. Faz upsert no banco via FintzRepository.
    3. Atualiza fintz_sync_log.

    Aqui simulamos o comportamento para demonstrar a integração.
    O FintzSyncService.sync_dataset() existente deve seguir este contrato:
    retornar DatasetSyncResult em vez de lançar exceção para erros recuperáveis.
    """
    start = time.perf_counter()

    # TODO: substituir pela chamada real ao FintzSyncService
    # from finanalytics_ai.application.services.fintz_sync_service import FintzSyncService
    # result = await service.sync_dataset(dataset)
    # return DatasetSyncResult(dataset=dataset, rows_synced=result.rows, ...)

    log.info("fintz_sync_dataset_started", dataset=dataset)

    try:
        # Simulação: em produção, aqui vai a chamada real
        await asyncio.sleep(0.01)  # representa I/O real
        rows = 1000  # placeholder
        duration = time.perf_counter() - start

        log.info(
            "fintz_sync_dataset_completed",
            dataset=dataset,
            rows=rows,
            duration_s=round(duration, 3),
        )
        return DatasetSyncResult(
            dataset=dataset,
            rows_synced=rows,
            errors=0,
            duration_s=duration,
        )

    except TransientExternalServiceError as exc:
        duration = time.perf_counter() - start
        log.warning("fintz_sync_dataset_transient_error", dataset=dataset, error=str(exc))
        return DatasetSyncResult(
            dataset=dataset,
            rows_synced=0,
            errors=1,
            duration_s=duration,
            error_type="TransientAPIError",
            error_message=str(exc),
        )

    except ExternalServiceError as exc:
        duration = time.perf_counter() - start
        log.error("fintz_sync_dataset_permanent_error", dataset=dataset, error=str(exc))
        return DatasetSyncResult(
            dataset=dataset,
            rows_synced=0,
            errors=1,
            duration_s=duration,
            error_type="APIError",
            error_message=str(exc),
        )

    except Exception as exc:
        duration = time.perf_counter() - start
        log.exception("fintz_sync_dataset_unexpected_error", dataset=dataset)
        return DatasetSyncResult(
            dataset=dataset,
            rows_synced=0,
            errors=1,
            duration_s=duration,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Orquestrador principal
# ──────────────────────────────────────────────────────────────────────────────


# Datasets a sincronizar (em produção: importar ALL_DATASETS de fintz/entities.py)
_DATASETS_TO_SYNC = [
    "cotacoes",
    "itens_contabeis",
    "indicadores",
]


async def run_sync(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    datasets: list[str] | None = None,
) -> SyncSession:
    """Executa um ciclo completo de sync e publica eventos.

    Usa asyncio.Semaphore para limitar concorrência (evitar sobrecarga
    da API Fintz e do banco). Valor configurável via settings.

    Retorna SyncSession com métricas agregadas para logging e alertas.
    """
    targets = datasets or _DATASETS_TO_SYNC
    semaphore = asyncio.Semaphore(5)  # max 5 datasets em paralelo
    sync_session = SyncSession()

    async def _sync_and_publish(dataset: str) -> None:
        async with semaphore:
            result = await _sync_dataset(dataset, settings)
            sync_session.results.append(result)

            # Publica evento em sessão dedicada (não bloqueia o sync dos demais)
            async with session_factory() as db_session:
                async with db_session.begin():
                    publisher = EventPublisher(db_session)
                    await _publish_result(result, publisher)

    tasks = [_sync_and_publish(ds) for ds in targets]
    await asyncio.gather(*tasks, return_exceptions=False)

    log.info(
        "fintz_sync_session_completed",
        datasets_total=len(targets),
        datasets_failed=len(sync_session.failed_datasets),
        total_rows=sync_session.total_rows,
        total_errors=sync_session.total_errors,
        duration_s=round(sync_session.duration_s, 2),
    )

    return sync_session


async def run_once(settings: Settings) -> None:
    """Roda um sync único e sai (para scripts e testes manuais)."""
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    try:
        await run_sync(session_factory, settings)
    finally:
        await engine.dispose()


async def run_scheduled(stop_event: asyncio.Event, settings: Settings) -> None:
    """Loop agendado: roda às 22h05 BRT, igual ao worker original."""
    import datetime

    engine = build_engine(settings)
    session_factory = build_session_factory(engine)

    log.info("fintz_sync_worker_scheduled_started")

    while not stop_event.is_set():
        now = datetime.datetime.now(tz=datetime.timezone(datetime.timedelta(hours=-3)))
        target = now.replace(hour=22, minute=5, second=0, microsecond=0)
        if now >= target:
            target += datetime.timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        log.info(
            "fintz_sync_next_run",
            scheduled_at=target.isoformat(),
            wait_minutes=round(wait_seconds / 60, 1),
        )

        try:
            await asyncio.wait_for(
                asyncio.shield(stop_event.wait()),
                timeout=wait_seconds,
            )
        except asyncio.TimeoutError:
            pass  # chegou a hora

        if not stop_event.is_set():
            try:
                await run_sync(session_factory, settings)
            except Exception:
                log.exception("fintz_sync_scheduled_run_error")

    log.info("fintz_sync_worker_stopped")
    await engine.dispose()


def main() -> None:
    settings = get_settings()
    bootstrap(settings)

    run_once_flag = os.environ.get("RUN_ONCE", "").lower() in ("1", "true", "yes")

    stop_event = asyncio.Event()

    def _handle_signal(sig: int, _: object) -> None:
        log.info("fintz_sync_worker_shutdown", signal=sig)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        if run_once_flag:
            log.info("fintz_sync_worker_run_once")
            asyncio.run(run_once(settings))
        else:
            asyncio.run(run_scheduled(stop_event, settings))
    except KeyboardInterrupt:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
