"""
Cliente Anthropic — wrapper sobre o SDK oficial.

Padrao do projeto p/ chamadas a Claude API. Encapsula:
  - Modelo default (Haiku 4.5 — barato, rapido, suficiente p/ classificacao)
  - Prompt caching automatico no system prompt (cache_control top-level)
  - Typed exception handling (RateLimitError, APIStatusError, etc.)
  - Logging estruturado de usage (cache_read/cache_creation/input/output)

Notas:
  - SDK auto-retry: max_retries=2 default (configuravel via construtor)
  - Caching minimo Haiku 4.5: 4096 tokens. System prompts menores nao
    cacheiam (no-op silencioso, sem erro). Verificar via
    response.usage.cache_read_input_tokens > 0 apos 2+ requests.
  - narrative_service.py ainda usa httpx raw com modelo legado — migrar
    pra esse client em sessao futura.

Uso (sync):
    client = AnthropicClient.from_settings(settings)
    response = client.parse(
        system="<instructions>",
        user_content="<email body>",
        output_format=ClassificationResult,
        cache_system=True,
    )
    result: ClassificationResult = response.parsed_output
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

import anthropic
from anthropic import APIConnectionError, APIStatusError, RateLimitError
import structlog

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = structlog.get_logger(__name__)

# Modelo default — Haiku 4.5: $1.00/M input, $5.00/M output, 200K context.
# Versao com data: claude-haiku-4-5-20251001 (alias claude-haiku-4-5 tambem
# funciona; usamos a dated p/ travar deploy reproduzivel).
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# Default max_tokens conservador p/ classificacao (output curto JSON).
# Aumentar quando usar pra geracao de texto.
DEFAULT_MAX_TOKENS = 2000

T = TypeVar("T", bound="BaseModel")


class AnthropicClientError(Exception):
    """Erro generico do AnthropicClient — wrapping de exceptions SDK."""


class AnthropicClient:
    """Wrapper sync sobre anthropic.Anthropic com defaults do projeto."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        max_retries: int = 2,
        timeout_sec: float = 30.0,
    ) -> None:
        if not api_key:
            raise AnthropicClientError("anthropic_api_key is empty")
        self._client = anthropic.Anthropic(
            api_key=api_key,
            max_retries=max_retries,
            timeout=timeout_sec,
        )
        self._model = model

    @classmethod
    def from_settings(cls, settings: Any) -> AnthropicClient:
        """Constroi a partir de pydantic Settings (le anthropic_api_key)."""
        return cls(api_key=getattr(settings, "anthropic_api_key", "") or "")

    @property
    def model(self) -> str:
        return self._model

    def parse(
        self,
        *,
        system: str,
        user_content: str,
        output_format: type[T],
        max_tokens: int = DEFAULT_MAX_TOKENS,
        cache_system: bool = True,
    ) -> Any:
        """
        Executa messages.parse com saida estruturada (Pydantic).

        Args:
            system: system prompt (instructions estaveis — bom candidato a cache).
            user_content: conteudo dinamico (e.g. corpo do email).
            output_format: classe Pydantic alvo. Resposta SDK valida + popula
                response.parsed_output como instancia dessa classe.
            max_tokens: limite de tokens de saida.
            cache_system: se True, marca system com cache_control ephemeral.
                Ineficaz se system < 4096 tokens (Haiku 4.5 minimo).

        Retorna o response object do SDK. Acesse:
            response.parsed_output  -> instancia do output_format
            response.usage.cache_read_input_tokens -> sanity check de cache hit

        Raises:
            AnthropicClientError em qualquer falha (wrap de SDK exceptions).
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user_content}],
            "output_format": output_format,
        }
        if cache_system:
            # Auto-cache do ultimo bloco cacheavel (system aqui).
            kwargs["cache_control"] = {"type": "ephemeral"}

        try:
            response = self._client.messages.parse(**kwargs)
        except RateLimitError as exc:
            logger.warning("anthropic.rate_limit", error=str(exc))
            raise AnthropicClientError(f"rate_limit: {exc}") from exc
        except APIStatusError as exc:
            logger.error(
                "anthropic.api_status_error",
                status=getattr(exc, "status_code", None),
                error=str(exc),
            )
            raise AnthropicClientError(f"api_status_error: {exc}") from exc
        except APIConnectionError as exc:
            logger.warning("anthropic.connection_error", error=str(exc))
            raise AnthropicClientError(f"connection_error: {exc}") from exc

        usage = getattr(response, "usage", None)
        if usage is not None:
            logger.info(
                "anthropic.parse.ok",
                model=self._model,
                input_tokens=getattr(usage, "input_tokens", 0),
                output_tokens=getattr(usage, "output_tokens", 0),
                cache_read=getattr(usage, "cache_read_input_tokens", 0),
                cache_create=getattr(usage, "cache_creation_input_tokens", 0),
            )
        return response
