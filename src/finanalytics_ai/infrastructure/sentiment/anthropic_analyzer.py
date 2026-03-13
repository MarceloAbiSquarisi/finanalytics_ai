"""
AnthropicSentimentAnalyzer — análise de sentimento via Claude API.

Design decision: httpx async direto em vez do SDK anthropic para:
  - Controle total sobre timeout e retry logic (tenacity).
  - Sem dependência do SDK no pyproject (opcional, só se ANTHROPIC_API_KEY presente).
  - Resposta JSON estruturada via prompt engineering — mais confiável que
    parsear texto livre.

Trade-off: se o SDK anthropic evoluir a API (ex: tools/function calling),
teremos de atualizar o prompt manualmente. Aceitável para S16 — S18+
pode migrar para SDK se necessário.

Resiliência:
  - timeout de 10s para evitar bloquear o worker.
  - em qualquer exceção, retorna NewsSentiment.neutral() e loga o erro.
  - NÃO re-levanta exceção — handler nunca deve falhar por análise de sentimento.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import httpx
import structlog

from finanalytics_ai.domain.entities.news_sentiment import (
    NewsSentiment,
    SentimentLabel,
)

logger = structlog.get_logger(__name__)

_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-sonnet-4-20250514"
_TIMEOUT_SECONDS = 10.0

_SYSTEM_PROMPT = """\
Você é um analista de mercado financeiro brasileiro especializado em análise de sentimento de notícias.

Analise a headline fornecida e retorne APENAS um JSON válido (sem markdown, sem explicações fora do JSON) com:
{
  "score": <float entre -1.0 e 1.0>,
  "label": <"bullish" | "bearish" | "neutral">,
  "reasoning": <string curta em português explicando o score, máximo 150 chars>
}

Regras de score:
- +1.0: extremamente positivo para o ativo (lucro recorde, aprovação regulatória grande)
- +0.5: claramente positivo (bons resultados, upgrade de rating)
- 0.0: neutro ou ambíguo
- -0.5: claramente negativo (queda de lucro, perda de contrato)
- -1.0: extremamente negativo (falência, fraude confirmada, multa bilionária)

Use label="bullish" para score >= 0.15, label="bearish" para score <= -0.15, label="neutral" caso contrário.\
"""


class AnthropicSentimentAnalyzer:
    """
    Análise de sentimento usando Claude via API REST.

    Requer variável de ambiente ANTHROPIC_API_KEY.
    Em ausência da key ou falha de rede, retorna sentimento neutro.
    """

    MODEL_NAME = _MODEL

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        if not self._api_key:
            logger.warning(
                "anthropic.analyzer.no_api_key",
                message="ANTHROPIC_API_KEY não configurada — usando fallback neutro",
            )

    async def analyze(
        self,
        event_id: str,
        ticker: str,
        headline: str,
        source: str = "unknown",
    ) -> NewsSentiment:
        if not headline.strip() or not self._api_key:
            return NewsSentiment.neutral(
                event_id=event_id,
                ticker=ticker,
                headline=headline,
                model=self.MODEL_NAME,
                source=source,
            )

        try:
            return await self._call_api(event_id, ticker, headline, source)
        except Exception as exc:
            logger.warning(
                "anthropic.analyzer.failed",
                ticker=ticker,
                event_id=event_id,
                error=str(exc),
            )
            return NewsSentiment.neutral(
                event_id=event_id,
                ticker=ticker,
                headline=headline,
                model=self.MODEL_NAME,
                source=source,
            )

    async def _call_api(
        self,
        event_id: str,
        ticker: str,
        headline: str,
        source: str,
    ) -> NewsSentiment:
        user_message = f"Ticker: {ticker}\nHeadline: {headline[:500]}"
        assert self._api_key, "api_key deve estar definido"  # já verificado em analyze()
        api_key: str = self._api_key

        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(
                _ANTHROPIC_API_URL,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": _MODEL,
                    "max_tokens": 256,
                    "system": _SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_message}],
                },
            )
            response.raise_for_status()

        data = response.json()
        raw_text = data["content"][0]["text"].strip()

        # Strip markdown fences se presentes
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]

        parsed = json.loads(raw_text)
        score = float(parsed["score"])
        score = max(-1.0, min(1.0, score))
        label = SentimentLabel(parsed.get("label", SentimentLabel.from_score(score)))
        reasoning = str(parsed.get("reasoning", ""))[:200]

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

        logger.info(
            "anthropic.analyzer.success",
            ticker=ticker,
            score=sentiment.score,
            label=str(sentiment.label),
        )

        return sentiment
