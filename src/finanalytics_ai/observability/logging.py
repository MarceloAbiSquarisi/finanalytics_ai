"""
Logging estruturado com structlog.

Por que structlog vs logging padrão?
- Campos extras como dicts, não f-strings (parsing fácil no Loki/CloudWatch).
- Contexto acumulado por request via contextvars (sem passar logger por toda a call chain).
- Processadores plugáveis: dev usa ConsoleRenderer; prod usa JSONRenderer.

Uso:
    from finanalytics_ai.observability.logging import get_logger
    log = get_logger(__name__)
    log.info("evento_processado", event_id=str(event.id), tipo=event.event_type)
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, WrappedLogger

from finanalytics_ai.config import Settings


def _add_log_level(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """Adiciona campo 'level' explícito para facilitar filtragem."""
    event_dict["level"] = method_name.upper()
    return event_dict


def configure_logging(settings: Settings) -> None:
    """Configura structlog + stdlib logging.

    Chamado UMA vez no startup (main.py ou worker entry point).
    Idempotente — pode ser chamado múltiplas vezes sem efeito colateral.
    """
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        _add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.log_format == "json":
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Silencia libs barulhentas em produção
    for noisy_lib in ("asyncio", "aiohttp.access", "sqlalchemy.engine"):
        logging.getLogger(noisy_lib).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Factory de logger. Uso: log = get_logger(__name__)"""
    return structlog.get_logger(name)  # type: ignore[return-value]
