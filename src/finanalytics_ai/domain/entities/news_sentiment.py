"""
Entidade NewsSentiment — resultado da análise de sentimento de uma notícia.

Design decision: frozen dataclass garante imutabilidade — sentimentos são
fatos históricos e nunca devem ser alterados após criação. O score float
(-1.0 a +1.0) é mais rico que um label binário e permite agregações como
"sentimento médio da semana" sem perda de informação.

O campo `model` registra qual analisador gerou o resultado, viabilizando
comparações A/B entre modelos (mock vs claude) e auditoria.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class SentimentLabel(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"

    @classmethod
    def from_score(cls, score: float) -> SentimentLabel:
        """Classifica score numérico em label discreto.

        Thresholds calibrados para notícias de mercado brasileiro:
        - |score| < 0.15: ruído de mercado, neutro
        - |score| >= 0.15: sinal relevante
        """
        if score >= 0.15:
            return cls.BULLISH
        if score <= -0.15:
            return cls.BEARISH
        return cls.NEUTRAL


@dataclass(frozen=True)
class NewsSentiment:
    """
    Resultado da análise de sentimento de uma notícia de mercado.

    event_id: referência ao MarketEvent que originou a análise.
    ticker: ativo referenciado na notícia.
    headline: texto analisado (primeiros 500 chars).
    score: valor contínuo em [-1.0, +1.0] onde:
           -1.0 = extremamente bearish
            0.0 = neutro
           +1.0 = extremamente bullish
    label: classificação discreta derivada do score.
    reasoning: explicação da análise (para auditoria e debug).
    model: identificador do analisador ("mock", "claude-sonnet-4-6", etc).
    analyzed_at: timestamp da análise em UTC.
    source: origem da notícia (brapi, infomoney, etc).
    """

    event_id: str
    ticker: str
    headline: str
    score: float
    label: SentimentLabel
    reasoning: str
    model: str
    analyzed_at: datetime
    source: str = "unknown"

    def __post_init__(self) -> None:
        if not (-1.0 <= self.score <= 1.0):
            raise ValueError(f"score deve estar em [-1.0, 1.0], recebido: {self.score}")

    @property
    def is_actionable(self) -> bool:
        """Sentimento com sinal suficiente para trigger de alerta."""
        return abs(self.score) >= 0.15

    @classmethod
    def neutral(
        cls,
        event_id: str,
        ticker: str,
        headline: str,
        model: str = "mock",
        source: str = "unknown",
    ) -> NewsSentiment:
        """Factory para sentimento neutro (payload incompleto, fallback)."""
        return cls(
            event_id=event_id,
            ticker=ticker,
            headline=headline,
            score=0.0,
            label=SentimentLabel.NEUTRAL,
            reasoning="Análise indisponível — headline ausente ou analisador não configurado.",
            model=model,
            analyzed_at=datetime.now(UTC),
            source=source,
        )
