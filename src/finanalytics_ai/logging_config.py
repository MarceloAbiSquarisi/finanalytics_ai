"""
finanalytics_ai.logging_config
───────────────────────────────
Logging estruturado com structlog.

Design decision: structlog ao invés de logging padrão porque:
  1. Logs em JSON nativos — compatível com Datadog, Grafana Loki, CloudWatch
  2. Context binding — adicione campos ao logger sem poluir a assinatura
  3. Processadores encadeáveis — fácil de adicionar trace_id, user_id etc.
  4. Compatível com asyncio — sem bloqueio no I/O de log

Em desenvolvimento usa ConsoleRenderer (bonito e colorido).
Em produção usa JSONRenderer (parseable por sistemas de log).

Usage:
    import structlog
    logger = structlog.get_logger(__name__)
    await logger.ainfo("event.processed", ticker="PETR4", price=32.50)
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

import structlog

from finanalytics_ai.config import get_settings

if TYPE_CHECKING:
    from structlog.types import EventDict, WrappedLogger


def _add_app_context(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Injeta metadados padrão em todos os logs."""
    settings = get_settings()
    event_dict["service"] = getattr(settings, "otel_service_name", "finanalytics-ai")
    event_dict["env"] = str(getattr(settings, "env", "production"))
    return event_dict


def _drop_color_message_key(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Remove campo interno do uvicorn que polui o JSON."""
    event_dict.pop("color_message", None)
    return event_dict


def configure_logging() -> None:
    """
    Configura structlog + logging stdlib.

    Chame uma vez no startup da aplicação (main.py).
    """
    settings = get_settings()

    # Processadores compartilhados (sempre aplicados)
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _add_app_context,
        _drop_color_message_key,
    ]

    if settings.log_format == "json" or settings.is_production:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
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
    root_logger.setLevel(settings.log_level.upper())

    # Silencia loggers verbosos de libs externas
    for noisy_lib in ("asyncio", "aiohttp.access", "sqlalchemy.engine"):
        logging.getLogger(noisy_lib).setLevel(logging.WARNING)
