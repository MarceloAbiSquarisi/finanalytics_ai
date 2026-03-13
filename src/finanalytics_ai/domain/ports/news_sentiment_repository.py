"""
Port NewsSentimentRepository — contrato de persistência de sentimentos.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from finanalytics_ai.domain.entities.news_sentiment import NewsSentiment


@runtime_checkable
class NewsSentimentRepository(Protocol):
    """Persiste e recupera análises de sentimento de notícias."""

    async def save(self, sentiment: NewsSentiment) -> bool:
        """
        Persiste sentimento. Retorna True se inserido, False se duplicata.
        Idempotente por event_id — segunda chamada com mesmo event_id é no-op.
        """
        ...

    async def get_by_ticker(
        self,
        ticker: str,
        limit: int = 50,
    ) -> list[NewsSentiment]:
        """Retorna sentimentos mais recentes do ticker em ordem cronológica."""
        ...
