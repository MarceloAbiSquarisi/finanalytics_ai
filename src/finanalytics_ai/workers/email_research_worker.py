#!/usr/bin/env python3
"""
email_research_worker — Pipeline E1.1 (research bulletins → tags por ticker).

Estado atual: SCAFFOLD. Polling com ResearchFetcher Protocol abstrato —
implementacao concreta depende da fonte de dados a ser definida.
Por agora, o worker roda contra um fetcher injetado (mockado em tests).

Pipeline por ciclo:
  1. fetcher.fetch_unprocessed() -> lista de RawEmail (msg_id, body, source, received_at)
  2. para cada RawEmail:
     a. Skip se msg_id ja processado (qualquer ticker em email_research)
     b. classifier.classify(body, source) -> ClassificationResult
     c. Para cada mention: INSERT em email_research ON CONFLICT DO NOTHING
  3. Sleep INTERVAL_SEC

Config env:
  EMAIL_RESEARCH_ENABLED   default false (CI safe; nao roda sem opt-in)
  EMAIL_RESEARCH_INTERVAL  default 300 (5min)
  EMAIL_RESEARCH_DSN       postgresql DSN p/ tabela email_research
  ANTHROPIC_API_KEY        injetado via Settings global do projeto

Observabilidade: logs estruturados em pontos chave; metrica Prometheus fica
como follow-up.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import os
import signal as _signal_mod
import sys
from typing import Any, Protocol

import structlog

from finanalytics_ai.application.services.research_classifier import (
    ClassificationResult,
    ResearchClassifier,
    ResearchClassifierError,
)

logger = structlog.get_logger(__name__)

# ── Config via env ───────────────────────────────────────────────────────────

ENABLED = os.environ.get("EMAIL_RESEARCH_ENABLED", "false").lower() == "true"
INTERVAL_SEC = int(os.environ.get("EMAIL_RESEARCH_INTERVAL", "300"))
DSN = os.environ.get(
    "EMAIL_RESEARCH_DSN",
    "postgresql://finanalytics:postgres@finanalytics_postgres:5432/finanalytics",
)


# ── Tipos / Protocol ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RawEmail:
    """Email pronto p/ classificacao (HTML/PDF ja parseado em texto)."""

    msg_id: str
    broker_source: str  # 'btg' | 'xp' | 'genial' | ...
    body_text: str
    received_at: datetime  # tz-aware UTC


class ResearchFetcher(Protocol):
    """Adapter que retorna mensagens de research pendentes. Impl concreta
    depende da fonte de dados a ser definida."""

    def fetch_unprocessed(self, limit: int = 50) -> list[RawEmail]:
        """Retorna mensagens ainda nao classificadas (filtro do lado do impl)."""
        ...


# ── DB helpers (psycopg2 sync — worker tem 1 thread) ─────────────────────────


def _get_conn(dsn: str):
    import psycopg2

    return psycopg2.connect(dsn)


def msg_id_already_processed(dsn: str, msg_id: str) -> bool:
    """True se ha pelo menos 1 row em email_research com esse msg_id."""
    try:
        with _get_conn(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM email_research WHERE msg_id = %s LIMIT 1", (msg_id,))
            return cur.fetchone() is not None
    except Exception as exc:
        logger.warning("email_research.dedup_check_failed", error=str(exc))
        return False  # fail-open: tenta processar; INSERT ON CONFLICT cobre


def insert_mentions(
    *,
    dsn: str,
    msg_id: str,
    broker_source: str,
    raw_text_excerpt: str,
    received_at: datetime,
    result: ClassificationResult,
) -> int:
    """
    INSERT cada mention em email_research. ON CONFLICT (msg_id, ticker) DO NOTHING.
    Retorna numero de rows inseridas (best-effort — psycopg2 nao retorna por
    row). Loga erros mas nao raise.
    """
    if not result.mentions:
        return 0
    try:
        with _get_conn(dsn) as conn, conn.cursor() as cur:
            inserted = 0
            for m in result.mentions:
                cur.execute(
                    """
                    INSERT INTO email_research
                        (msg_id, ticker, broker_source, sentiment, action,
                         target_price, time_horizon, confidence,
                         raw_text_excerpt, received_at, classified_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (msg_id, ticker) DO NOTHING
                    """,
                    (
                        msg_id,
                        m.ticker,
                        broker_source,
                        m.sentiment,
                        m.action,
                        m.target_price,
                        m.time_horizon,
                        m.confidence,
                        raw_text_excerpt,
                        received_at,
                    ),
                )
                inserted += cur.rowcount
            conn.commit()
            return inserted
    except Exception as exc:
        logger.error("email_research.insert_failed", msg_id=msg_id, error=str(exc))
        return 0


# ── Loop principal ───────────────────────────────────────────────────────────


_shutdown = False


def _handle_signal(signum: int, frame: Any) -> None:
    global _shutdown
    logger.info("email_research.shutdown_requested", signum=signum)
    _shutdown = True


async def process_once(
    *,
    fetcher: ResearchFetcher,
    classifier: ResearchClassifier,
    dsn: str = DSN,
) -> dict[str, int]:
    """
    Executa 1 ciclo. Retorna stats {fetched, skipped_dup, classified, mentions}.
    """
    stats = {"fetched": 0, "skipped_dup": 0, "classified": 0, "mentions": 0}

    try:
        emails = fetcher.fetch_unprocessed(limit=50)
    except Exception as exc:
        logger.error("email_research.fetch_failed", error=str(exc))
        return stats

    stats["fetched"] = len(emails)
    for email in emails:
        if msg_id_already_processed(dsn, email.msg_id):
            stats["skipped_dup"] += 1
            continue

        try:
            result = classifier.classify(email.body_text, email.broker_source)
        except ResearchClassifierError as exc:
            logger.warning(
                "email_research.classify_failed",
                msg_id=email.msg_id,
                error=str(exc),
            )
            continue

        excerpt = (email.body_text or "")[:500]
        inserted = insert_mentions(
            dsn=dsn,
            msg_id=email.msg_id,
            broker_source=email.broker_source,
            raw_text_excerpt=excerpt,
            received_at=email.received_at,
            result=result,
        )
        stats["classified"] += 1
        stats["mentions"] += inserted
        logger.info(
            "email_research.classified",
            msg_id=email.msg_id,
            mentions=len(result.mentions),
            inserted=inserted,
            broker=email.broker_source,
        )

    return stats


async def main(*, fetcher: ResearchFetcher, classifier: ResearchClassifier) -> int:
    if not ENABLED:
        logger.info("email_research.disabled_via_env")
        while not _shutdown:
            await asyncio.sleep(60)
        return 0

    logger.info(
        "email_research.starting",
        interval=INTERVAL_SEC,
        booted_at=datetime.now(UTC).isoformat(),
    )

    while not _shutdown:
        try:
            stats = await process_once(fetcher=fetcher, classifier=classifier)
            logger.info("email_research.cycle_done", **stats)
        except Exception as exc:
            logger.error("email_research.cycle_failed", error=str(exc))
        try:
            await asyncio.sleep(INTERVAL_SEC)
        except asyncio.CancelledError:
            break

    logger.info("email_research.stopped")
    return 0


if __name__ == "__main__":
    # Entrypoint real depende da fonte de dados (ResearchFetcher concreto).
    # Por enquanto, este branch nao inicia (roda como modulo).
    _signal_mod.signal(_signal_mod.SIGTERM, _handle_signal)
    _signal_mod.signal(_signal_mod.SIGINT, _handle_signal)
    logger.error("email_research.entrypoint_pending_fetcher_impl")
    sys.exit(1)
