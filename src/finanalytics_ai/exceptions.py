"""
Exceções customizadas do sistema.

Hierarquia:
    AppError                  ← base para tudo
    ├── DomainError           ← violações de regras de negócio (não retry)
    │   ├── InvalidEventIdError
    │   ├── InvalidStatusTransitionError
    │   └── EventValidationError
    ├── ApplicationError      ← erros de orquestração
    │   ├── EventAlreadyProcessedError
    │   ├── NoHandlerFoundError
    │   └── BusinessRuleError
    └── InfrastructureError   ← erros de I/O (candidatos a retry)
        ├── DatabaseError
        ├── TransientDatabaseError  ← subconjunto retryable
        └── ExternalServiceError
            └── TransientExternalServiceError

Regra de retry: apenas subclasses de TransientError são retentadas.
"""


class AppError(Exception):
    """Raiz da hierarquia. Nunca capture esta classe diretamente no app code."""

    def __init__(self, message: str, *, context: dict | None = None) -> None:
        super().__init__(message)
        self.context: dict = context or {}


# ──────────────────────────────────────────────────────────────────────────────
# Application errors
# ──────────────────────────────────────────────────────────────────────────────


class ApplicationError(AppError):
    pass


class EventAlreadyProcessedError(ApplicationError):
    """Lançado quando tentamos reprocessar um evento já COMPLETED.

    Não é um erro de verdade — é idempotência. O caller deve ignorar.
    """

    pass


class NoHandlerFoundError(ApplicationError):
    """Nenhuma BusinessRule registrada para o EventType recebido."""

    pass


class BusinessRuleError(ApplicationError):
    """A regra de negócio rejeitou o evento (dados inválidos, estado inconsistente).

    Não faz sentido retentar — o payload não vai mudar.
    Deve ir direto para dead-letter.
    """

    pass


# ──────────────────────────────────────────────────────────────────────────────
# Infrastructure errors
# ──────────────────────────────────────────────────────────────────────────────


class InfrastructureError(AppError):
    pass


class DatabaseError(InfrastructureError):
    pass


class TransientDatabaseError(DatabaseError):
    """Falhas de conexão, deadlock, timeout — candidatas a retry."""

    pass


class ExternalServiceError(InfrastructureError):
    pass


class TransientExternalServiceError(ExternalServiceError):
    """HTTP 429, 503, timeout — candidatas a retry com backoff."""

    pass


# ── Exceções do domínio legado (compatibilidade) ─────────────────────────────

class InvalidTickerError(AppError):
    pass

class InvalidQuantityError(AppError):
    pass

class FintzAPIError(InfrastructureError):
    pass

class FintzParseError(InfrastructureError):
    pass

class FintzSyncError(InfrastructureError):
    pass

# ── Exceções legado — compatibilidade com código pré-existente ───────────────

class FinAnalyticsError(AppError):
    pass

class InsufficientFundsError(AppError):
    pass

class PortfolioNotFoundError(AppError):
    pass

class MarketDataUnavailableError(InfrastructureError):
    pass

class EventProcessingError(ApplicationError):
    pass

class TransientError(InfrastructureError):
    pass