"""
Testes do ResearchClassifier (E1.1).

Cobertura — foco na logica de orquestracao + tipos:
  - body vazio -> ClassificationResult(mentions=[]) sem chamada ao LLM
  - LLM retorna ClassificationResult valido -> passthrough
  - LLM retorna parsed_output != ClassificationResult -> raise ResearchClassifierError
  - AnthropicClientError -> wrap em ResearchClassifierError
  - body grande (>50k) -> truncado antes de enviar
  - broker_source vai pro user_content (nao no system — preserva cache)
  - Schema Pydantic: validacao de sentiment/action enums

Mocks: substitui AnthropicClient.parse via attribute injection. NAO chama
a API real.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from finanalytics_ai.application.services.research_classifier import (
    ClassificationResult,
    ResearchClassifier,
    ResearchClassifierError,
    ResearchMention,
)
from finanalytics_ai.infrastructure.llm import AnthropicClientError

# ── Helpers ──────────────────────────────────────────────────────────────────


def _stub_response(parsed: Any) -> Any:
    """Resposta stub mimetizando o objeto retornado pelo SDK."""
    r = MagicMock()
    r.parsed_output = parsed
    r.usage = MagicMock(
        input_tokens=10,
        output_tokens=20,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    return r


def _llm_returning(parsed: Any) -> MagicMock:
    """Mock AnthropicClient cujo .parse() retorna o stub."""
    llm = MagicMock()
    llm.parse.return_value = _stub_response(parsed)
    return llm


# ── Empty body ────────────────────────────────────────────────────────────────


class TestEmptyBody:
    def test_empty_string_returns_empty(self) -> None:
        llm = MagicMock()
        clf = ResearchClassifier(llm)
        result = clf.classify("", "btg")
        assert result.mentions == []
        llm.parse.assert_not_called()

    def test_whitespace_returns_empty(self) -> None:
        llm = MagicMock()
        clf = ResearchClassifier(llm)
        result = clf.classify("   \n  \t ", "btg")
        assert result.mentions == []
        llm.parse.assert_not_called()


# ── Happy path ────────────────────────────────────────────────────────────────


class TestHappyPath:
    def test_single_mention_passthrough(self) -> None:
        expected = ClassificationResult(
            mentions=[
                ResearchMention(
                    ticker="PETR4",
                    sentiment="BULLISH",
                    action="BUY",
                    target_price=38.0,
                    time_horizon="12 meses",
                    confidence=0.95,
                )
            ]
        )
        llm = _llm_returning(expected)
        clf = ResearchClassifier(llm)

        result = clf.classify("Mantemos compra para PETR4 com TP R$ 38.", "btg")

        assert result == expected
        assert llm.parse.call_count == 1
        kwargs = llm.parse.call_args.kwargs
        assert kwargs["output_format"] is ClassificationResult
        assert kwargs["cache_system"] is True
        assert "Fonte: btg" in kwargs["user_content"]
        # System prompt vai como kwarg — deve incluir regras B3
        assert "B3" in kwargs["system"]

    def test_multi_mention_passthrough(self) -> None:
        expected = ClassificationResult(
            mentions=[
                ResearchMention(
                    ticker="ITUB4",
                    sentiment="BULLISH",
                    action="BUY",
                    target_price=36.0,
                    confidence=0.92,
                ),
                ResearchMention(
                    ticker="BBDC4",
                    sentiment="NEUTRAL",
                    action="HOLD",
                    confidence=0.88,
                ),
                ResearchMention(
                    ticker="SANB11",
                    sentiment="BEARISH",
                    action="SELL",
                    target_price=14.0,
                    confidence=0.93,
                ),
            ]
        )
        llm = _llm_returning(expected)
        clf = ResearchClassifier(llm)

        result = clf.classify("Setor financeiro: ...", "xp")

        assert len(result.mentions) == 3
        assert {m.ticker for m in result.mentions} == {"ITUB4", "BBDC4", "SANB11"}

    def test_no_mentions_when_macro_only(self) -> None:
        """Email macro sem ticker B3 -> mentions vazia."""
        expected = ClassificationResult(mentions=[])
        llm = _llm_returning(expected)
        clf = ResearchClassifier(llm)

        result = clf.classify("Esperamos Selic em 11.75%...", "genial")

        assert result.mentions == []
        llm.parse.assert_called_once()


# ── Error handling ────────────────────────────────────────────────────────────


class TestErrors:
    def test_llm_error_wrapped(self) -> None:
        llm = MagicMock()
        llm.parse.side_effect = AnthropicClientError("rate_limit: too many")
        clf = ResearchClassifier(llm)

        with pytest.raises(ResearchClassifierError) as exc_info:
            clf.classify("PETR4 ...", "btg")
        assert "llm_failed" in str(exc_info.value)
        assert "rate_limit" in str(exc_info.value)

    def test_unexpected_parsed_type_raises(self) -> None:
        """Se o SDK retornar algo diferente de ClassificationResult, falha."""
        llm = _llm_returning("string ao inves de ClassificationResult")
        clf = ResearchClassifier(llm)

        with pytest.raises(ResearchClassifierError) as exc_info:
            clf.classify("PETR4 ...", "btg")
        assert "unexpected_parsed_output_type" in str(exc_info.value)


# ── Truncation ────────────────────────────────────────────────────────────────


class TestTruncation:
    def test_long_body_truncated_to_50k(self) -> None:
        big = "PETR4 " * 20_000  # 120_000 chars
        expected = ClassificationResult(mentions=[])
        llm = _llm_returning(expected)
        clf = ResearchClassifier(llm)

        clf.classify(big, "btg")

        user_content = llm.parse.call_args.kwargs["user_content"]
        # body trecho enviado nao pode passar de 50_000 + overhead do template
        # Garantimos que o tamanho do segmento "Corpo do email:" e' bounded.
        assert "PETR4" in user_content
        assert len(user_content) < 60_000  # 50k body + ~5k overhead


# ── Schema Pydantic ──────────────────────────────────────────────────────────


class TestPydanticSchema:
    def test_invalid_sentiment_rejected(self) -> None:
        with pytest.raises(Exception):  # pydantic ValidationError
            ResearchMention(
                ticker="PETR4",
                sentiment="EXTRA_BULLISH",  # type: ignore[arg-type]
                confidence=0.9,
            )

    def test_invalid_confidence_range(self) -> None:
        with pytest.raises(Exception):
            ResearchMention(
                ticker="PETR4",
                sentiment="BULLISH",
                confidence=1.5,  # > 1.0 -> reject
            )

    def test_optional_fields_default_none(self) -> None:
        m = ResearchMention(ticker="PETR4", sentiment="BULLISH", confidence=0.9)
        assert m.action is None
        assert m.target_price is None
        assert m.time_horizon is None

    def test_action_validation(self) -> None:
        with pytest.raises(Exception):
            ResearchMention(
                ticker="PETR4",
                sentiment="BULLISH",
                action="STRONG_BUY",  # type: ignore[arg-type]
                confidence=0.9,
            )
