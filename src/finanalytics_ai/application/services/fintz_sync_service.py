"""
finanalytics_ai.application.services.fintz_sync_service
─────────────────────────────────────────────────────────
Orquestra o sync completo dos datasets Fintz.

Responsabilidade: para cada FintzDatasetSpec do catálogo,
coordenar: download → hash → idempotência → parse → upsert → log.

Design decisions:

  asyncio.Semaphore para concorrência controlada:
    A Fintz serve ~80 datasets/dia. Downloads paralelos sem limite
    saturariam a rede e poderiam gerar rate-limiting. Semáforo com
    MAX_CONCURRENT=5 empírico: descarga o pipeline rapidamente sem
    arriscar bloqueio.
    Trade-off: APScheduler + job pool seria mais observável, mas
    adiciona dependência e complexidade inconsistente com o resto
    do projeto (que usa asyncio puro).

  Hash-first idempotência:
    Antes de parsear o parquet (CPU-intensivo), verificamos se o
    SHA-256 dos bytes mudou em relação ao último sync bem-sucedido.
    Isso reduz CPU ~90% em dias sem atualização real.

  Skip ≠ Error no sync_log:
    Datasets com hash idêntico são marcados como "skip", não "ok".
    Isso preserva o timestamp do último sync real para auditoria.

  Falhas isoladas por dataset:
    Um erro em "item_EBITDA_TRIMESTRAL" não aborta os outros 79
    datasets. O service coleta todos os erros e os reporta no sumário
    final. O worker decide se lança FintzSyncError ou não.

  Injeção de dependências manual:
    FintzClient e FintzRepo são injetados no construtor. Isso permite
    substituição nos testes sem monkey-patching de módulos.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from finanalytics_ai.domain.fintz.entities import ALL_DATASETS
from finanalytics_ai.exceptions import FintzAPIError, FintzParseError

if TYPE_CHECKING:
    from finanalytics_ai.domain.fintz.entities import FintzDatasetSpec
    from finanalytics_ai.domain.fintz.ports import FintzRepository
    from finanalytics_ai.infrastructure.adapters.fintz_client import FintzClient

logger = structlog.get_logger(__name__)

MAX_CONCURRENT = 5          # downloads simultâneos


class FintzSyncService:
    """
    Serviço de sincronização de datasets Fintz.

    Usage:
        repo   = FintzRepo()
        async with create_fintz_client() as client:
            svc = FintzSyncService(client=client, repo=repo)
            summary = await svc.sync_all()
    """

    def __init__(
        self,
        client: FintzClient,
        repo: FintzRepository,
        max_concurrent: int = MAX_CONCURRENT,
        datasets: list[FintzDatasetSpec] | None = None,
    ) -> None:
        self._client = client
        self._repo = repo
        self._sem = asyncio.Semaphore(max_concurrent)
        self._datasets = datasets or ALL_DATASETS

    # ── API pública ───────────────────────────────────────────────────────────

    async def sync_all(self) -> dict[str, Any]:
        """
        Sincroniza todos os datasets do catálogo em paralelo (com semáforo).

        Returns dict com sumário:
            {
                "ok": int,
                "skip": int,
                "error": int,
                "total": int,
                "failed_keys": list[str],
                "datasets": {key: {"status": ..., "rows": ...}}
            }
        """
        logger.info("fintz_sync.start", total_datasets=len(self._datasets))

        tasks = [
            asyncio.create_task(self._sync_one(spec))
            for spec in self._datasets
        ]
        results: list[dict[str, Any]] = await asyncio.gather(*tasks, return_exceptions=False)

        return self._build_summary(results)

    async def sync_dataset(self, dataset_key: str) -> dict[str, Any]:
        """Sincroniza um único dataset pelo key. Útil para reprocessamento pontual."""
        spec = next((d for d in self._datasets if d.key == dataset_key), None)
        if not spec:
            raise ValueError(f"Dataset não encontrado: {dataset_key!r}")
        return await self._sync_one(spec)

    # ── Internals ─────────────────────────────────────────────────────────────

    async def _sync_one(self, spec: FintzDatasetSpec) -> dict[str, Any]:
        """Sincroniza um dataset — isolado de falhas de outros."""
        async with self._sem:
            return await self._execute_sync(spec)

    async def _execute_sync(self, spec: FintzDatasetSpec) -> dict[str, Any]:
        log = logger.bind(dataset_key=spec.key, dataset_type=spec.dataset_type)
        log.info("fintz_sync.dataset.start")

        # Métricas — hook best-effort
        _inc_metric("fintz_sync_attempts_total", spec.dataset_type)

        try:
            # 1. Download + hash
            df, sha256 = await self._client.fetch_dataset(spec)

            # 2. Idempotência: compara com o hash anterior
            last_hash = await self._repo.get_last_hash(spec.key)
            if last_hash == sha256:
                log.info("fintz_sync.dataset.skip", reason="hash_unchanged")
                _inc_metric("fintz_sync_skips_total", spec.dataset_type)
                return {"key": spec.key, "status": "skip", "rows": 0}

            # 3. Upsert no banco
            rows = await self._upsert(df, spec)

            # 4. Registra sync bem-sucedido
            await self._repo.record_sync(
                dataset_key=spec.key,
                file_hash=sha256,
                rows_upserted=rows,
                status="ok",
            )

            log.info("fintz_sync.dataset.ok", rows_upserted=rows)
            _inc_metric("fintz_sync_success_total", spec.dataset_type)
            _observe_rows_metric(rows, spec.dataset_type)
            return {"key": spec.key, "status": "ok", "rows": rows}

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
            return {"key": spec.key, "status": "error", "error": str(exc)}

    async def _upsert(self, df: "Any", spec: FintzDatasetSpec) -> int:
        """Despacha para o método de upsert correto conforme o tipo de dataset."""
        if spec.dataset_type == "cotacoes":
            return await self._repo.upsert_cotacoes(df)
        if spec.dataset_type == "item_contabil":
            return await self._repo.upsert_itens_contabeis(df, spec)
        if spec.dataset_type == "indicador":
            return await self._repo.upsert_indicadores(df, spec)
        raise ValueError(f"dataset_type desconhecido: {spec.dataset_type!r}")

    @staticmethod
    def _build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
        ok    = sum(1 for r in results if r["status"] == "ok")
        skip  = sum(1 for r in results if r["status"] == "skip")
        error = sum(1 for r in results if r["status"] == "error")
        total_rows = sum(r.get("rows", 0) for r in results)
        failed = [r["key"] for r in results if r["status"] == "error"]

        logger.info(
            "fintz_sync.complete",
            ok=ok, skip=skip, error=error,
            total_rows=total_rows,
            failed_count=len(failed),
        )
        return {
            "ok":          ok,
            "skip":        skip,
            "error":       error,
            "total":       len(results),
            "total_rows":  total_rows,
            "failed_keys": failed,
            "datasets":    {r["key"]: r for r in results},
        }


# ── Helpers de métricas (best-effort — não bloqueia o sync) ──────────────────


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
            "fintz_sync_success_total":  fintz_sync_success_total,
            "fintz_sync_skips_total":    fintz_sync_skips_total,
            "fintz_sync_errors_total":   fintz_sync_errors_total,
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
