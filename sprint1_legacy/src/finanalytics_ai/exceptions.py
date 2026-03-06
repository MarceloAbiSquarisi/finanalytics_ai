"""
finanalytics_ai.exceptions
───────────────────────────
Hierarquia de exceções customizadas do domínio.

Design decision: Hierarquia explícita permite que o código chamador
faça catch granular ou amplo conforme o contexto. Evita usar
exceções genéricas (ValueError, RuntimeError) que perdem contexto.

Cada exceção carrega um `code` estruturado — útil para logging,
monitoramento e respostas de API padronizadas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FinAnalyticsError(Exception):
    """Raiz da hierarquia de exceções do FinAnalytics AI."""

    message: str
    code: str = "FINANALYTICS_ERROR"
    context: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        if self.context:
            ctx_str = ", ".join(f"{k}={v!r}" for k, v in self.context.items())
            return f"[{self.code}] {self.message} ({ctx_str})"
        return f"[{self.code}] {self.message}"


# ── Domínio ───────────────────────────────────────────────────────────────────


@dataclass
class DomainError(FinAnalyticsError):
    """Violação de regra de negócio."""

    code: str = "DOMAIN_ERROR"


@dataclass
class InvalidTickerError(DomainError):
    """Ticker de ativo inválido ou não encontrado."""

    code: str = "INVALID_TICKER"


@dataclass
class InsufficientFundsError(DomainError):
    """Saldo insuficiente para a operação."""

    code: str = "INSUFFICIENT_FUNDS"


@dataclass
class InvalidQuantityError(DomainError):
    """Quantidade de ativos inválida (negativa ou zero)."""

    code: str = "INVALID_QUANTITY"


@dataclass
class PortfolioNotFoundError(DomainError):
    """Portfólio não encontrado para o usuário."""

    code: str = "PORTFOLIO_NOT_FOUND"


@dataclass
class StopLossViolationError(DomainError):
    """Operação bloqueada por regra de stop loss."""

    code: str = "STOP_LOSS_VIOLATION"


# ── Aplicação ─────────────────────────────────────────────────────────────────


@dataclass
class ApplicationError(FinAnalyticsError):
    """Erros de orquestração e casos de uso."""

    code: str = "APPLICATION_ERROR"


@dataclass
class DuplicateEventError(ApplicationError):
    """Evento duplicado detectado — idempotência."""

    code: str = "DUPLICATE_EVENT"


@dataclass
class EventProcessingError(ApplicationError):
    """Falha ao processar um evento de mercado."""

    code: str = "EVENT_PROCESSING_ERROR"


@dataclass
class CommandValidationError(ApplicationError):
    """Command inválido (campos faltando, tipos errados)."""

    code: str = "COMMAND_VALIDATION_ERROR"


# ── Infraestrutura ────────────────────────────────────────────────────────────


@dataclass
class InfrastructureError(FinAnalyticsError):
    """Erros de I/O, banco, rede."""

    code: str = "INFRASTRUCTURE_ERROR"


@dataclass
class DatabaseError(InfrastructureError):
    """Falha de banco de dados."""

    code: str = "DATABASE_ERROR"


@dataclass
class ConnectionPoolExhaustedError(DatabaseError):
    """Pool de conexões esgotado."""

    code: str = "CONNECTION_POOL_EXHAUSTED"


@dataclass
class MarketDataUnavailableError(InfrastructureError):
    """API de dados de mercado indisponível ou sem resposta."""

    code: str = "MARKET_DATA_UNAVAILABLE"


@dataclass
class BrokerAPIError(InfrastructureError):
    """Erro na API da corretora (XP, BTG)."""

    code: str = "BROKER_API_ERROR"
    status_code: int = 0


@dataclass
class TransientError(InfrastructureError):
    """
    Erro transitório — elegível para retry.

    O decorator de retry (tenacity) captura esta exceção.
    Não envolva erros permanentes (ex: 404) nesta classe.
    """

    code: str = "TRANSIENT_ERROR"
    attempt: int = 0
