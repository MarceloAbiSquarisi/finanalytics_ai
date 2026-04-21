"""
fintz_sync_service_updated.py — versão Sprint D do FintzSyncService.
Este arquivo deve SUBSTITUIR fintz_sync_service.py no projeto.

Mudanças em relação ao original:
  1. __init__ aceita event_publisher e timescale_writer opcionais
  2. _execute_sync: após upsert bem-sucedido, grava no TimescaleDB + publica evento
  3. _execute_sync: em caso de erro, publica evento de falha
  4. Ambas as operações são fire-and-forget (não abortam o sync se falharem)
  5. Zero breaking changes — comportamento idêntico sem as novas deps
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import structlog

from finanalytics_ai.domain.fintz.entities import ALL_DATASETS
from finanalytics_ai.exceptions import FintzAPIError, FintzParseError

if TYPE_CHECKING:
    from finanalytics_ai.application.services.event_publisher import EventPublisher
from finanalytics_ai.domain.fintz.entities import FintzDatasetSpec
from finanalytics_ai.domain.fintz.ports import FintzRepository
from finanalytics_ai.domain.fintz.timescale_port import TimescaleWriter
from finanalytics_ai.infrastructure.adapters.fintz_client import FintzClient

logger = structlog.get_logger(__name__)

MAX_CONCURRENT = 5


class FintzSyncService:
    """
    Serviço de sincronização de datasets Fintz.

    Sprint D: aceita EventPublisher e TimescaleWriter opcionais.
    Quando injetados:
      - TimescaleWriter: grava dados no TimescaleDB após cada sync bem-sucedido
      - EventPublisher: publica FINTZ_SYNC_COMPLETED ou FINTZ_SYNC_FAILED
    Ambas as operações são fire-and-forget — falha nelas não aborta o sync.
    """

    def __init__(
        self,
        client: FintzClient,
        repo: FintzRepository,
        max_concurrent: int = MAX_CONCURRENT,
        datasets: list[FintzDatasetSpec] | None = None,
        event_publisher: EventPublisher | None = None,
        timescale_writer: TimescaleWriter | None = None,
    ) -> None:
        self._client = client
        self._repo = repo
        self._sem = asyncio.Semaphore(max_concurrent)
        self._datasets = datasets or ALL_DATASETS
        self._event_publisher = event_publisher
        self._timescale_writer = timescale_writer

    # ── API pública ───────────────────────────────────────────────────────────

    async def sync_all(self) -> dict[str, Any]:
        logger.info("fintz_sync.start", total_datasets=len(self._datasets))
        tasks = [asyncio.create_task(self._sync_one(spec)) for spec in self._datasets]
        results: list[dict[str, Any]] = await asyncio.gather(*tasks, return_exceptions=False)
        return self._build_summary(results)

    async def sync_dataset(self, dataset_key: str) -> dict[str, Any]:
        spec = next((d for d in self._datasets if d.key == dataset_key), None)
        if not spec:
            raise ValueError(f"Dataset não encontrado: {dataset_key!r}")
        return await self._sync_one(spec)

    # ── Internals ─────────────────────────────────────────────────────────────

    async def _sync_one(self, spec: FintzDatasetSpec) -> dict[str, Any]:
        async with self._sem:
            return await self._execute_sync(spec)

    async def _execute_sync(self, spec: FintzDatasetSpec) -> dict[str, Any]:
        log = logger.bind(dataset_key=spec.key, dataset_type=spec.dataset_type)
        log.info("fintz_sync.dataset.start")
        _inc_metric("fintz_sync_attempts_total", spec.dataset_type)

        t0 = time.perf_counter()

        try:
            # 1. Download + hash
            df, sha256 = await self._client.fetch_dataset(spec)

            # 2. Idempotência
            last_hash = await self._repo.get_last_hash(spec.key)
            if last_hash == sha256:
                log.info("fintz_sync.dataset.skip", reason="hash_unchanged")
                _inc_metric("fintz_sync_skips_total", spec.dataset_type)
                return {"key": spec.key, "status": "skip", "rows": 0}

            # 3. Upsert Postgres (source of truth)
            rows = await self._upsert(df, spec)

            # 4. Grava no TimescaleDB (fire-and-forget)
            ts_rows = await self._write_timescale(df, spec)

            # 5. Registra sync bem-sucedido
            await self._repo.record_sync(
                dataset_key=spec.key,
                file_hash=sha256,
                rows_upserted=rows,
                status="ok",
            )

            duration_s = time.perf_counter() - t0
            log.info(
                "fintz_sync.dataset.ok",
                rows_upserted=rows,
                ts_rows=ts_rows,
                duration_s=round(duration_s, 2),
            )
            _inc_metric("fintz_sync_success_total", spec.dataset_type)
            _observe_rows_metric(rows, spec.dataset_type)

            # 6. Publica evento (fire-and-forget)
            await self._publish_completed(spec.key, rows, 0, duration_s)

            return {"key": spec.key, "status": "ok", "rows": rows, "ts_rows": ts_rows}

        except (FintzAPIError, FintzParseError) as exc:
            log.error("fintz_sync.dataset.error", error=str(exc), error_code=exc.code)
            await self._repo.record_sync(
                dataset_key=spec.key,
                file_hash="",
                rows_upserted=0,
                status="error",
                error_message=str(exc)[:500],
            )
            _inc_metric("fintz_sync_errors_total", spec.dataset_type)
            await self._publish_failed(spec.key, type(exc).__name__, str(exc))
            return {"key": spec.key, "status": "error", "error": str(exc)}

        except Exception as exc:
            log.exception("fintz_sync.dataset.unexpected_error", error=str(exc))
            await self._repo.record_sync(
                dataset_key=spec.key,
                file_hash="",
                rows_upserted=0,
                status="error",
                error_message=f"Unexpected: {exc!s}"[:500],
            )
            _inc_metric("fintz_sync_errors_total", spec.dataset_type)
            await self._publish_failed(spec.key, type(exc).__name__, str(exc))
            return {"key": spec.key, "status": "error", "error": str(exc)}

    async def _upsert(self, df: Any, spec: FintzDatasetSpec) -> int:
        if spec.dataset_type == "cotacoes":
            return await self._repo.upsert_cotacoes(df)
        if spec.dataset_type == "item_contabil":
            return await self._repo.upsert_itens_contabeis(df, spec)
        if spec.dataset_type == "indicador":
            return await self._repo.upsert_indicadores(df, spec)
        raise ValueError(f"dataset_type desconhecido: {spec.dataset_type!r}")

    async def _write_timescale(self, df: Any, spec: FintzDatasetSpec) -> int:
        """Grava no TimescaleDB. Fire-and-forget — retorna -1 em caso de falha."""
        if self._timescale_writer is None:
            return 0
        try:
            return await self._timescale_writer.write(df, spec)
        except Exception as exc:
            logger.warning(
                "fintz_sync.timescale.failed",
                dataset_key=spec.key,
                error=str(exc),
            )
            return -1

    async def _publish_completed(
        self,
        dataset_key: str,
        rows_synced: int,
        errors: int,
        duration_s: float,
    ) -> None:
        """Publica evento FINTZ_SYNC_COMPLETED. Fire-and-forget."""
        if self._event_publisher is None:
            return
        try:
            await self._event_publisher.publish_fintz_sync_completed(
                dataset=dataset_key,
                rows_synced=rows_synced,
                errors=errors,
                duration_s=round(duration_s, 2),
            )
        except Exception as exc:
            logger.warning(
                "fintz_sync.event_publish.failed",
                dataset_key=dataset_key,
                error=str(exc),
            )

    async def _publish_failed(
        self,
        dataset_key: str,
        error_type: str,
        error_message: str,
    ) -> None:
        """Publica evento FINTZ_SYNC_FAILED. Fire-and-forget."""
        if self._event_publisher is None:
            return
        try:
            await self._event_publisher.publish_fintz_sync_failed(
                dataset=dataset_key,
                error_type=error_type,
                error_message=error_message[:500],
            )
        except Exception as exc:
            logger.warning(
                "fintz_sync.event_publish_failed.failed",
                dataset_key=dataset_key,
                error=str(exc),
            )

    @staticmethod
    def _build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
        ok = sum(1 for r in results if r["status"] == "ok")
        skip = sum(1 for r in results if r["status"] == "skip")
        error = sum(1 for r in results if r["status"] == "error")
        total_rows = sum(r.get("rows", 0) for r in results)
        failed = [r["key"] for r in results if r["status"] == "error"]

        logger.info(
            "fintz_sync.complete",
            ok=ok,
            skip=skip,
            error=error,
            total_rows=total_rows,
            failed_count=len(failed),
        )
        return {
            "ok": ok,
            "skip": skip,
            "error": error,
            "total": len(results),
            "total_rows": total_rows,
            "failed_keys": failed,
            "datasets": {r["key"]: r for r in results},
        }


# ── Helpers de métricas ───────────────────────────────────────────────────────


def _inc_metric(name: str, label: str) -> None:
    try:
        from finanalytics_ai.metrics import (
            fintz_sync_attempts_total,
            fintz_sync_errors_total,
            fintz_sync_skips_total,
            fintz_sync_success_total,
        )

        metric_map = {
            "fintz_sync_attempts_total": fintz_sync_attempts_total,
            "fintz_sync_success_total": fintz_sync_success_total,
            "fintz_sync_skips_total": fintz_sync_skips_total,
            "fintz_sync_errors_total": fintz_sync_errors_total,
        }
        m = metric_map.get(name)
        if m is not None:
            m.labels(dataset_type=label).inc()
    except Exception:
        pass


def _observe_rows_metric(rows: int, label: str) -> None:
    try:
        from finanalytics_ai.metrics import fintz_rows_upserted_total

        fintz_rows_upserted_total.labels(dataset_type=label).inc(rows)
    except Exception:
        pass
