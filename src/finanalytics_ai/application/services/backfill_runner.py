"""
backfill_runner — worker in-process que executa jobs criados pela aba
/admin → Backfill.

Modelo: 1 task asyncio global com asyncio.Lock — jobs do admin sao
serializados (a DLL Nelogica em :8002/collect_history e' single-threaded de
qualquer forma). Items dentro de cada job tambem sao sequenciais.

Para cada item (ticker x dia util):
  - Se NOT force_refetch e' market_history_trades ja' tem dado -> 'skip'
  - Senao chama POST :8002/collect_history (timeout 60s acoes / 300s futuros)
    -> 'ok' (status='ok') ou 'err' (HTTP error / timeout / network).
  - Atualiza backfill_job_items + counters em backfill_jobs.
  - Antes de cada item checa cancel_requested -> aborta com status='cancelled'.

Limitacao conhecida: API restart durante job deixa items 'running' orfaos.
v2: recovery via SELECT WHERE status='running' AND started_at < NOW()-10min.
"""

from __future__ import annotations

import asyncio
from datetime import date
import os
import time
from typing import Any

import httpx
import structlog

from finanalytics_ai.infrastructure.database.repositories import backfill_repo

logger = structlog.get_logger(__name__)

AGENT_URL = os.environ.get("PROFIT_AGENT_URL", "http://172.17.80.1:8002")
TIMEOUT_S = int(os.environ.get("BACKFILL_TIMEOUT_S", "60"))
TIMEOUT_FUT_S = int(os.environ.get("BACKFILL_TIMEOUT_FUT_S", "300"))
FUTURES_EXCHANGE = {"F"}

# tickers cujo nome explicito vai pra DLL com exchange='F' (futuros).
# Lista canonica e' descoberta via :8002/tickers/active, mas mantemos um
# heuristic fallback.
FUTURES_PREFIXES = ("WIN", "WDO", "IND", "DOL")


def _exchange_for_ticker(ticker: str) -> str:
    t = ticker.upper()
    if any(t.startswith(p) for p in FUTURES_PREFIXES):
        return "F"
    return "B"


def _fmt_dt(d: date, hour: str) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year} {hour}"


_LOCK = asyncio.Lock()


async def enqueue_job(job_id: int) -> None:
    """Spawna o worker em background sem bloquear o caller (FastAPI handler)."""
    asyncio.create_task(_run_job(job_id))


async def _run_job(job_id: int) -> None:
    async with _LOCK:
        try:
            await _run_job_body(job_id)
        except Exception as exc:
            logger.exception("backfill.job.crash", job_id=job_id, error=str(exc))
            try:
                await backfill_repo.mark_job_finished(job_id, status="failed")
            except Exception:
                pass


async def _run_job_body(job_id: int) -> None:
    job = await backfill_repo.get_job(job_id)
    if job is None:
        logger.warning("backfill.job.missing", job_id=job_id)
        return
    if job["status"] in ("done", "cancelled", "failed"):
        return

    await backfill_repo.mark_job_running(job_id)
    logger.info(
        "backfill.job.start",
        job_id=job_id,
        tickers=len(job["tickers"]),
        date_start=job["date_start"],
        date_end=job["date_end"],
        force_refetch=job["force_refetch"],
        total_items=job["total_items"],
    )
    force = bool(job["force_refetch"])

    timeout = max(TIMEOUT_S, TIMEOUT_FUT_S) + 30
    cancelled = False
    async with httpx.AsyncClient(timeout=timeout) as client:
        while True:
            if await backfill_repo.is_cancel_requested(job_id):
                logger.info("backfill.job.cancelled", job_id=job_id)
                await backfill_repo.mark_job_finished(job_id, status="cancelled")
                cancelled = True
                break

            item = await backfill_repo.next_pending_item(job_id)
            if item is None:
                break

            await backfill_repo.start_item(item["id"])
            await _process_item(client, job_id, item, force=force)

    if not cancelled:
        await backfill_repo.mark_job_finished(job_id, status="done")
        logger.info("backfill.job.done", job_id=job_id)

    # Pushover alert se houve erros ou cancel — operador precisa saber
    # que coleta agendada não completou full success.
    final_job = await backfill_repo.get_job(job_id)
    if final_job:
        await _maybe_alert_job_outcome(final_job, was_cancelled=cancelled)


async def _maybe_alert_job_outcome(job: dict[str, Any], *, was_cancelled: bool) -> None:
    """Dispara push (Pushover) quando job de backfill termina com falha ou
    cancelamento. Job 100% ok é silencioso (sucesso é o esperado).
    """
    err = int(job.get("err_items") or 0)
    ok = int(job.get("ok_items") or 0)
    skip = int(job.get("skip_items") or 0)
    total = int(job.get("total_items") or 0)
    done = int(job.get("done_items") or 0)
    incomplete = done < total  # cancel ou crash deixou items pending

    if not (err > 0 or was_cancelled or incomplete):
        return  # sucesso completo — silencioso

    job_id = job.get("id")
    tickers_preview = ",".join(job.get("tickers") or [])[:120]
    if was_cancelled:
        title = f"Backfill #{job_id} cancelado"
    elif err > 0 and ok == 0:
        title = f"Backfill #{job_id} FALHOU"
    elif incomplete:
        title = f"Backfill #{job_id} incompleto"
    else:
        title = f"Backfill #{job_id} c/ erros"

    msg_lines = [
        f"Range {job.get('date_start')} → {job.get('date_end')}",
        f"Tickers: {tickers_preview}",
        f"Items: ok={ok} skip={skip} err={err} done={done}/{total}",
    ]
    if incomplete:
        msg_lines.append(f"⚠ {total - done} item(s) não processado(s)")
    msg = "\n".join(msg_lines)

    critical = (err > 0 and ok == 0) or was_cancelled
    try:
        from finanalytics_ai.infrastructure.notifications.pushover import (
            notify_system,
        )
        sent = await notify_system(title=title, message=msg, critical=critical)
        logger.info(
            "backfill.job.alert_dispatched",
            job_id=job_id, sent=sent, critical=critical,
            err=err, ok=ok, skip=skip, done=done, total=total,
        )
    except Exception as exc:
        logger.warning("backfill.job.alert_failed", job_id=job_id, error=str(exc))


async def _process_item(
    client: httpx.AsyncClient,
    job_id: int,
    item: dict[str, Any],
    *,
    force: bool,
) -> None:
    ticker: str = item["ticker"]
    exchange: str = item["exchange"] or _exchange_for_ticker(ticker)
    target_date: date = item["target_date"]

    if not force and await backfill_repo.already_has_history(ticker, target_date):
        await backfill_repo.finish_item(
            item["id"], status="skip", error_msg="ja existe em market_history_trades"
        )
        return

    is_fut = exchange.upper() in FUTURES_EXCHANGE
    timeout_s = TIMEOUT_FUT_S if is_fut else TIMEOUT_S
    body = {
        "ticker": ticker,
        "exchange": exchange,
        "dt_start": _fmt_dt(target_date, "09:00:00"),
        "dt_end": _fmt_dt(target_date, "18:00:00"),
        "timeout": timeout_s,
    }
    t0 = time.time()
    try:
        resp = await client.post(
            f"{AGENT_URL}/collect_history",
            json=body,
            timeout=timeout_s + 30,
        )
        elapsed = time.time() - t0
        if resp.status_code != 200:
            await backfill_repo.finish_item(
                item["id"],
                status="err",
                elapsed_s=elapsed,
                error_msg=f"http_{resp.status_code}: {resp.text[:200]}",
            )
            return
        data = resp.json()
        if "error" in data:
            await backfill_repo.finish_item(
                item["id"],
                status="err",
                elapsed_s=elapsed,
                error_msg=str(data.get("error"))[:300],
            )
            return
        ticks = int(data.get("ticks", 0) or 0)
        inserted = int(data.get("inserted", 0) or 0)
        agent_status = str(data.get("status", "?"))
        # status='timeout' do agent -> err pq nao tem garantia de ter dado.
        final = "ok" if agent_status == "ok" else "err"
        err_msg = None if final == "ok" else f"agent_status={agent_status}"
        await backfill_repo.finish_item(
            item["id"],
            status=final,
            ticks_returned=ticks,
            inserted=inserted,
            elapsed_s=elapsed,
            error_msg=err_msg,
        )
    except httpx.TimeoutException:
        await backfill_repo.finish_item(
            item["id"],
            status="err",
            elapsed_s=time.time() - t0,
            error_msg=f"timeout (>{timeout_s + 30}s)",
        )
    except httpx.HTTPError as exc:
        await backfill_repo.finish_item(
            item["id"],
            status="err",
            elapsed_s=time.time() - t0,
            error_msg=f"http_err: {type(exc).__name__}: {str(exc)[:200]}",
        )
    except Exception as exc:
        await backfill_repo.finish_item(
            item["id"],
            status="err",
            elapsed_s=time.time() - t0,
            error_msg=f"err: {type(exc).__name__}: {str(exc)[:200]}",
        )
