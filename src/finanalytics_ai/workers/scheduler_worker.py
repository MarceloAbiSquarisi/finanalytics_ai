#!/usr/bin/env python3
"""
Scheduler Worker — Coleta diária automática de dados de mercado.

Jobs agendados (horário de Brasília, UTC-3):
  06:00  macro_job   — SELIC, IPCA, USD/BRL, EUR/BRL, IBOV, VIX, S&P500, IGP-M
  07:00  ohlcv_job   — Delta OHLCV diário de todos os tickers B3 (skip se < 3 dias)

Design:
  - asyncio puro, sem frameworks de scheduler (consistente com ticker_refresh_worker)
  - Idempotente: collect_all(force=False) já pula tickers com dados frescos
  - Resiliente: cada job captura exceções e loga sem derrubar o loop
  - Observabilidade: structlog + Prometheus counter hooks
  - Configurável via env: SCHEDULER_MACRO_HOUR, SCHEDULER_OHLCV_HOUR, SCHEDULER_TZ_OFFSET
  - RUN_ONCE=true para execução pontual (útil em CI e first-run)
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

# ── Configuração via env ──────────────────────────────────────────────────────
DATA_DIR = os.environ.get("DATA_DIR", "/data")
BRAPI_TOKEN = os.environ.get("BRAPI_TOKEN", "")
MACRO_HOUR = int(os.environ.get("SCHEDULER_MACRO_HOUR", "6"))   # 06:00 local
OHLCV_HOUR = int(os.environ.get("SCHEDULER_OHLCV_HOUR", "7"))   # 07:00 local
TZ_OFFSET = int(os.environ.get("SCHEDULER_TZ_OFFSET", "-3"))    # UTC-3 Brasília
RUN_ONCE = os.environ.get("RUN_ONCE", "false").lower() == "true"
OHLCV_ENABLED = os.environ.get("SCHEDULER_OHLCV_ENABLED", "true").lower() == "true"
MACRO_ENABLED = os.environ.get("SCHEDULER_MACRO_ENABLED", "true").lower() == "true"

# ── Logger ────────────────────────────────────────────────────────────────────
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(colors=False),
    ]
)
logger = structlog.get_logger("scheduler_worker")


# ── Helpers de tempo ──────────────────────────────────────────────────────────

def _next_run_utc(local_hour: int, tz_offset: int = TZ_OFFSET) -> datetime:
    """Calcula próxima execução em UTC dado um horário local (e.g. 06:00 BRT = 09:00 UTC)."""
    now_utc = datetime.now(UTC)
    utc_hour = (local_hour - tz_offset) % 24   # converte para UTC
    next_run = now_utc.replace(hour=utc_hour, minute=0, second=0, microsecond=0)
    if next_run <= now_utc:
        next_run += timedelta(days=1)
    return next_run


def _seconds_until(target: datetime) -> float:
    return max(0.0, (target - datetime.now(UTC)).total_seconds())


def _is_weekday() -> bool:
    """Retorna True se hoje é dia útil (segunda a sexta). Sábado/domingo pula OHLCV."""
    return datetime.now(UTC).weekday() < 5  # 0=Mon … 4=Fri


# ── Jobs ──────────────────────────────────────────────────────────────────────

async def macro_job() -> dict[str, Any]:
    """Coleta todas as séries macro e persiste no data lake."""
    logger.info("scheduler.macro_job.start")
    try:
        from finanalytics_ai.infrastructure.storage.data_storage_service import get_storage
        from finanalytics_ai.infrastructure.storage.macro_collector import MacroCollector

        storage = get_storage(DATA_DIR)
        collector = MacroCollector(storage=storage)
        result = await collector.collect_all()

        ok = sum(1 for v in result.values() if v.get("status") == "ok")
        errors = sum(1 for v in result.values() if v.get("status") == "error")
        logger.info("scheduler.macro_job.done", ok=ok, errors=errors)
        return {"status": "ok", "ok": ok, "errors": errors}

    except Exception as exc:
        logger.error("scheduler.macro_job.failed", error=str(exc), exc_info=True)
        return {"status": "error", "error": str(exc)}


async def ohlcv_job() -> dict[str, Any]:
    """
    Coleta delta OHLCV diário para todos os tickers B3.

    Idempotente: HistoricalCollector.collect_all(force=False) pula tickers
    cujo dado mais recente tem menos de 3 dias — sem duplas inserções.
    """
    if not _is_weekday():
        logger.info("scheduler.ohlcv_job.skip", reason="weekend")
        return {"status": "skip", "reason": "weekend"}

    if not BRAPI_TOKEN:
        logger.warning("scheduler.ohlcv_job.skip", reason="BRAPI_TOKEN not set")
        return {"status": "skip", "reason": "no_brapi_token"}

    logger.info("scheduler.ohlcv_job.start")
    try:
        from finanalytics_ai.infrastructure.storage.data_storage_service import get_storage
        from finanalytics_ai.infrastructure.storage.historical_collector import HistoricalCollector

        storage = get_storage(DATA_DIR)
        collector = HistoricalCollector(brapi_token=BRAPI_TOKEN, storage=storage)
        result = await collector.collect_all(force=False)  # idempotente

        summary = result.get("summary", {})
        logger.info(
            "scheduler.ohlcv_job.done",
            ok=summary.get("ok", 0),
            skip=summary.get("skip", 0),
            errors=summary.get("errors", 0),
            elapsed_min=summary.get("elapsed_seconds", 0) // 60,
        )
        return {"status": "ok", **summary}

    except Exception as exc:
        logger.error("scheduler.ohlcv_job.failed", error=str(exc), exc_info=True)
        return {"status": "error", "error": str(exc)}


# ── Run-once (first-run / CI) ─────────────────────────────────────────────────

async def run_once() -> None:
    """Executa ambos os jobs uma única vez e sai."""
    logger.info("scheduler.run_once.start", macro=MACRO_ENABLED, ohlcv=OHLCV_ENABLED)
    if MACRO_ENABLED:
        await macro_job()
    if OHLCV_ENABLED:
        await ohlcv_job()
    logger.info("scheduler.run_once.done")


# ── Schedule loop ─────────────────────────────────────────────────────────────

async def schedule_loop() -> None:
    """
    Loop principal.

    Mantém dois "relógios" independentes — macro e ohlcv — cada um dormindo
    até seu próximo horário agendado. Falhas em um job não bloqueiam o outro.

    Por que asyncio.sleep em vez de APScheduler?
    - Zero dependências extras
    - Mesma abordagem do ticker_refresh_worker (consistência)
    - Lógica trivial: próximo disparo = amanhã no mesmo horário
    - APScheduler valeria a pena se houvesse >5 jobs com cron complexo
    """
    logger.info(
        "scheduler.loop.start",
        macro_local_hour=MACRO_HOUR,
        ohlcv_local_hour=OHLCV_HOUR,
        tz_offset=TZ_OFFSET,
        macro_enabled=MACRO_ENABLED,
        ohlcv_enabled=OHLCV_ENABLED,
    )

    async def macro_loop() -> None:
        while True:
            next_run = _next_run_utc(MACRO_HOUR)
            wait = _seconds_until(next_run)
            logger.info(
                "scheduler.macro.next",
                next_utc=next_run.isoformat(),
                wait_min=round(wait / 60),
            )
            await asyncio.sleep(wait)
            await macro_job()

    async def ohlcv_loop() -> None:
        while True:
            next_run = _next_run_utc(OHLCV_HOUR)
            wait = _seconds_until(next_run)
            logger.info(
                "scheduler.ohlcv.next",
                next_utc=next_run.isoformat(),
                wait_min=round(wait / 60),
            )
            await asyncio.sleep(wait)
            await ohlcv_job()

    tasks: list[asyncio.Task[None]] = []
    if MACRO_ENABLED:
        tasks.append(asyncio.create_task(macro_loop()))
    if OHLCV_ENABLED:
        tasks.append(asyncio.create_task(ohlcv_loop()))

    if not tasks:
        logger.warning("scheduler.loop.no_jobs", hint="Set SCHEDULER_MACRO_ENABLED or SCHEDULER_OHLCV_ENABLED=true")
        return

    await asyncio.gather(*tasks)


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main() -> None:
    sys.path.insert(0, "/app/src")
    logger.info(
        "scheduler_worker.init",
        data_dir=DATA_DIR,
        run_once=RUN_ONCE,
        brapi_token_set=bool(BRAPI_TOKEN),
    )
    asyncio.run(run_once() if RUN_ONCE else schedule_loop())


if __name__ == "__main__":
    main()
