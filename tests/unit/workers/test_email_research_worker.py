"""
Testes do email_research_worker (E1.1).

Cobertura — foco em process_once orchestration:
  - fetcher vazio -> stats zerados, sem chamadas
  - fetcher com 1 email novo -> classify + insert chamados
  - fetcher com email duplicado (msg_id ja em DB) -> skip, classify nao chamado
  - classifier raise -> log + continue (nao quebra ciclo)
  - fetcher raise -> log + retorna stats zerados (loop continua no main)

Mocks: GmailFetcher in-memory (lista predefinida), classifier.classify
substituido, _get_conn substituido p/ controle das queries.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from finanalytics_ai.application.services.research_classifier import (
    ClassificationResult,
    ResearchClassifierError,
    ResearchMention,
)
from finanalytics_ai.workers.email_research_worker import (
    RawEmail,
    process_once,
)


# ── Stub fetcher ─────────────────────────────────────────────────────────────


class _StubFetcher:
    """In-memory GmailFetcher pra tests."""

    def __init__(self, emails: list[RawEmail]) -> None:
        self._emails = emails
        self.calls = 0

    def fetch_unprocessed(self, limit: int = 50) -> list[RawEmail]:
        self.calls += 1
        return self._emails[:limit]


def _email(msg_id: str, body: str = "PETR4 mantemos compra", source: str = "btg") -> RawEmail:
    return RawEmail(
        msg_id=msg_id,
        broker_source=source,
        body_text=body,
        received_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
    )


def _result_with(*tickers: str) -> ClassificationResult:
    return ClassificationResult(
        mentions=[
            ResearchMention(
                ticker=t, sentiment="BULLISH", action="BUY",
                confidence=0.95,
            )
            for t in tickers
        ]
    )


# ── Empty fetcher ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_fetcher_returns_zero_stats() -> None:
    fetcher = _StubFetcher([])
    classifier = MagicMock()
    classifier.classify.return_value = _result_with()

    stats = await process_once(
        fetcher=fetcher, classifier=classifier, dsn="postgres://stub"
    )

    assert stats == {"fetched": 0, "skipped_dup": 0, "classified": 0, "mentions": 0}
    classifier.classify.assert_not_called()
    assert fetcher.calls == 1


# ── Happy path: classify + insert ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_email_classified_and_inserted() -> None:
    fetcher = _StubFetcher([_email("m1")])
    classifier = MagicMock()
    classifier.classify.return_value = _result_with("PETR4", "VALE3")

    with patch(
        "finanalytics_ai.workers.email_research_worker.msg_id_already_processed",
        return_value=False,
    ), patch(
        "finanalytics_ai.workers.email_research_worker.insert_mentions",
        return_value=2,
    ) as m_insert:
        stats = await process_once(
            fetcher=fetcher, classifier=classifier, dsn="postgres://stub"
        )

    assert stats == {"fetched": 1, "skipped_dup": 0, "classified": 1, "mentions": 2}
    classifier.classify.assert_called_once_with(
        "PETR4 mantemos compra", "btg"
    )
    m_insert.assert_called_once()
    insert_kwargs = m_insert.call_args.kwargs
    assert insert_kwargs["msg_id"] == "m1"
    assert insert_kwargs["broker_source"] == "btg"
    assert len(insert_kwargs["result"].mentions) == 2


# ── Dedup: msg_id ja processado ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_msg_id_skipped() -> None:
    fetcher = _StubFetcher([_email("m1"), _email("m2")])
    classifier = MagicMock()
    classifier.classify.return_value = _result_with("PETR4")

    # m1 ja processado, m2 nao
    def already(_dsn: str, msg_id: str) -> bool:
        return msg_id == "m1"

    with patch(
        "finanalytics_ai.workers.email_research_worker.msg_id_already_processed",
        side_effect=already,
    ), patch(
        "finanalytics_ai.workers.email_research_worker.insert_mentions",
        return_value=1,
    ) as m_insert:
        stats = await process_once(
            fetcher=fetcher, classifier=classifier, dsn="postgres://stub"
        )

    assert stats == {"fetched": 2, "skipped_dup": 1, "classified": 1, "mentions": 1}
    # classifier so foi chamado pra m2
    classifier.classify.assert_called_once()
    assert classifier.classify.call_args.args[0] == "PETR4 mantemos compra"
    m_insert.assert_called_once()
    assert m_insert.call_args.kwargs["msg_id"] == "m2"


# ── Classifier failure: log + continue ───────────────────────────────────────


@pytest.mark.asyncio
async def test_classifier_error_continues_to_next_email() -> None:
    fetcher = _StubFetcher([_email("m1"), _email("m2", body="VALE3 venda")])
    classifier = MagicMock()
    classifier.classify.side_effect = [
        ResearchClassifierError("llm_failed: timeout"),
        _result_with("VALE3"),
    ]

    with patch(
        "finanalytics_ai.workers.email_research_worker.msg_id_already_processed",
        return_value=False,
    ), patch(
        "finanalytics_ai.workers.email_research_worker.insert_mentions",
        return_value=1,
    ) as m_insert:
        stats = await process_once(
            fetcher=fetcher, classifier=classifier, dsn="postgres://stub"
        )

    # m1 falhou, m2 passou
    assert stats == {"fetched": 2, "skipped_dup": 0, "classified": 1, "mentions": 1}
    assert classifier.classify.call_count == 2
    m_insert.assert_called_once()
    assert m_insert.call_args.kwargs["msg_id"] == "m2"


# ── Fetcher failure: stats zerados ────────────────────────────────────────────


class _BoomFetcher:
    def fetch_unprocessed(self, limit: int = 50) -> list[RawEmail]:  # noqa: ARG002
        raise RuntimeError("gmail offline")


@pytest.mark.asyncio
async def test_fetcher_error_returns_zero_stats() -> None:
    fetcher = _BoomFetcher()
    classifier = MagicMock()

    stats = await process_once(
        fetcher=fetcher, classifier=classifier, dsn="postgres://stub"
    )

    assert stats == {"fetched": 0, "skipped_dup": 0, "classified": 0, "mentions": 0}
    classifier.classify.assert_not_called()


# ── Insert excerpt truncation (sanity) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_excerpt_truncated_to_500_chars() -> None:
    body = "A" * 1000
    fetcher = _StubFetcher([_email("m1", body=body)])
    classifier = MagicMock()
    classifier.classify.return_value = _result_with("PETR4")

    with patch(
        "finanalytics_ai.workers.email_research_worker.msg_id_already_processed",
        return_value=False,
    ), patch(
        "finanalytics_ai.workers.email_research_worker.insert_mentions",
        return_value=1,
    ) as m_insert:
        await process_once(
            fetcher=fetcher, classifier=classifier, dsn="postgres://stub"
        )

    excerpt = m_insert.call_args.kwargs["raw_text_excerpt"]
    assert len(excerpt) == 500
    assert excerpt == "A" * 500
