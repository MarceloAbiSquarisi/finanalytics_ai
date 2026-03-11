"""
MockSentimentAnalyzer — análise de sentimento por palavras-chave.

Design decision: implementação determinística sem IO externo.
Serve para:
  1. Testes unitários sem mock de rede.
  2. Desenvolvimento local sem ANTHROPIC_API_KEY.
  3. Fallback quando API externa está indisponível.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from finanalytics_ai.domain.entities.news_sentiment import (
    NewsSentiment,
    SentimentLabel,
)

logger = structlog.get_logger(__name__)

_BULLISH_KEYWORDS: frozenset[str] = frozenset({
    "lucro", "crescimento", "alta", "recorde", "dividendo", "expansão",
    "aprovação", "contrato", "parceria", "upgrade", "compra", "supera",
    "positivo", "resultado", "ganho", "valoriza", "sobe", "subiu",
    "profit", "growth", "record", "dividend", "approval", "beats",
    "buy", "positive", "rally", "surge", "rises",
})

_BEARISH_KEYWORDS: frozenset[str] = frozenset({
    "prejuízo", "queda", "perda", "multa", "investigação", "crise",
    "downgrade", "venda", "negativo", "redução", "rebaixamento", "falência",
    "fraude", "escândalo", "processo", "cai", "caiu", "baixa",
    "loss", "decline", "penalty", "investigation", "crisis",
    "sell", "negative", "cut", "bankruptcy", "fraud", "falls", "drops",
})

_STRONG_WORDS: frozenset[str] = frozenset({
    "muito", "fortemente", "significativo", "expressivo", "histórico",
    "strongly", "significantly", "massive", "historic",
})
_STRONG_MULTIPLIER = 1.5


class MockSentimentAnalyzer:
    """Analisador de sentimento baseado em contagem de palavras-chave."""

    MODEL_NAME = "mock-keyword-v1"

    async def analyze(
        self,
        event_id: str,
        ticker: str,
        headline: str,
        source: str = "unknown",
    ) -> NewsSentiment:
        if not headline.strip():
            return NewsSentiment.neutral(
                event_id=event_id,
                ticker=ticker,
                headline=headline,
                model=self.MODEL_NAME,
                source=source,
            )

        words = set(headline.lower().split())
        bullish_hits = words & _BULLISH_KEYWORDS
        bearish_hits = words & _BEARISH_KEYWORDS
        has_intensifier = bool(words & _STRONG_WORDS)

        bullish_count = len(bullish_hits)
        bearish_count = len(bearish_hits)

        if has_intensifier:
            bullish_count = int(bullish_count * _STRONG_MULTIPLIER)
            bearish_count = int(bearish_count * _STRONG_MULTIPLIER)

        total = bullish_count + bearish_count
        raw_score = (bullish_count - bearish_count) / max(total, 1) if total > 0 else 0.0
        score = max(-1.0, min(1.0, raw_score))
        label = SentimentLabel.from_score(score)

        reasoning_parts = []
        if bullish_hits:
            reasoning_parts.append(f"bullish: {sorted(bullish_hits)}")
        if bearish_hits:
            reasoning_parts.append(f"bearish: {sorted(bearish_hits)}")
        if has_intensifier:
            reasoning_parts.append("intensificador detectado")
        reasoning = "; ".join(reasoning_parts) if reasoning_parts else "sem palavras-chave relevantes"

        sentiment = NewsSentiment(
            event_id=event_id,
            ticker=ticker,
            headline=headline[:500],
            score=round(score, 4),
            label=label,
            reasoning=reasoning,
            model=self.MODEL_NAME,
            analyzed_at=datetime.now(UTC),
            source=source,
        )

        logger.debug(
            "sentiment.mock.analyzed",
            ticker=ticker,
            score=sentiment.score,
            label=str(sentiment.label),
        )

        return sentiment
