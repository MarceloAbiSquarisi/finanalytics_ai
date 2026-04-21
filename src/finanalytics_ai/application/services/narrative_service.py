"""
NarrativeService — LLM analysis for forecast results.

Strategy:
  1. Ollama local (primary) — free, private, uses local RTX 4090
  2. Claude API (fallback) — if Ollama is unavailable/slow

Design decisions:
- httpx async for both providers; no heavy SDKs
- Prompt is structured to produce a consistent JSON response
  so the frontend can parse signal + confidence + narrative text
- Timeout 60s for Ollama (70B model), 30s for Claude
- Returns a plain ForecastNarrative even on failure (neutral signal)
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re

import httpx
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ForecastNarrative:
    signal: str  # "COMPRA" | "VENDA" | "NEUTRO"
    confidence: str  # "ALTA" | "MÉDIA" | "BAIXA"
    summary: str  # 2-3 frases curtas
    reasoning: str  # análise detalhada
    risks: str  # principais riscos
    provider: str  # "ollama" | "claude" | "fallback"


_NEUTRAL_FALLBACK = ForecastNarrative(
    signal="NEUTRO",
    confidence="BAIXA",
    summary="Análise narrativa indisponível no momento.",
    reasoning="Nenhum provedor de LLM respondeu dentro do tempo limite.",
    risks="Impossível avaliar riscos sem análise narrativa.",
    provider="fallback",
)

_PROMPT_TEMPLATE = """Você é um analista quantitativo sênior especializado em mercado financeiro brasileiro.
Analise os dados de forecast abaixo e produza uma análise de investimento objetiva.

TICKER: {ticker}
PREÇO ATUAL: R$ {last_price:.2f}
PREÇO ALVO (forecast {horizon}d): R$ {target_price:.2f}
VARIAÇÃO ESPERADA: {change_pct:+.2f}%
INTERVALO DE CONFIANÇA 80%: R$ {ci_lower:.2f} — R$ {ci_upper:.2f}
MODELOS USADOS: {models}
PESOS DO ENSEMBLE: {weights}

INDICADORES TÉCNICOS ATUAIS:
- RSI(14): {rsi}
- MACD: {macd_signal}
- Bollinger: {bb_position}

PADRÕES RECENTES DETECTADOS: {patterns}

Retorne APENAS um JSON válido com esta estrutura exata (sem markdown, sem texto fora do JSON):
{{
  "signal": "COMPRA" | "VENDA" | "NEUTRO",
  "confidence": "ALTA" | "MÉDIA" | "BAIXA",
  "summary": "2-3 frases resumindo a oportunidade/risco",
  "reasoning": "análise detalhada em 4-6 frases cobrindo tendência, momentum e valuação relativa",
  "risks": "principais riscos que podem invalidar o forecast em 2-3 frases"
}}"""


class NarrativeService:
    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        ollama_model: str = "llama3.1:70b",
        anthropic_api_key: str = "",
    ) -> None:
        self._ollama_url = ollama_url.rstrip("/")
        self._ollama_model = ollama_model
        self._anthropic_key = anthropic_api_key

    async def analyze(self, context: dict) -> ForecastNarrative:
        prompt = _PROMPT_TEMPLATE.format(
            ticker=context.get("ticker", "?"),
            last_price=context.get("last_price", 0),
            target_price=context.get("target_price", 0),
            change_pct=context.get("change_pct", 0),
            horizon=context.get("horizon_days", 30),
            ci_lower=context.get("ci_lower", 0),
            ci_upper=context.get("ci_upper", 0),
            models=context.get("models", "ensemble"),
            weights=context.get("weights", "{}"),
            rsi=context.get("rsi", "N/A"),
            macd_signal=context.get("macd_signal", "N/A"),
            bb_position=context.get("bb_position", "N/A"),
            patterns=context.get("patterns", "Nenhum detectado"),
        )

        # Try Ollama first
        try:
            result = await self._call_ollama(prompt)
            logger.info("narrative.ollama.ok", ticker=context.get("ticker"))
            return result
        except Exception as e:
            logger.warning("narrative.ollama.failed", error=str(e))

        # Fallback: Claude API
        if self._anthropic_key:
            try:
                result = await self._call_claude(prompt)
                logger.info("narrative.claude.ok", ticker=context.get("ticker"))
                return result
            except Exception as e:
                logger.warning("narrative.claude.failed", error=str(e))

        return _NEUTRAL_FALLBACK

    async def _call_ollama(self, prompt: str) -> ForecastNarrative:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self._ollama_url}/api/generate",
                json={
                    "model": self._ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.2, "num_predict": 600},
                },
            )
            resp.raise_for_status()
            text = resp.json().get("response", "")
            return _parse_narrative(text, "ollama")

    async def _call_claude(self, prompt: str) -> ForecastNarrative:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self._anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 600,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            text = resp.json()["content"][0]["text"]
            return _parse_narrative(text, "claude")


def _parse_narrative(text: str, provider: str) -> ForecastNarrative:
    """Extract JSON from LLM response robustly."""
    # Strip markdown code fences if present
    clean = re.sub(r"```(?:json)?", "", text).strip()
    # Find first { ... } block
    match = re.search(r"\{.*\}", clean, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON found in response: {clean[:200]}")
    data = json.loads(match.group())

    valid_signals = {"COMPRA", "VENDA", "NEUTRO"}
    valid_conf = {"ALTA", "MÉDIA", "BAIXA"}

    signal = data.get("signal", "NEUTRO").upper()
    if signal not in valid_signals:
        signal = "NEUTRO"
    confidence = data.get("confidence", "BAIXA").upper()
    if confidence not in {"ALTA", "MÉDIA", "BAIXA"}:
        confidence = "BAIXA"

    return ForecastNarrative(
        signal=signal,
        confidence=confidence,
        summary=data.get("summary", ""),
        reasoning=data.get("reasoning", ""),
        risks=data.get("risks", ""),
        provider=provider,
    )
