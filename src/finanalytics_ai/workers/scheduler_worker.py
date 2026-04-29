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
from datetime import UTC, datetime, timedelta
import os
import sys
from typing import Any

import structlog

# ── Configuração via env ──────────────────────────────────────────────────────
DATA_DIR = os.environ.get("DATA_DIR", "/data")
BRAPI_TOKEN = os.environ.get("BRAPI_TOKEN", "")
MACRO_HOUR = int(os.environ.get("SCHEDULER_MACRO_HOUR", "6"))  # 06:00 local
OHLCV_HOUR = int(os.environ.get("SCHEDULER_OHLCV_HOUR", "7"))  # 07:00 local
TZ_OFFSET = int(os.environ.get("SCHEDULER_TZ_OFFSET", "-3"))  # UTC-3 Brasília
RUN_ONCE = os.environ.get("RUN_ONCE", "false").lower() == "true"
BRAPI_SYNC_ENABLED = os.environ.get("SCHEDULER_BRAPI_SYNC_ENABLED", "true").lower() == "true"
# DSN aceita DATABASE_URL (asyncpg) ou DATABASE_DSN (psycopg2-style)
_raw_dsn = os.environ.get("DATABASE_DSN") or os.environ.get("DATABASE_URL", "")
PG_DSN_SYNC = _raw_dsn.replace("postgresql+asyncpg://", "postgresql://").replace(
    "postgresql+psycopg2://", "postgresql://"
)

FINTZ_API_KEY = os.environ.get("FINTZ_API_KEY", "")
FINTZ_BASE_URL = os.environ.get("FINTZ_BASE_URL", "https://api.fintz.com.br")
FINTZ_BULK_HOUR = int(os.environ.get("SCHEDULER_FINTZ_BULK_HOUR", "8"))  # 08:00 local
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

# V4 (21/abr/2026): reconciliacao automatica posicoes DLL <-> DB.
# Roda a cada N minutos dentro da janela de pregao (10h-18h BRT).
# Chama GET /positions/dll no profit_agent — handler ja faz o UPDATE
# em profit_orders quando ordens DLL diferem do DB.
RECONCILE_ENABLED = os.environ.get("SCHEDULER_RECONCILE_ENABLED", "true").lower() == "true"
RECONCILE_START_HOUR = int(os.environ.get("SCHEDULER_RECONCILE_START_HOUR", "10"))  # 10:00 BRT
RECONCILE_END_HOUR = int(os.environ.get("SCHEDULER_RECONCILE_END_HOUR", "18"))  # 18:00 BRT
RECONCILE_INTERVAL_MIN = int(os.environ.get("SCHEDULER_RECONCILE_INTERVAL_MIN", "5"))
PROFIT_AGENT_URL = os.environ.get("PROFIT_AGENT_URL", "http://host.docker.internal:8002")

# F (Sprint Fix Alerts 21/abr): expor /metrics em :9102 para Prometheus
# scrape — destrava alert rules de scheduler_reconcile_errors. Porta
# interna do docker network; nao precisa expose: para o host. Setar
# SCHEDULER_METRICS_PORT=0 desabilita o servidor HTTP.
METRICS_PORT = int(os.environ.get("SCHEDULER_METRICS_PORT", "9102"))

# N2 (27/abr/2026): sync mensal CVM informe diario. Roda 1x/dia, mas
# so executa o sync_informe_diario quando hoje == CVM_INFORME_DAY (default 5
# do mes — CVM publica o ZIP do mes anterior por volta do dia 3-4).
# Competencia calculada = mes anterior em formato AAAAMM.
CVM_INFORME_ENABLED = os.environ.get("SCHEDULER_CVM_INFORME_ENABLED", "true").lower() == "true"
CVM_INFORME_DAY = int(os.environ.get("SCHEDULER_CVM_INFORME_DAY", "5"))
CVM_INFORME_HOUR = int(os.environ.get("SCHEDULER_CVM_INFORME_HOUR", "9"))  # 09:00 BRT

# N5 (27/abr/2026): refresh diario de fundamentals FII (DY, P/VP, etc) via
# scraper Status Invest. Roda 1x/dia em FII_FUND_HOUR (default 7h BRT,
# antes do pregao). Idempotente por (ticker, snapshot_date) — re-rodar
# faz UPSERT do dia. Skip em weekend (Status Invest nao atualiza no fim de semana).
FII_FUND_ENABLED = os.environ.get("SCHEDULER_FII_FUND_ENABLED", "true").lower() == "true"
FII_FUND_HOUR = int(os.environ.get("SCHEDULER_FII_FUND_HOUR", "7"))  # 07:00 BRT

# N11b (28/abr/2026): refresh diario das daily bars Yahoo para FIIs+ETFs em
# profit_daily_bars. Mantem fetch_candles cobertura para tickers fora DLL.
# Roda em YAHOO_BARS_HOUR (default 7:30h BRT). Idempotente via ON CONFLICT.
YAHOO_BARS_ENABLED = os.environ.get("SCHEDULER_YAHOO_BARS_ENABLED", "true").lower() == "true"
# Cleanup ordens pending stale (28/abr): roda 23h BRT, cancela orders pending >24h
STALE_PENDING_ENABLED = os.environ.get(
    "SCHEDULER_STALE_PENDING_ENABLED", "true"
).lower() == "true"
STALE_PENDING_HOUR = int(os.environ.get("SCHEDULER_STALE_PENDING_HOUR", "23"))
YAHOO_BARS_HOUR = int(os.environ.get("SCHEDULER_YAHOO_BARS_HOUR", "8"))  # 08:00 BRT (depois do FII_FUND)

# N6 (28/abr/2026): snapshot diario de crypto signals (BTC/ETH/SOL/etc) para
# crypto_signals_history. Roda 1x/dia em CRYPTO_SIGNALS_HOUR (default 9h BRT).
# Crypto roda 24/7, nao tem skip de weekend. Idempotente via PK.
CRYPTO_SIGNALS_ENABLED = os.environ.get("SCHEDULER_CRYPTO_SIGNALS_ENABLED", "true").lower() == "true"
CRYPTO_SIGNALS_HOUR = int(os.environ.get("SCHEDULER_CRYPTO_SIGNALS_HOUR", "9"))  # 09:00 BRT

# Snapshot diario de /api/v1/ml/signals → signal_history (29/abr/2026: re-ativado).
# Roda apos pregao fechar (default 19h BRT, 2h margem sobre close 17h).
SNAPSHOT_SIGNALS_ENABLED = os.environ.get("SCHEDULER_SNAPSHOT_SIGNALS_ENABLED", "true").lower() == "true"
SNAPSHOT_SIGNALS_HOUR = int(os.environ.get("SCHEDULER_SNAPSHOT_SIGNALS_HOUR", "19"))  # 19:00 BRT

# ── Logger ────────────────────────────────────────────────────────────────────
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(colors=False),
    ]
)
logger = structlog.get_logger("scheduler_worker")


# ── Prometheus metrics (Sprint Fix Alerts F, 21/abr) ─────────────────────────
# Counters por job + status para alertar em Grafana. Servidor HTTP
# iniciado em start() abaixo se METRICS_PORT > 0.

try:
    from prometheus_client import Counter, start_http_server  # type: ignore[import-not-found]

    _PROM_AVAILABLE = True
except ImportError:
    _PROM_AVAILABLE = False
    Counter = None  # type: ignore[assignment,misc]

if _PROM_AVAILABLE:
    scheduler_job_runs_total = Counter(
        "scheduler_job_runs_total",
        "Total de execucoes de jobs do scheduler por status",
        labelnames=[
            "job",
            "status",
        ],  # job: macro/ohlcv/cleanup/reconcile/etc; status: ok/error/skip
    )
    scheduler_reconcile_errors_total = Counter(
        "scheduler_reconcile_errors_total",
        "Total de falhas no reconcile_loop (HTTP errors no profit_agent)",
    )
else:
    scheduler_job_runs_total = None  # type: ignore[assignment]
    scheduler_reconcile_errors_total = None  # type: ignore[assignment]


def _record(job: str, status: str) -> None:
    """Incrementa counter de job runs (no-op se prometheus_client ausente)."""
    if scheduler_job_runs_total is not None:
        try:
            scheduler_job_runs_total.labels(job=job, status=status).inc()
        except Exception:
            pass


# ── Helpers de tempo ──────────────────────────────────────────────────────────


def _next_run_utc(local_hour: int, tz_offset: int = TZ_OFFSET) -> datetime:
    """Calcula próxima execução em UTC dado um horário local (e.g. 06:00 BRT = 09:00 UTC)."""
    now_utc = datetime.now(UTC)
    utc_hour = (local_hour - tz_offset) % 24  # converte para UTC
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
        dsn = PG_DSN.replace("postgresql+asyncpg://", "postgresql://").replace(
            "postgresql+psycopg2://", "postgresql://"
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
                    float(row["open"]) if pd.notna(row["open"]) else None,
                    float(row["high"]) if pd.notna(row["high"]) else None,
                    float(row["low"]) if pd.notna(row["low"]) else None,
                    float(row["close"]) if pd.notna(row["close"]) else None,
                    float(row["adj_close"]) if pd.notna(row.get("adj_close")) else None,
                    float(row["volume"]) if pd.notna(row.get("volume")) else None,
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
                await conn.executemany(sql, records[i : i + BATCH])

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
            d for d in os.listdir(ohlcv_dir) if os.path.isdir(os.path.join(ohlcv_dir, d))
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
                        float(row["open"]) if pd.notna(row["open"]) else None,
                        float(row["high"]) if pd.notna(row["high"]) else None,
                        float(row["low"]) if pd.notna(row["low"]) else None,
                        float(row["close"]) if pd.notna(row["close"]) else None,
                        None,
                        float(row["volume"]) if pd.notna(row["volume"]) else None,
                    )
                    for _, row in df.iterrows()
                ]
                for i in range(0, len(records), BATCH):
                    await conn.executemany(sql, records[i : i + BATCH])
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
        return {
            "status": "ok",
            "tickers": total_tickers,
            "rows": total_rows,
            "errors": total_errors,
        }

    except Exception as exc:
        logger.error("scheduler.brapi_sync_job.failed", error=str(exc), exc_info=True)
        return {"status": "error", "error": str(exc)}


async def tick_to_ohlc_backfill_job(date_iso: str | None = None) -> dict:
    """Agrega profit_ticks → ohlc_1m via continuous aggregate ohlc_1m_from_ticks.

    Compensa BRAPI ingestor stale: o tick_agg_v1 já gera bars 1min via
    continuous aggregate, mas o ohlc_1m fisica pode ter rows desatualizadas
    pra equity quando BRAPI não atualiza. INSERT com source='tick_agg_v1'
    + ON CONFLICT DO NOTHING preserva rows do BRAPI quando existirem.

    Roda 1x/dia ~21h BRT (após close pregão + after-market). Idempotente.
    """
    timescale_dsn = (
        os.environ.get("TIMESCALE_URL")
        or os.environ.get("PROFIT_TIMESCALE_DSN")
        or os.environ.get("TIMESCALE_DSN", "")
    )
    if not timescale_dsn:
        logger.warning("scheduler.tick_to_ohlc.skip", reason="no_timescale_dsn")
        return {"status": "skip", "reason": "no_dsn"}
    ts_dsn = timescale_dsn.replace("postgresql+asyncpg://", "postgresql://")

    target_date = date_iso or datetime.now(UTC).strftime("%Y-%m-%d")
    logger.info("scheduler.tick_to_ohlc.start", date=target_date)
    try:
        import asyncpg
        conn = await asyncpg.connect(ts_dsn)
        try:
            result = await conn.execute(
                f"""
                INSERT INTO ohlc_1m (time, ticker, open, high, low, close, volume, trades, source)
                SELECT time, ticker, open, high, low, close, volume, COALESCE(trades, 0),
                       'tick_agg_v1'
                FROM ohlc_1m_from_ticks
                WHERE time >= '{target_date} 00:00:00+00'
                  AND time < ('{target_date}'::date + INTERVAL '1 day')
                ON CONFLICT (time, ticker) DO NOTHING
                """
            )
            inserted = int(result.split()[-1]) if result.startswith("INSERT") else 0
            logger.info("scheduler.tick_to_ohlc.done", date=target_date, inserted=inserted)
            _record("tick_to_ohlc", "ok")
            return {"status": "ok", "date": target_date, "inserted": inserted}
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("scheduler.tick_to_ohlc.failed", error=str(exc), date=target_date)
        _record("tick_to_ohlc", "error")
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


async def cleanup_stale_pending_orders_job() -> dict:
    """Cancela ordens em status pending (0=New, 10=PendingNew) há mais de N horas
    em `profit_orders`. Roda 1x/dia (default 23h BRT, após pregão).

    Estratégia conservadora:
      1. Lê ordens DB com status pending + created_at < NOW() - STALE_HOURS
      2. Cruza com DLL via /positions/dll — separar:
         a) Ordens ainda ativas no DLL → tenta /order/cancel
         b) Ordens NÃO no DLL (broker já dropou) → UPDATE status='8' (Rejected stale)
      3. Idempotente: ordens já marcadas não voltam.

    Mitiga acúmulo de "49 PETR4 pending" residuais que entulham /positions/dll
    e caixa flatten do dashboard.
    """
    timescale_dsn = (
        os.environ.get("TIMESCALE_URL")
        or os.environ.get("PROFIT_TIMESCALE_DSN")
        or os.environ.get("TIMESCALE_DSN", "")
    )
    if not timescale_dsn:
        logger.error("scheduler.stale_pending.skip", reason="no_timescale_dsn")
        return {"status": "skip", "reason": "no_timescale_dsn"}

    stale_hours = int(os.environ.get("PROFIT_STALE_PENDING_HOURS", "24"))
    agent_url = os.environ.get("PROFIT_AGENT_URL", "http://host.docker.internal:8002")

    logger.info("scheduler.stale_pending.start", stale_hours=stale_hours)
    try:
        import asyncpg
        import httpx

        # 1) DB: ordens stale
        ts_dsn = timescale_dsn.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(ts_dsn)
        try:
            # Junta dois universos:
            # 1) Stale (>24h sem update) — sweep de seguranca
            # 2) GTD expirado (validity_date < NOW e ainda pending) — enforcement
            #    do Time In Force escolhido pelo user no envio.
            stale_rows = await conn.fetch(
                """
                SELECT local_order_id, ticker, env, order_status, created_at,
                       validity_type, validity_date
                  FROM profit_orders
                 WHERE order_status IN (0, 10)
                   AND (
                         created_at < NOW() - ($1::int * INTERVAL '1 hour')
                      OR (validity_type = 'GTD' AND validity_date IS NOT NULL
                          AND validity_date < NOW())
                       )
                 ORDER BY created_at ASC
                 LIMIT 500
                """,
                stale_hours,
            )
        finally:
            await conn.close()

        if not stale_rows:
            logger.info("scheduler.stale_pending.no_orders")
            return {"status": "ok", "stale_found": 0, "cancelled_dll": 0, "marked_db": 0}

        # 2) DLL: lista ativas
        active_dll_ids = set()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{agent_url}/positions/dll?env=simulation")
                if resp.status_code == 200:
                    for o in (resp.json() or {}).get("orders", []):
                        if o.get("order_status") in (0, 10):
                            active_dll_ids.add(int(o.get("local_id", 0)))
        except Exception as exc:
            logger.warning("scheduler.stale_pending.dll_query_failed", error=str(exc))

        cancelled_dll = 0
        marked_db = 0

        # 3) Process each
        async with httpx.AsyncClient(timeout=5.0) as client:
            conn = await asyncpg.connect(ts_dsn)
            try:
                for row in stale_rows:
                    lid = int(row["local_order_id"])
                    env = row["env"] or "simulation"
                    if lid in active_dll_ids:
                        # Ainda no DLL — tenta cancel via agent
                        try:
                            r = await client.post(
                                f"{agent_url}/order/cancel",
                                json={"local_order_id": lid, "env": env},
                            )
                            if r.status_code == 200 and r.json().get("ok"):
                                cancelled_dll += 1
                            else:
                                # Cancel falhou (broker rejeitou) — marca como stale
                                await conn.execute(
                                    "UPDATE profit_orders SET order_status=8, "
                                    "error_message='cleanup_stale: cancel rejected', "
                                    "updated_at=NOW() WHERE local_order_id=$1",
                                    lid,
                                )
                                marked_db += 1
                        except Exception:
                            await conn.execute(
                                "UPDATE profit_orders SET order_status=8, "
                                "error_message='cleanup_stale: cancel exception', "
                                "updated_at=NOW() WHERE local_order_id=$1",
                                lid,
                            )
                            marked_db += 1
                    else:
                        # Não está no DLL — broker já dropou. Marca DB.
                        await conn.execute(
                            "UPDATE profit_orders SET order_status=8, "
                            "error_message='cleanup_stale: not in DLL', "
                            "updated_at=NOW() WHERE local_order_id=$1",
                            lid,
                        )
                        marked_db += 1
            finally:
                await conn.close()

        logger.info(
            "scheduler.stale_pending.done",
            stale_found=len(stale_rows),
            cancelled_dll=cancelled_dll,
            marked_db=marked_db,
        )
        return {
            "status": "ok",
            "stale_found": len(stale_rows),
            "cancelled_dll": cancelled_dll,
            "marked_db": marked_db,
        }
    except Exception as exc:
        logger.error("scheduler.stale_pending.failed", error=str(exc), exc_info=True)
        return {"status": "error", "error": str(exc)}


async def settle_cash_transactions_job() -> dict[str, Any]:
    """Feature C (24/abr): liquida transacoes pending com settlement_date <= hoje.

    - trade_buy / trade_sell → D+1 (B3 T+1)
    - rf_redeem → D+liquidity_days do titulo
    - Outras pendentes criadas manualmente

    Atualiza account_transactions.status='settled' + cash_balance da conta.
    Idempotente (re-rodar nao muda nada — tx ja settled e skip).
    """
    try:
        from finanalytics_ai.infrastructure.database.repositories.wallet_repo import WalletRepository
        from datetime import date as _date

        repo = WalletRepository()
        count = await repo.settle_due_transactions(_date.today())
        logger.info("scheduler.settle_cash.done", settled=count)
        _record("settle_cash", "ok")
        return {"status": "ok", "settled": count, "date": _date.today().isoformat()}
    except Exception as exc:  # noqa: BLE001
        logger.error("scheduler.settle_cash.failed", error=str(exc), exc_info=True)
        _record("settle_cash", "error")
        return {"status": "error", "error": str(exc)}


async def crypto_signals_snapshot_job() -> dict[str, Any]:
    """N6 (28/abr): snapshot diario de signals de crypto via API local.

    Chama scripts/snapshot_crypto_signals.py que atinge /api/v1/crypto/signal/{sym}
    e persiste em crypto_signals_history. Idempotente.
    """
    logger.info("scheduler.crypto_signals.start")
    try:
        proc = await asyncio.create_subprocess_exec(
            "python",
            "/app/scripts/snapshot_crypto_signals.py",
            "--rate-limit", "2.0",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={
                **os.environ,
                "FINANALYTICS_API_BASE": os.environ.get("FINANALYTICS_API_BASE", "http://api:8000"),
            },
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
        rc = proc.returncode
        last_lines = stdout.decode("utf-8", errors="replace").splitlines()[-5:]
        if rc == 0:
            logger.info("scheduler.crypto_signals.done", tail=last_lines)
            _record("crypto_signals", "ok")
            return {"status": "ok", "tail": last_lines}
        logger.warning("scheduler.crypto_signals.bad_rc", rc=rc, tail=last_lines)
        _record("crypto_signals", "error")
        return {"status": "error", "rc": rc, "tail": last_lines}

    except asyncio.TimeoutError:
        logger.error("scheduler.crypto_signals.timeout")
        _record("crypto_signals", "error")
        return {"status": "error", "reason": "timeout_3min"}
    except Exception as exc:
        logger.error("scheduler.crypto_signals.failed", error=str(exc), exc_info=True)
        _record("crypto_signals", "error")
        return {"status": "error", "error": str(exc)}


async def snapshot_signals_job() -> dict[str, Any]:
    """Snapshot diario de /api/v1/ml/signals → signal_history.

    Chama scripts/snapshot_signals.py via subprocess. Idempotente
    (UPSERT em signal_history). Sem skip de weekend (snapshot pode
    rodar todo dia, simplesmente repete o estado se nada mudou).
    """
    logger.info("scheduler.snapshot_signals.start")
    # DSN priority: TIMESCALE_URL (compose network) sobrescreve PROFIT_TIMESCALE_DSN
    # (default localhost do .env). Mesma logica do gtd_enforcer_loop.
    ts_dsn = (
        os.environ.get("TIMESCALE_URL", "")
        .replace("postgresql+asyncpg://", "postgresql://")
        or os.environ.get("PROFIT_TIMESCALE_DSN", "")
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "python",
            "/app/scripts/snapshot_signals.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={
                **os.environ,
                "FINANALYTICS_API_URL": os.environ.get("FINANALYTICS_API_URL", "http://api:8000"),
                "PROFIT_TIMESCALE_DSN": ts_dsn,
            },
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
        rc = proc.returncode
        last_lines = stdout.decode("utf-8", errors="replace").splitlines()[-5:]
        if rc == 0:
            logger.info("scheduler.snapshot_signals.done", tail=last_lines)
            _record("snapshot_signals", "ok")
            return {"status": "ok", "tail": last_lines}
        logger.warning("scheduler.snapshot_signals.bad_rc", rc=rc, tail=last_lines)
        _record("snapshot_signals", "error")
        return {"status": "error", "rc": rc, "tail": last_lines}

    except asyncio.TimeoutError:
        logger.error("scheduler.snapshot_signals.timeout")
        _record("snapshot_signals", "error")
        return {"status": "error", "reason": "timeout_5min"}
    except Exception as exc:
        logger.error("scheduler.snapshot_signals.failed", error=str(exc), exc_info=True)
        _record("snapshot_signals", "error")
        return {"status": "error", "error": str(exc)}


async def yahoo_daily_bars_refresh_job() -> dict[str, Any]:
    """N11b (28/abr): refresh diario de profit_daily_bars com Yahoo OHLCV
    para FIIs+ETFs (asset_class IN ('fii','etf') no ticker_ml_config).

    Idempotente via ON CONFLICT. Skip em weekend (Yahoo nao atualiza B3 no
    fim de semana). Subprocess isolado para nao bloquear event loop.
    """
    if not _is_weekday():
        _record("yahoo_bars", "skip")
        return {"status": "skip", "reason": "weekend"}

    logger.info("scheduler.yahoo_bars.start")
    try:
        proc = await asyncio.create_subprocess_exec(
            "python",
            "/app/scripts/backfill_yahoo_daily_bars.py",
            "--years", "2",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
        rc = proc.returncode
        last_lines = stdout.decode("utf-8", errors="replace").splitlines()[-3:]
        if rc == 0:
            logger.info("scheduler.yahoo_bars.done", tail=last_lines)
            _record("yahoo_bars", "ok")
            return {"status": "ok", "tail": last_lines}
        logger.warning("scheduler.yahoo_bars.bad_rc", rc=rc, tail=last_lines)
        _record("yahoo_bars", "error")
        return {"status": "error", "rc": rc, "tail": last_lines}

    except asyncio.TimeoutError:
        logger.error("scheduler.yahoo_bars.timeout")
        _record("yahoo_bars", "error")
        return {"status": "error", "reason": "timeout_10min"}
    except Exception as exc:
        logger.error("scheduler.yahoo_bars.failed", error=str(exc), exc_info=True)
        _record("yahoo_bars", "error")
        return {"status": "error", "error": str(exc)}


async def fii_fundamentals_refresh_job() -> dict[str, Any]:
    """N5 (27/abr): refresh diario de DY/P/VP/div_12m via Status Invest.

    Reusa logica do scripts/scrape_status_invest_fii.py executando-o
    como subprocess. Mantido como subprocess (nao import direto) para
    isolar o scraping bloqueante (httpx sync) do event loop do scheduler.
    """
    if not _is_weekday():
        _record("fii_fund", "skip")
        return {"status": "skip", "reason": "weekend"}

    logger.info("scheduler.fii_fund.start")
    try:
        proc = await asyncio.create_subprocess_exec(
            "python",
            "/app/scripts/scrape_status_invest_fii.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
        rc = proc.returncode

        last_lines = stdout.decode("utf-8", errors="replace").splitlines()[-5:]
        if rc == 0:
            logger.info("scheduler.fii_fund.done", tail=last_lines)
            _record("fii_fund", "ok")
            return {"status": "ok", "tail": last_lines}
        logger.warning("scheduler.fii_fund.bad_rc", rc=rc, tail=last_lines)
        _record("fii_fund", "error")
        return {"status": "error", "rc": rc, "tail": last_lines}

    except asyncio.TimeoutError:
        logger.error("scheduler.fii_fund.timeout")
        _record("fii_fund", "error")
        return {"status": "error", "reason": "timeout_5min"}
    except Exception as exc:
        logger.error("scheduler.fii_fund.failed", error=str(exc), exc_info=True)
        _record("fii_fund", "error")
        return {"status": "error", "error": str(exc)}


async def cvm_informe_sync_job() -> dict[str, Any]:
    """N2 (27/abr): sincroniza inf_diario_fi_AAAAMM.zip da CVM.

    Competencia = mes anterior (AAAAMM). CVM publica entre dias 3-5 do
    mes seguinte; rodar dia CVM_INFORME_DAY (default 5) garante que
    o ZIP esta disponivel.

    Idempotente: sync_informe_diario faz check em fundos_sync_log
    (skip se ja sincronizou hoje). Pode rodar varias vezes sem problema.
    """
    today = datetime.now(UTC)
    if today.day != CVM_INFORME_DAY:
        _record("cvm_informe", "skip")
        return {"status": "skip", "reason": "wrong_day", "today": today.day, "target": CVM_INFORME_DAY}

    prev_month = today.replace(day=1) - timedelta(days=1)
    competencia = prev_month.strftime("%Y%m")

    logger.info("scheduler.cvm_informe.start", competencia=competencia)
    try:
        from finanalytics_ai.infrastructure.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            from finanalytics_ai.application.services.fundos_cvm_service import sync_informe_diario

            result = await sync_informe_diario(session, competencia=competencia)

        logger.info("scheduler.cvm_informe.done", competencia=competencia, **result)
        _record("cvm_informe", "ok")
        return {"status": "ok", "competencia": competencia, **result}

    except Exception as exc:
        logger.error("scheduler.cvm_informe.failed", error=str(exc), exc_info=True)
        _record("cvm_informe", "error")
        return {"status": "error", "competencia": competencia, "error": str(exc)}


async def reconcile_job() -> dict[str, Any]:
    """V4 (21/abr): chama GET /positions/dll no profit_agent.

    O handler enumera ordens via DLL e faz UPDATE em profit_orders
    quando status divergem (idempotente). Loga divergencias para
    Prometheus alertar (futuro: contador profit_agent_reconcile_diff).

    Skip silencioso fora da janela de pregao para evitar tentativas
    quando profit_agent pode estar offline. Usuario pode forcar
    via /positions/dll no UI.
    """
    now_utc = datetime.now(UTC)
    local_hour = (now_utc.hour + TZ_OFFSET) % 24  # UTC + (-3) = BRT
    if not _is_weekday():
        _record("reconcile", "skip")
        return {"status": "skip", "reason": "weekend"}
    if not (RECONCILE_START_HOUR <= local_hour < RECONCILE_END_HOUR):
        _record("reconcile", "skip")
        return {"status": "skip", "reason": "outside_market_hours", "local_hour": local_hour}

    try:
        import aiohttp

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
        ) as session:
            url = f"{PROFIT_AGENT_URL}/positions/dll"
            async with session.get(url) as resp:
                if resp.status != 200:
                    txt = await resp.text()
                    logger.warning(
                        "scheduler.reconcile.http_error",
                        status=resp.status,
                        body=txt[:200],
                    )
                    _record("reconcile", "error")
                    if scheduler_reconcile_errors_total is not None:
                        scheduler_reconcile_errors_total.inc()
                    return {"status": "error", "http_status": resp.status}
                data = await resp.json()
        n_orders = len(data.get("orders", [])) if isinstance(data, dict) else 0
        logger.info("scheduler.reconcile.done", orders=n_orders)
        _record("reconcile", "ok")
        return {"status": "ok", "orders": n_orders}

    except Exception as exc:
        # profit_agent pode estar offline — logar warning, nao error,
        # para nao spammar Sentry/alertas durante restart do agent.
        logger.warning("scheduler.reconcile.failed", error=str(exc))
        _record("reconcile", "error")
        if scheduler_reconcile_errors_total is not None:
            scheduler_reconcile_errors_total.inc()
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
        reconcile=RECONCILE_ENABLED,
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
    if RECONCILE_ENABLED:
        await reconcile_job()
    if CVM_INFORME_ENABLED:
        await cvm_informe_sync_job()
    if FII_FUND_ENABLED:
        await fii_fundamentals_refresh_job()
    if YAHOO_BARS_ENABLED:
        await yahoo_daily_bars_refresh_job()
    if CRYPTO_SIGNALS_ENABLED:
        await crypto_signals_snapshot_job()
    if STALE_PENDING_ENABLED:
        await cleanup_stale_pending_orders_job()
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

    async def settle_cash_loop() -> None:
        # Feature C5 (24/abr): liquida tx pending com settlement_date <= today.
        # Roda 00:05 BRT + mais uma vez 09:00 BRT (antes do pregao abrir).
        import os as _os_sc
        settle_hour = int(_os_sc.getenv("SCHEDULER_SETTLE_HOUR", "0"))  # 00 BRT default
        logger.info("scheduler.settle_cash.start", daily_hour=settle_hour)
        while True:
            next_run = _next_run_utc(settle_hour)
            wait = _seconds_until(next_run)
            logger.info(
                "scheduler.settle_cash.next",
                next_utc=next_run.isoformat(),
                wait_min=round(wait / 60),
            )
            await asyncio.sleep(wait)
            await settle_cash_transactions_job()

    async def reconcile_loop() -> None:
        # V4: interval-based em vez de daily — roda a cada N min,
        # silenciosamente skippa fora do pregao via reconcile_job().
        # Sprint Fix Alerts D (21/abr): apos 5 erros consecutivos
        # em horario de pregao, escala via Pushover critical.
        interval_s = max(60, RECONCILE_INTERVAL_MIN * 60)
        logger.info(
            "scheduler.reconcile.start",
            interval_min=RECONCILE_INTERVAL_MIN,
            window=f"{RECONCILE_START_HOUR}h-{RECONCILE_END_HOUR}h BRT",
            agent=PROFIT_AGENT_URL,
        )
        consecutive_errors = 0
        notified = False
        while True:
            result = await reconcile_job()
            status = result.get("status") if isinstance(result, dict) else None
            if status == "error":
                consecutive_errors += 1
                if consecutive_errors >= 5 and not notified:
                    try:
                        from finanalytics_ai.infrastructure.notifications.pushover import (
                            notify_system,
                        )

                        await notify_system(
                            title="Reconcile DLL <-> DB: 5+ falhas consecutivas",
                            message=(
                                f"profit_agent unreachable em pregao. "
                                f"Agent={PROFIT_AGENT_URL}. Verificar host Windows."
                            ),
                            critical=True,
                        )
                        notified = True
                    except Exception as _pex:
                        logger.warning("reconcile.pushover_failed", error=str(_pex))
            elif status == "ok":
                consecutive_errors = 0
                notified = False
            await asyncio.sleep(interval_s)

    async def cvm_informe_loop() -> None:
        # N2 (27/abr): roda 1x/dia em CVM_INFORME_HOUR. cvm_informe_sync_job
        # so executa de fato quando today.day == CVM_INFORME_DAY (default 5);
        # nos demais dias retorna skip silencioso. Loop diario simplifica
        # logica de "proximo dia 5 do mes" sem precisar calcular meses.
        logger.info(
            "scheduler.cvm_informe.start_loop",
            hour=CVM_INFORME_HOUR,
            target_day=CVM_INFORME_DAY,
        )
        while True:
            next_run = _next_run_utc(CVM_INFORME_HOUR)
            wait = _seconds_until(next_run)
            logger.info(
                "scheduler.cvm_informe.next",
                next_utc=next_run.isoformat(),
                wait_min=round(wait / 60),
            )
            await asyncio.sleep(wait)
            await cvm_informe_sync_job()

    tasks: list[asyncio.Task[None]] = []
    if MACRO_ENABLED:
        tasks.append(asyncio.create_task(macro_loop()))
    if OHLCV_ENABLED:
        tasks.append(asyncio.create_task(ohlcv_loop()))
    if CLEANUP_ENABLED:
        tasks.append(asyncio.create_task(cleanup_loop()))
    if RECONCILE_ENABLED:
        tasks.append(asyncio.create_task(reconcile_loop()))
    if CVM_INFORME_ENABLED:
        tasks.append(asyncio.create_task(cvm_informe_loop()))

    async def fii_fund_loop() -> None:
        # N5 (27/abr): roda 1x/dia em FII_FUND_HOUR. fii_fundamentals_refresh_job
        # ja faz skip em weekend. Idempotente por (ticker, snapshot_date).
        logger.info("scheduler.fii_fund.start_loop", hour=FII_FUND_HOUR)
        while True:
            next_run = _next_run_utc(FII_FUND_HOUR)
            wait = _seconds_until(next_run)
            logger.info(
                "scheduler.fii_fund.next",
                next_utc=next_run.isoformat(),
                wait_min=round(wait / 60),
            )
            await asyncio.sleep(wait)
            await fii_fundamentals_refresh_job()

    if FII_FUND_ENABLED:
        tasks.append(asyncio.create_task(fii_fund_loop()))

    async def yahoo_bars_loop() -> None:
        # N11b (28/abr): roda 1x/dia em YAHOO_BARS_HOUR. Skip em weekend.
        # Idempotente (ON CONFLICT em profit_daily_bars).
        logger.info("scheduler.yahoo_bars.start_loop", hour=YAHOO_BARS_HOUR)
        while True:
            next_run = _next_run_utc(YAHOO_BARS_HOUR)
            wait = _seconds_until(next_run)
            logger.info(
                "scheduler.yahoo_bars.next",
                next_utc=next_run.isoformat(),
                wait_min=round(wait / 60),
            )
            await asyncio.sleep(wait)
            await yahoo_daily_bars_refresh_job()

    if YAHOO_BARS_ENABLED:
        tasks.append(asyncio.create_task(yahoo_bars_loop()))

    async def tick_to_ohlc_backfill_loop() -> None:
        # Backfill diario profit_ticks -> ohlc_1m via continuous aggregate.
        # Roda 21h BRT (00h UTC) — apos close pregao (17h) + after-market (18h).
        hour = int(os.environ.get("TICK_TO_OHLC_BACKFILL_HOUR", "0"))  # UTC
        logger.info("scheduler.tick_to_ohlc.start_loop", hour_utc=hour)
        while True:
            next_run = _next_run_utc(hour)
            wait = _seconds_until(next_run)
            logger.info(
                "scheduler.tick_to_ohlc.next",
                next_utc=next_run.isoformat(),
                wait_min=round(wait / 60),
            )
            await asyncio.sleep(wait)
            await tick_to_ohlc_backfill_job()

    tasks.append(asyncio.create_task(tick_to_ohlc_backfill_loop()))

    async def crypto_signals_loop() -> None:
        # N6 (28/abr): snapshot diario crypto signals. Sem skip de weekend
        # (crypto 24/7).
        logger.info("scheduler.crypto_signals.start_loop", hour=CRYPTO_SIGNALS_HOUR)
        while True:
            next_run = _next_run_utc(CRYPTO_SIGNALS_HOUR)
            wait = _seconds_until(next_run)
            logger.info(
                "scheduler.crypto_signals.next",
                next_utc=next_run.isoformat(),
                wait_min=round(wait / 60),
            )
            await asyncio.sleep(wait)
            await crypto_signals_snapshot_job()

    if CRYPTO_SIGNALS_ENABLED:
        tasks.append(asyncio.create_task(crypto_signals_loop()))

    async def snapshot_signals_loop() -> None:
        # Snapshot diario de /signals → signal_history. Roda 19h BRT
        # (pos-pregao + 2h margem). Idempotente; sem skip de weekend.
        logger.info("scheduler.snapshot_signals.start_loop", hour=SNAPSHOT_SIGNALS_HOUR)
        while True:
            next_run = _next_run_utc(SNAPSHOT_SIGNALS_HOUR)
            wait = _seconds_until(next_run)
            logger.info(
                "scheduler.snapshot_signals.next",
                next_utc=next_run.isoformat(),
                wait_min=round(wait / 60),
            )
            await asyncio.sleep(wait)
            await snapshot_signals_job()

    if SNAPSHOT_SIGNALS_ENABLED:
        tasks.append(asyncio.create_task(snapshot_signals_loop()))

    async def stale_pending_loop() -> None:
        # C (28/abr): cleanup ordens pending stale 1x/dia (default 23h BRT,
        # após pregão). Mitiga acúmulo "49 orders" entupindo /positions/dll.
        # Tambem captura GTD expiradas (sweep de seguranca, alem do gtd_loop 60s).
        logger.info("scheduler.stale_pending.start_loop", hour=STALE_PENDING_HOUR)
        while True:
            next_run = _next_run_utc(STALE_PENDING_HOUR)
            wait = _seconds_until(next_run)
            logger.info(
                "scheduler.stale_pending.next",
                next_utc=next_run.isoformat(),
                wait_min=round(wait / 60),
            )
            await asyncio.sleep(wait)
            await cleanup_stale_pending_orders_job()

    if STALE_PENDING_ENABLED:
        tasks.append(asyncio.create_task(stale_pending_loop()))

    async def gtd_enforcer_loop() -> None:
        # GTD enforcer (28/abr): ordens com validity_type='GTD' e validity_date < NOW
        # sao canceladas. Roda a cada 60s para baixa latencia de cancel.
        # Nao usa cleanup_stale_pending (que faz mais coisas) — query SQL focada.
        # Prioridade DSN: TIMESCALE_URL (compose network) > PROFIT_TIMESCALE_DSN > TIMESCALE_DSN.
        timescale_dsn = (
            os.environ.get("TIMESCALE_URL")
            or os.environ.get("PROFIT_TIMESCALE_DSN")
            or os.environ.get("TIMESCALE_DSN", "")
        )
        agent_url = os.environ.get(
            "PROFIT_AGENT_URL", "http://host.docker.internal:8002",
        )
        if not timescale_dsn:
            logger.warning("scheduler.gtd.skip", reason="no_timescale_dsn")
            return
        ts_dsn = timescale_dsn.replace("postgresql+asyncpg://", "postgresql://")
        logger.info("scheduler.gtd.start_loop")
        import asyncpg, httpx
        while True:
            await asyncio.sleep(60)
            try:
                conn = await asyncpg.connect(ts_dsn)
                try:
                    rows = await conn.fetch(
                        """
                        SELECT local_order_id, env FROM profit_orders
                         WHERE order_status IN (0, 10)
                           AND validity_type = 'GTD'
                           AND validity_date IS NOT NULL
                           AND validity_date < NOW()
                         LIMIT 100
                        """,
                    )
                finally:
                    await conn.close()
                if not rows:
                    continue
                logger.info("scheduler.gtd.expired_found", count=len(rows))
                async with httpx.AsyncClient(timeout=5.0) as client:
                    for row in rows:
                        lid = int(row["local_order_id"])
                        try:
                            r = await client.post(
                                f"{agent_url}/order/cancel",
                                json={
                                    "local_order_id": lid,
                                    "env": row["env"] or "simulation",
                                },
                            )
                            body = r.json() if r.status_code == 200 else {}
                            if body.get("ok"):
                                logger.info("scheduler.gtd.cancelled", local_order_id=lid)
                            else:
                                # Marca como expired no DB mesmo se broker rejeitou
                                # (ordem pode nao existir mais no DLL — ex: status 4
                                # cancelled por outra rota, ret=-2147483636).
                                try:
                                    cn = await asyncpg.connect(ts_dsn)
                                    try:
                                        await cn.execute(
                                            "UPDATE profit_orders SET order_status=8, "
                                            "error_message='gtd_expired_cancel_failed', "
                                            "updated_at=NOW() WHERE local_order_id=$1",
                                            lid,
                                        )
                                    finally:
                                        await cn.close()
                                    logger.info(
                                        "scheduler.gtd.marked_expired",
                                        local_order_id=lid,
                                        agent_ret=body.get("ret"),
                                    )
                                except Exception as exc2:
                                    logger.warning(
                                        "scheduler.gtd.mark_failed",
                                        local_order_id=lid, error=str(exc2),
                                    )
                        except Exception as exc:
                            logger.warning(
                                "scheduler.gtd.cancel_exception",
                                local_order_id=lid, error=str(exc),
                            )
            except Exception as exc:
                logger.warning("scheduler.gtd.loop_error", error=str(exc))

    if os.environ.get("SCHEDULER_GTD_ENFORCE_ENABLED", "true").lower() == "true":
        tasks.append(asyncio.create_task(gtd_enforcer_loop()))

    # Feature C5: liquidacao diaria (on by default, simples e idempotente)
    import os as _os_sc
    if _os_sc.getenv("SCHEDULER_SETTLE_CASH_ENABLED", "true").lower() == "true":
        tasks.append(asyncio.create_task(settle_cash_loop()))

    if not tasks:
        logger.warning(
            "scheduler.loop.no_jobs",
            hint="Set SCHEDULER_MACRO_ENABLED or SCHEDULER_OHLCV_ENABLED or SCHEDULER_CLEANUP_ENABLED or SCHEDULER_RECONCILE_ENABLED=true",
        )
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
        metrics_port=METRICS_PORT,
        prom_available=_PROM_AVAILABLE,
    )
    # F (21/abr): inicia servidor HTTP /metrics em thread separada.
    # start_http_server cria background thread com daemon=True; nao
    # impede shutdown do processo. Falha em bind (porta ocupada)
    # apenas loga warning — scheduler segue sem metrics.
    if _PROM_AVAILABLE and METRICS_PORT > 0 and not RUN_ONCE:
        try:
            start_http_server(METRICS_PORT)
            logger.info("scheduler_worker.metrics.serving", port=METRICS_PORT)
        except Exception as exc:
            logger.warning(
                "scheduler_worker.metrics.bind_failed", port=METRICS_PORT, error=str(exc)
            )

    asyncio.run(run_once() if RUN_ONCE else schedule_loop())


if __name__ == "__main__":
    main()
