"""
Hierarquia de excecoes do dominio.

Principio: excecoes sao parte da API publica do dominio. Cada excecao
carrega contexto suficiente para logging e decisao de retry sem precisar
inspecionar a mensagem (anti-pattern).

Hierarquia:
    EventProcessingError (base)
    ├── TransientError      → retry permitido
    │   ├── DatabaseError
    │   └── ExternalServiceError
    └── PermanentError      → sem retry, vai para dead-letter
        ├── RuleViolationError
        ├── InvalidEventError
        └── IdempotencyConflict  (tecnico, nao um erro de negocio)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uuid


class EventProcessingError(Exception):
    """Base para todos os erros do pipeline de eventos."""

    def __init__(
        self,
        message: str,
        *,
        event_id: uuid.UUID | None = None,
        original: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.event_id = event_id
        self.original = original

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"message={str(self)!r}, "
            f"event_id={self.event_id!r})"
        )


class TransientError(EventProcessingError):
    """
    Erro temporario — o retry faz sentido.
    Exemplos: timeout de banco, servico externo indisponivel.
    """


class DatabaseError(TransientError):
    """Falha de conexao ou operacao no banco de dados."""


class ExternalServiceError(TransientError):
    """
    Falha em servico externo (ex: BRAPI timeout).
    Inclui status_code para decisao de retry inteligente:
    5xx = retriavel, 4xx = permanente.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        event_id: uuid.UUID | None = None,
        original: Exception | None = None,
    ) -> None:
        super().__init__(message, event_id=event_id, original=original)
        self.status_code = status_code

    @property
    def is_retriable(self) -> bool:
        """4xx nao deve ser retriado (exceto 429). 5xx sim."""
        if self.status_code is None:
            return True
        return self.status_code >= 500 or self.status_code == 429


class PermanentError(EventProcessingError):
    """
    Erro permanente — retry nao resolveria.
    O evento vai direto para dead-letter queue.
    """


class RuleViolationError(PermanentError):
    """Regra de negocio violada. Inclui nome da regra para rastreabilidade."""

    def __init__(
        self,
        message: str,
        *,
        rule_name: str,
        event_id: uuid.UUID | None = None,
    ) -> None:
        super().__init__(message, event_id=event_id)
        self.rule_name = rule_name


class InvalidEventError(PermanentError):
    """Payload do evento invalido (schema incorreto, campos obrigatorios ausentes)."""


class IdempotencyConflict(EventProcessingError):
    """
    Evento ja foi processado com sucesso.
    Nao eh um erro de negocio — eh o comportamento correto do sistema.
    Separado de PermanentError para nao ser logado como falha.
    """

    def __init__(self, event_id: uuid.UUID) -> None:
        super().__init__(
            f"Evento {event_id} ja foi processado (idempotencia)",
            event_id=event_id,
        )


class MaxRetriesExceededError(PermanentError):
    """Evento esgotou todas as tentativas de retry."""

    def __init__(self, event_id: uuid.UUID, max_retries: int) -> None:
        super().__init__(
            f"Evento {event_id} excedeu {max_retries} tentativas de retry",
            event_id=event_id,
        )
        self.max_retries = max_retries
