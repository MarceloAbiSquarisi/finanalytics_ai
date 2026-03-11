"""
Port SentimentAnalyzer — contrato para análise de sentimento de notícias.

Design decision: Protocol com runtime_checkable permite isinstance() nos testes
sem herança forçada. Implementações (mock, Anthropic, OpenAI) satisfazem o
contrato estruturalmente — zero acoplamento ao domínio.

Trade-off: Protocol vs ABC
  - ABC: erro em tempo de definição da classe se método não implementado.
  - Protocol: erro em tempo de uso (isinstance falha). Preferimos Protocol pois
    as implementações vivem na infra e não devem importar o domínio via herança.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from finanalytics_ai.domain.entities.news_sentiment import NewsSentiment


@runtime_checkable
class SentimentAnalyzer(Protocol):
    """Analisa headline de notícia e retorna sentimento estruturado."""

    async def analyze(
        self,
        event_id: str,
        ticker: str,
        headline: str,
        source: str = "unknown",
    ) -> NewsSentiment:
        """
        Analisa sentimento da headline.

        Deve ser idempotente: mesma headline + ticker = mesmo resultado
        (dentro da margem de variação do modelo).
        Nunca deve levantar exceção — retorna NewsSentiment.neutral() em falha.
        """
        ...
