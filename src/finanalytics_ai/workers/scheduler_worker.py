#!/usr/bin/env python3
"""
Scheduler Worker — Coleta diária automática de dados de mercado.

Jobs agendados (horário de Brasília, UTC-3):
  06:00  macro_job    — SELIC, IPCA, USD/BRL, EUR/BRL, IBOV, VIX, S&P500, IGP-M
  07:00  ohlcv_job    — Delta OHLCV diário de todos os tickers B3 (skip se < 3 dias)
  23:00  cleanup_job  — Poda event_records terminais (7d completed/skipped, 30d dead_letter)

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
BRAPI_SYNC_ENABLED = os.environ.get("SCHEDULER_BRAPI_SYNC_ENABLED", "true").lower() == "true"
# DSN aceita DATABASE_URL (asyncpg) ou DATABASE_DSN (psycopg2-style)
_raw_dsn = os.environ.get("DATABASE_DSN") or os.environ.get("DATABASE_URL", "")
PG_DSN_SYNC = (
    _raw_dsn
    .replace("postgresql+asyncpg://", "postgresql://")
    .replace("postgresql+psycopg2://", "postgresql://")
)

FINTZ_API_KEY = os.environ.get("FINTZ_API_KEY", "")
FINTZ_BASE_URL = os.environ.get("FINTZ_BASE_URL", "https://api.fintz.com.br")
FINTZ_BULK_HOUR = int(os.environ.get("SCHEDULER_FINTZ_BULK_HOUR", "8"))   # 08:00 local
FINTZ_BULK_ENABLED = os.environ.get("SCHEDULER_FINTZ_BULK_ENABLED", "true").lower() == "true"
PG_DSN = os.environ.get("DATABASE_DSN", os.environ.get("DATABASE_URL", ""))

OHLCV_ENABLED = os.environ.get("SCHEDULER_OHLCV_ENABLED", "true").lower() == "true"
MACRO_ENABLED = os.environ.get("SCHEDULER_MACRO_ENABLED", "true").lower() == "true"

# Cleanup de event_records (Sprint U8): remove registros antigos terminais
# para manter a tabela enxuta. Status removidos: completed, skipped (idempotente),
# dead_letter (mantido para auditoria so se DEAD_LETTER_RETENTION_DAYS=0).
CLEANUP_HOUR = int(os.environ.get("SCHEDULER_CLEANUP_HOUR", "23"))  # 23:00 local
CLEANUP_ENABLED = os.environ.get("SCHEDULER_CLEANUP_ENABLED", "true").lower() == "true"
CLEANUP_RETENTION_DAYS = int(os.environ.get("EVENT_CLEANUP_RETENTION_DAYS", "7"))
CLEANUP_DEAD_LETTER_RETENTION_DAYS = int(
    os.environ.get("EVENT_CLEANUP_DEAD_LETTER_RETENTION_DAYS", "30")
)

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

async def fintz_bulk_job() -> dict:
    """
    Atualiza ohlc_prices via download bulk Fintz.

    Fluxo (1 chamada de API):
      1. GET /bolsa/b3/avista/cotacoes/historico/arquivos  → {"link": "<url>"}
      2. Baixa o .parquet completo (todos tickers, desde 2010)
      3. Upsert incremental em ohlc_prices (apenas linhas mais novas que o MAX(date) por ticker)

    Vantagens vs fintz_sync_worker (N chamadas por ticker):
      - 1 chamada de API total → nunca atinge rate limit
      - Parquet contém precoFechamentoAjustado → adj_close disponível
      - Idempotente: ON CONFLICT DO UPDATE
    """
    if not _is_weekday():
        logger.info("scheduler.fintz_bulk_job.skip", reason="weekend")
        return {"status": "skip", "reason": "weekend"}

    if not FINTZ_API_KEY:
        logger.warning("scheduler.fintz_bulk_job.skip", reason="FINTZ_API_KEY not set")
        return {"status": "skip", "reason": "no_fintz_key"}

    if not PG_DSN:
        logger.error("scheduler.fintz_bulk_job.skip", reason="DATABASE_DSN not set")
        return {"status": "skip", "reason": "no_pg_dsn"}

    logger.info("scheduler.fintz_bulk_job.start")
    t0 = asyncio.get_event_loop().time()

    try:
        import io
        import aiohttp
        import asyncpg
        import pandas as pd

        # ── 1. Obter link de download ────────────────────────────────────────
        async with aiohttp.ClientSession(
            headers={"X-API-Key": FINTZ_API_KEY},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as session:
            async with session.get(
                f"{FINTZ_BASE_URL}/bolsa/b3/avista/cotacoes/historico/arquivos"
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                download_url = data["link"]

            logger.info("scheduler.fintz_bulk_job.download_url_ok")

            # ── 2. Baixar o arquivo Parquet ──────────────────────────────────
            async with session.get(
                download_url,
                timeout=aiohttp.ClientTimeout(total=600),  # arquivo pode ser grande
            ) as resp:
                resp.raise_for_status()
                content = await resp.read()

        logger.info(
            "scheduler.fintz_bulk_job.downloaded",
            size_mb=round(len(content) / 1_048_576, 1),
        )

        # ── 3. Ler Parquet ───────────────────────────────────────────────────
        df = pd.read_parquet(io.BytesIO(content))
        logger.info("scheduler.fintz_bulk_job.parquet_read", rows=len(df), cols=df.columns.tolist())

        # Normalizar nomes de colunas (Fintz usa camelCase)
        col_map = {
            "ticker": "ticker",
            "data": "date",
            "precoAbertura": "open",
            "precoMaximo": "high",
            "precoMinimo": "low",
            "precoFechamento": "close",
            "precoFechamentoAjustado": "adj_close",
            "volumeNegociado": "volume",
            "preco_abertura": "open",
            "preco_maximo": "high",
            "preco_minimo": "low",
            "preco_fechamento": "close",
            "preco_fechamento_ajustado": "adj_close",
            "volume_negociado": "volume",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        required = {"ticker", "date", "open", "high", "low", "close"}
        if not required.issubset(df.columns):
            missing = required - set(df.columns)
            raise ValueError(f"Colunas ausentes no Parquet: {missing}")

        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df[df["close"] > 0]

        if "adj_close" not in df.columns:
            df["adj_close"] = None
        if "volume" not in df.columns:
            df["volume"] = None

        # ── 4. Upsert incremental em ohlc_prices via asyncpg ────────────────
        dsn = (
            PG_DSN
            .replace("postgresql+asyncpg://", "postgresql://")
            .replace("postgresql+psycopg2://", "postgresql://")
        )
        conn = await asyncpg.connect(dsn)

        # MAX(date) por ticker — inserir apenas delta
        rows_max = await conn.fetch("SELECT ticker, MAX(date) FROM ohlc_prices GROUP BY ticker")
        max_dates: dict = {row[0]: row[1] for row in rows_max}

        BATCH = 2000
        total_rows = 0
        tickers_updated = 0

        for ticker, grp in df.groupby("ticker"):
            max_date = max_dates.get(ticker)
            if max_date:
                grp = grp[grp["date"] > max_date]
            if grp.empty:
                continue

            records = [
                (
                    ticker,
                    row["date"],
                    float(row["open"])      if pd.notna(row["open"])      else None,
                    float(row["high"])      if pd.notna(row["high"])      else None,
                    float(row["low"])       if pd.notna(row["low"])       else None,
                    float(row["close"])     if pd.notna(row["close"])     else None,
                    float(row["adj_close"]) if pd.notna(row.get("adj_close")) else None,
                    float(row["volume"])    if pd.notna(row.get("volume")) else None,
                )
                for _, row in grp.iterrows()
            ]

            sql = """
                INSERT INTO ohlc_prices (ticker, date, open, high, low, close, adj_close, volume)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (ticker, date) DO UPDATE SET
                    open      = EXCLUDED.open,
                    high      = EXCLUDED.high,
                    low       = EXCLUDED.low,
                    close     = EXCLUDED.close,
                    adj_close = EXCLUDED.adj_close,
                    volume    = EXCLUDED.volume
            """
            for i in range(0, len(records), BATCH):
                await conn.executemany(sql, records[i:i+BATCH])

            total_rows += len(records)
            tickers_updated += 1

        await conn.close()

        elapsed = round(asyncio.get_event_loop().time() - t0)
        logger.info(
            "scheduler.fintz_bulk_job.done",
            tickers_updated=tickers_updated,
            rows_inserted=total_rows,
            elapsed_s=elapsed,
        )
        return {"status": "ok", "tickers": tickers_updated, "rows": total_rows}

    except Exception as exc:
        logger.error("scheduler.fintz_bulk_job.failed", error=str(exc), exc_info=True)
        return {"status": "error", "error": str(exc)}


async def brapi_sync_job() -> dict:
    """
    Sincroniza Parquets BRAPI (/data/ohlcv) → ohlc_prices.

    Roda após ohlcv_job: os Parquets já estão frescos (coletados às 07:00).
    1 chamada de API zero — lê apenas arquivos locais.
    Idempotente: insere apenas linhas com date > MAX(date) por ticker.
    """
    if not _is_weekday():
        logger.info("scheduler.brapi_sync_job.skip", reason="weekend")
        return {"status": "skip", "reason": "weekend"}

    if not PG_DSN_SYNC:
        logger.error("scheduler.brapi_sync_job.skip", reason="DATABASE_URL not set")
        return {"status": "skip", "reason": "no_db_dsn"}

    logger.info("scheduler.brapi_sync_job.start")
    t0 = asyncio.get_event_loop().time()

    try:
        import glob
        import asyncpg
        import pandas as pd

        ohlcv_dir = DATA_DIR + "/ohlcv"
        conn = await asyncpg.connect(PG_DSN_SYNC)

        rows_max = await conn.fetch("SELECT ticker, MAX(date) FROM ohlc_prices GROUP BY ticker")
        max_dates: dict = {r[0]: r[1] for r in rows_max}

        tickers = sorted(
            d for d in os.listdir(ohlcv_dir)
            if os.path.isdir(os.path.join(ohlcv_dir, d))
        )

        sql = """
            INSERT INTO ohlc_prices (ticker, date, open, high, low, close, adj_close, volume)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (ticker, date) DO UPDATE SET
                open      = EXCLUDED.open,
                high      = EXCLUDED.high,
                low       = EXCLUDED.low,
                close     = EXCLUDED.close,
                adj_close = EXCLUDED.adj_close,
                volume    = EXCLUDED.volume
        """

        BATCH = 2000
        total_rows = total_tickers = total_errors = 0

        for ticker in tickers:
            files = glob.glob(f"{ohlcv_dir}/{ticker}/*.parquet")
            if not files:
                continue
            try:
                df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
                df["date"] = pd.to_datetime(df["date"]).dt.date
                max_date = max_dates.get(ticker)
                if max_date:
                    df = df[df["date"] > max_date]
                df = df[df["close"] > 0]
                if df.empty:
                    continue

                records = [
                    (
                        ticker,
                        row["date"],
                        float(row["open"])   if pd.notna(row["open"])   else None,
                        float(row["high"])   if pd.notna(row["high"])   else None,
                        float(row["low"])    if pd.notna(row["low"])    else None,
                        float(row["close"])  if pd.notna(row["close"])  else None,
                        None,
                        float(row["volume"]) if pd.notna(row["volume"]) else None,
                    )
                    for _, row in df.iterrows()
                ]
                for i in range(0, len(records), BATCH):
                    await conn.executemany(sql, records[i:i + BATCH])
                total_rows += len(records)
                total_tickers += 1
            except Exception as e:
                logger.warning("scheduler.brapi_sync_job.ticker_error", ticker=ticker, error=str(e))
                total_errors += 1

        await conn.close()
        elapsed = round(asyncio.get_event_loop().time() - t0)
        logger.info(
            "scheduler.brapi_sync_job.done",
            tickers_updated=total_tickers,
            rows_inserted=total_rows,
            errors=total_errors,
            elapsed_s=elapsed,
        )
        return {"status": "ok", "tickers": total_tickers, "rows": total_rows, "errors": total_errors}

    except Exception as exc:
        logger.error("scheduler.brapi_sync_job.failed", error=str(exc), exc_info=True)
        return {"status": "error", "error": str(exc)}


async def cleanup_event_records_job() -> dict:
    """
    Sprint U8: poda event_records terminais para evitar inchaco da tabela.

    - status in ('completed', 'skipped'): retencao = EVENT_CLEANUP_RETENTION_DAYS (default 7d).
    - status = 'dead_letter': retencao = EVENT_CLEANUP_DEAD_LETTER_RETENTION_DAYS
      (default 30d; pode ser >> que o resto pq dead_letter precisa de inspecao manual).
    - status = 'failed' / 'pending' / 'processing': preservados (em vias de retry).

    Idempotente: repetir o DELETE em janelas de tempo no-op.
    """
    if not PG_DSN_SYNC:
        logger.error("scheduler.cleanup.skip", reason="DATABASE_URL not set")
        return {"status": "skip", "reason": "no_db_dsn"}

    logger.info(
        "scheduler.cleanup.start",
        retention_days=CLEANUP_RETENTION_DAYS,
        dead_letter_retention_days=CLEANUP_DEAD_LETTER_RETENTION_DAYS,
    )
    try:
        import asyncpg

        conn = await asyncpg.connect(PG_DSN_SYNC)
        try:
            # 1) Estados terminais "limpos": completed + skipped
            terminal_result = await conn.execute(
                """
                DELETE FROM event_records
                 WHERE status IN ('completed', 'skipped')
                   AND created_at < NOW() - ($1::int * INTERVAL '1 day')
                """,
                CLEANUP_RETENTION_DAYS,
            )
            terminal_deleted = int(terminal_result.split()[-1]) if terminal_result else 0

            # 2) dead_letter (retention maior — auditoria)
            dl_deleted = 0
            if CLEANUP_DEAD_LETTER_RETENTION_DAYS > 0:
                dl_result = await conn.execute(
                    """
                    DELETE FROM event_records
                     WHERE status = 'dead_letter'
                       AND created_at < NOW() - ($1::int * INTERVAL '1 day')
                    """,
                    CLEANUP_DEAD_LETTER_RETENTION_DAYS,
                )
                dl_deleted = int(dl_result.split()[-1]) if dl_result else 0
        finally:
            await conn.close()

        logger.info(
            "scheduler.cleanup.done",
            terminal_deleted=terminal_deleted,
            dead_letter_deleted=dl_deleted,
        )
        return {
            "status": "ok",
            "terminal_deleted": terminal_deleted,
            "dead_letter_deleted": dl_deleted,
        }
    except Exception as exc:
        logger.error("scheduler.cleanup.failed", error=str(exc), exc_info=True)
        return {"status": "error", "error": str(exc)}


async def run_once() -> None:
    """Executa ambos os jobs uma única vez e sai."""
    logger.info(
        "scheduler.run_once.start",
        macro=MACRO_ENABLED,
        ohlcv=OHLCV_ENABLED,
        fintz_bulk=FINTZ_BULK_ENABLED,
        brapi_sync=BRAPI_SYNC_ENABLED,
        cleanup=CLEANUP_ENABLED,
    )
    if MACRO_ENABLED:
        await macro_job()
    if OHLCV_ENABLED:
        await ohlcv_job()
    if FINTZ_BULK_ENABLED:
        await fintz_bulk_job()
    if BRAPI_SYNC_ENABLED:
        await brapi_sync_job()
    if CLEANUP_ENABLED:
        await cleanup_event_records_job()
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
            if BRAPI_SYNC_ENABLED:
                await brapi_sync_job()

    async def cleanup_loop() -> None:
        while True:
            next_run = _next_run_utc(CLEANUP_HOUR)
            wait = _seconds_until(next_run)
            logger.info(
                "scheduler.cleanup.next",
                next_utc=next_run.isoformat(),
                wait_min=round(wait / 60),
            )
            await asyncio.sleep(wait)
            await cleanup_event_records_job()

    tasks: list[asyncio.Task[None]] = []
    if MACRO_ENABLED:
        tasks.append(asyncio.create_task(macro_loop()))
    if OHLCV_ENABLED:
        tasks.append(asyncio.create_task(ohlcv_loop()))
    if CLEANUP_ENABLED:
        tasks.append(asyncio.create_task(cleanup_loop()))

    if not tasks:
        logger.warning("scheduler.loop.no_jobs", hint="Set SCHEDULER_MACRO_ENABLED or SCHEDULER_OHLCV_ENABLED or SCHEDULER_CLEANUP_ENABLED=true")
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
