"""ML metrics refresh — atualiza Gauges Prometheus a partir de DB + filesystem.

Sprint Fix Alerts E (21/abr/2026) — destrava alert rules de drift/snapshot
no Grafana sem precisar pollar JSON do /api/v1/ml/metrics.

Background task asyncio iniciado pelo lifespan do FastAPI (app.py).
Refresh a cada `ML_METRICS_REFRESH_SECONDS` (default 300s = 5min).

Por que 5min e nao on-scrape (30s):
  - Drift/snapshot nao mudam rapido (calibracao = job offline diario,
    snapshot = job 18:30 BRT).
  - Query no Timescale a cada 30s para metricas estaveis e desperdicio.
  - Trade-off aceitavel: alerta tem ate ~5min de defasagem.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import psycopg2
import structlog

from finanalytics_ai.metrics import (
    ml_config_count,
    ml_drift_count,
    ml_latest_pickle_age_days,
    ml_pickle_count,
    ml_signals_by_status,
    ml_snapshot_age_days,
)

logger = structlog.get_logger(__name__)

REFRESH_SECONDS = int(os.environ.get("ML_METRICS_REFRESH_SECONDS", "300"))
_default_models = Path(__file__).resolve().parents[3] / "models"
MODELS_DIR = Path(os.environ.get("FINANALYTICS_MODELS_DIR", str(_default_models)))


def _dsn() -> str:
    return (
        os.environ.get("TIMESCALE_URL")
        or os.environ.get("PROFIT_TIMESCALE_DSN")
        or "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
    ).replace("postgresql+asyncpg://", "postgresql://")


def _refresh_once() -> None:
    """Coleta drift/freshness e atualiza Gauges. Idempotente; logs em erro."""
    # ── Pickles em disco ──
    pickle_tickers: set[str] = set()
    latest_mtime: float | None = None
    if MODELS_DIR.exists():
        for p in MODELS_DIR.glob("*_*.pkl"):
            parts = p.stem.split("_")
            if len(parts) >= 3:
                pickle_tickers.add(parts[2].upper())
            try:
                mt = p.stat().st_mtime
                if latest_mtime is None or mt > latest_mtime:
                    latest_mtime = mt
            except OSError:
                continue

    pickle_count = len(pickle_tickers)
    latest_age = -1
    if latest_mtime is not None:
        latest_age = max(0, int((datetime.now().timestamp() - latest_mtime) // 86400))

    # ── Config + signal history em DB ──
    config_tickers: set[str] = set()
    snapshot_age = -1
    signals_by_status: dict[str, int] = {"BUY": 0, "SELL": 0, "HOLD": 0}

    try:
        with psycopg2.connect(_dsn()) as conn, conn.cursor() as cur:
            cur.execute("SELECT ticker FROM ticker_ml_config")
            config_tickers = {r[0].upper() for r in cur.fetchall()}

            cur.execute("SELECT MAX(snapshot_date) FROM signal_history")
            row = cur.fetchone()
            last_snap = row[0] if row else None
            if last_snap is not None:
                try:
                    snapshot_age = (datetime.now(UTC).date() - last_snap).days
                except Exception:
                    snapshot_age = -1

            cur.execute(
                "SELECT signal, COUNT(*) FROM signal_history "
                "WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM signal_history) "
                "GROUP BY signal"
            )
            for sig, cnt in cur.fetchall():
                signals_by_status[sig] = int(cnt)
    except Exception as exc:
        logger.warning("ml_metrics_refresh.db_error", error=str(exc))
        # Mantem valores anteriores das gauges; nao zera para evitar
        # alertas falsos quando DB esta temporariamente off.
        return

    drift = config_tickers - pickle_tickers

    # ── Atualiza gauges ──
    ml_config_count.set(len(config_tickers))
    ml_pickle_count.set(pickle_count)
    ml_drift_count.set(len(drift))
    ml_snapshot_age_days.set(snapshot_age)
    ml_latest_pickle_age_days.set(latest_age)
    for sig in ("BUY", "SELL", "HOLD"):
        ml_signals_by_status.labels(signal=sig).set(signals_by_status.get(sig, 0))


async def refresh_loop() -> None:
    """Loop background — chamada pelo lifespan do FastAPI.

    Faz primeiro refresh imediato para preencher gauges; depois
    refresh periodico a cada REFRESH_SECONDS.
    """
    logger.info(
        "ml_metrics_refresh.loop.start",
        interval_s=REFRESH_SECONDS,
        models_dir=str(MODELS_DIR),
    )
    while True:
        try:
            await asyncio.to_thread(_refresh_once)
        except Exception as exc:
            logger.error("ml_metrics_refresh.cycle_failed", error=str(exc), exc_info=True)
        await asyncio.sleep(REFRESH_SECONDS)
