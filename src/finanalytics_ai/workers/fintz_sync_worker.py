#!/usr/bin/env python3
"""
fintz_sync_worker — Sincronização diária dos datasets Fintz.

Jobs agendados (horário de Brasília, UTC-3):
  22:05  fintz_sync_job  — baixa todos os ~80 parquets Fintz e faz upsert no PostgreSQL.
                           Janela: após o fechamento do mercado e atualização Fintz (22h).

Design:
  - asyncio puro, sem frameworks de scheduler — consistente com scheduler_worker.py.
  - RUN_ONCE=true para carga histórica inicial e CI.
  - FINTZ_SYNC_DATASETS=cotacoes,item_EBIT_12M para sync seletivo (debug/reprocessamento).
  - Idempotente: sync_service pula datasets cujo hash não mudou.
  - Resiliente: falhas em datasets individuais não abortam o job.
  - Observabilidade: structlog + Prometheus hooks no sync_service.

Env vars:
  FINTZ_API_KEY              — obrigatório
  FINTZ_SYNC_HOUR            — hora local do disparo (padrão: 22)
  FINTZ_SYNC_MINUTE          — minuto local (padrão: 5)
  SCHEDULER_TZ_OFFSET        — offset UTC (padrão: -3, Brasília)
  RUN_ONCE                   — executa uma vez e sai (padrão: false)
  FINTZ_SYNC_DATASETS        — lista separada por vírgula de dataset_keys específicos
                               (padrão: vazio = todos)
  FINTZ_MAX_CONCURRENT       — semáforo de downloads simultâneos (padrão: 5)
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

# ── Configuração via env ──────────────────────────────────────────────────────
FINTZ_SYNC_HOUR    = int(os.environ.get("FINTZ_SYNC_HOUR", "22"))
FINTZ_SYNC_MINUTE  = int(os.environ.get("FINTZ_SYNC_MINUTE", "5"))
TZ_OFFSET          = int(os.environ.get("SCHEDULER_TZ_OFFSET", "-3"))
RUN_ONCE           = os.environ.get("RUN_ONCE", "false").lower() == "true"
SYNC_DATASETS_ENV  = os.environ.get("FINTZ_SYNC_DATASETS", "")   # ex: "cotacoes_ohlc,indicador_ROE"
MAX_CONCURRENT     = int(os.environ.get("FINTZ_MAX_CONCURRENT", "5"))

# ── Logger ────────────────────────────────────────────────────────────────────
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(colors=False),
    ]
)
logger = structlog.get_logger("fintz_sync_worker")


# ── Helpers de tempo (mesma lógica do scheduler_worker.py) ───────────────────

def _next_run_utc(local_hour: int, local_minute: int = 0, tz_offset: int = TZ_OFFSET) -> datetime:
    """Calcula próxima execução em UTC dado horário local."""
    now_utc  = datetime.now(UTC)
    utc_hour = (local_hour - tz_offset) % 24
    next_run = now_utc.replace(hour=utc_hour, minute=local_minute, second=0, microsecond=0)
    if next_run <= now_utc:
        next_run += timedelta(days=1)
    return next_run


def _seconds_until(target: datetime) -> float:
    return max(0.0, (target - datetime.now(UTC)).total_seconds())


def _parse_dataset_filter() -> list[str] | None:
    """Retorna lista de keys a sincronizar, ou None para todos."""
    if not SYNC_DATASETS_ENV.strip():
        return None
    return [k.strip() for k in SYNC_DATASETS_ENV.split(",") if k.strip()]


# ── Job ───────────────────────────────────────────────────────────────────────

async def fintz_sync_job() -> dict[str, Any]:
    """
    Baixa e persiste todos os datasets Fintz configurados.

    Retorna sumário {ok, skip, error, total, total_rows, failed_keys}.
    """
    logger.info("fintz_sync_worker.job.start", max_concurrent=MAX_CONCURRENT)

    try:
        from finanalytics_ai.application.services.fintz_sync_service import FintzSyncService
        from finanalytics_ai.domain.fintz.entities import ALL_DATASETS
        from finanalytics_ai.infrastructure.adapters.fintz_client import create_fintz_client
        from finanalytics_ai.infrastructure.database.repositories.fintz_repo import FintzRepo

        # Filtro opcional de datasets
        dataset_filter = _parse_dataset_filter()
        datasets = (
            [d for d in ALL_DATASETS if d.key in dataset_filter]
            if dataset_filter else ALL_DATASETS
        )

        if dataset_filter and not datasets:
            logger.warning(
                "fintz_sync_worker.job.skip",
                reason="no_matching_datasets",
                filter=dataset_filter,
            )
            return {"status": "skip", "reason": "no_matching_datasets"}

        logger.info("fintz_sync_worker.job.datasets", count=len(datasets))

        repo = FintzRepo()
        async with create_fintz_client() as client:
            svc = FintzSyncService(
                client=client,
                repo=repo,
                max_concurrent=MAX_CONCURRENT,
                datasets=datasets,
            )
            summary = await svc.sync_all()

        logger.info(
            "fintz_sync_worker.job.done",
            ok=summary["ok"],
            skip=summary["skip"],
            error=summary["error"],
            total_rows=summary["total_rows"],
            failed_keys=summary["failed_keys"],
        )
        return {"status": "ok", **summary}

    except Exception as exc:
        logger.exception("fintz_sync_worker.job.failed", error=str(exc))
        return {"status": "error", "error": str(exc)}


# ── Run-once ──────────────────────────────────────────────────────────────────

async def run_once() -> None:
    """Executa o job uma única vez e encerra. Ideal para carga histórica inicial."""
    logger.info("fintz_sync_worker.run_once.start")
    result = await fintz_sync_job()
    logger.info("fintz_sync_worker.run_once.done", **result)


# ── Schedule loop ─────────────────────────────────────────────────────────────

async def schedule_loop() -> None:
    """
    Loop principal — dorme até 22h05 BRT, executa, aguarda até o dia seguinte.

    Não usa APScheduler para manter consistência com o padrão do projeto
    (scheduler_worker.py usa a mesma abordagem).
    """
    logger.info(
        "fintz_sync_worker.loop.start",
        sync_local_hour=FINTZ_SYNC_HOUR,
        sync_local_minute=FINTZ_SYNC_MINUTE,
        tz_offset=TZ_OFFSET,
        max_concurrent=MAX_CONCURRENT,
    )

    while True:
        next_run = _next_run_utc(FINTZ_SYNC_HOUR, FINTZ_SYNC_MINUTE)
        wait = _seconds_until(next_run)

        logger.info(
            "fintz_sync_worker.loop.sleeping",
            next_utc=next_run.isoformat(),
            wait_min=round(wait / 60),
        )
        await asyncio.sleep(wait)
        await fintz_sync_job()


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main() -> None:
    sys.path.insert(0, "/app/src")
    logger.info(
        "fintz_sync_worker.init",
        run_once=RUN_ONCE,
        dataset_filter=_parse_dataset_filter(),
    )
    asyncio.run(run_once() if RUN_ONCE else schedule_loop())


if __name__ == "__main__":
    main()
