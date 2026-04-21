"""Pushover sender — canal unico para alertas system-wide.

Sprint Fix Alerts D (21/abr/2026) — substitui email/Slack/webhook adhoc.
Inclui:
  - send(): API HTTP simples (fire-and-forget, async).
  - subscribe_to_bus(): liga ao NotificationBus para alertas de
    indicador (price/threshold) automaticamente irem ao Pushover.
  - notify_system(): helper generico para qualquer modulo emitir
    alerta sem depender do AlertService bus (ex: scheduler_worker
    em falha critica).

Credenciais via env vars:
  PUSHOVER_USER_KEY  — sua user key (perfil em pushover.net)
  PUSHOVER_APP_TOKEN — app token (criar em pushover.net/apps/build)
  PUSHOVER_ENABLED   — auto-detect (true se ambas chaves setadas).

Doc API: https://pushover.net/api
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from finanalytics_ai.infrastructure.notifications import (
        AlertNotification,
        NotificationBus,
    )

logger = structlog.get_logger(__name__)

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"

USER_KEY = os.environ.get("PUSHOVER_USER_KEY", "").strip()
APP_TOKEN = os.environ.get("PUSHOVER_APP_TOKEN", "").strip()
ENABLED = (
    os.environ.get(
        "PUSHOVER_ENABLED",
        "true" if (USER_KEY and APP_TOKEN) else "false",
    ).lower()
    == "true"
)


async def send(
    message: str,
    title: str | None = None,
    priority: int = 0,
    url: str | None = None,
    url_title: str | None = None,
    sound: str | None = None,
) -> bool:
    """Envia push via Pushover. Fire-and-forget — nao bloqueia caller.

    Args:
        message: Corpo da notificacao (max 1024 chars).
        title: Titulo (max 250 chars). Default: nome do app no Pushover.
        priority: -2 (silent) | -1 (low) | 0 (normal) | 1 (high) |
                  2 (emergency, requer ack).
        url: URL clicavel anexada.
        url_title: Texto do link.
        sound: Override do sound (siren/cosmic/falling/etc).

    Returns:
        True se HTTP 200, False caso erro/disabled.
    """
    if not ENABLED:
        logger.debug("pushover.skip", reason="disabled")
        return False
    if not USER_KEY or not APP_TOKEN:
        logger.warning("pushover.missing_credentials")
        return False

    try:
        import httpx

        payload = {
            "token": APP_TOKEN,
            "user": USER_KEY,
            "message": message[:1024],
            "priority": str(priority),
        }
        if title:
            payload["title"] = title[:250]
        if url:
            payload["url"] = url
        if url_title:
            payload["url_title"] = url_title[:100]
        if sound:
            payload["sound"] = sound
        # Emergency requer retry+expire
        if priority == 2:
            payload["retry"] = "60"
            payload["expire"] = "3600"

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(PUSHOVER_API_URL, data=payload)
            ok = resp.status_code == 200
            (logger.info if ok else logger.warning)(
                "pushover.sent",
                status=resp.status_code,
                priority=priority,
                title=(title or "")[:50],
            )
            return ok
    except Exception as exc:
        logger.warning("pushover.failed", error=str(exc))
        return False


async def notify_system(
    title: str,
    message: str,
    *,
    critical: bool = False,
) -> bool:
    """Helper para qualquer modulo emitir alerta system-level.

    Ex no scheduler_worker:
        await notify_system(
            "Reconcile loop failed",
            f"profit_agent unreachable for {n} cycles",
            critical=True,
        )
    """
    return await send(
        message=message,
        title=f"FinAnalytics: {title}",
        priority=1 if critical else 0,
        sound="siren" if critical else None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# NotificationBus subscriber — alertas de indicador (AlertService) viram push
# ─────────────────────────────────────────────────────────────────────────────


async def _bus_consumer(bus: NotificationBus) -> None:
    """Consome alertas do NotificationBus e encaminha para Pushover.

    Roda como background task pelo lifespan do FastAPI.
    """
    queue = await bus.subscribe()
    logger.info("pushover.bus_consumer.started")
    try:
        while True:
            notif: AlertNotification = await queue.get()
            try:
                title = f"{notif.ticker}: {notif.alert_type}"
                msg = f"{notif.message}\nprice: {notif.current_price}\nthreshold: {notif.threshold}"
                # Alertas de indicador sao priority normal — alertas
                # criticos vem do Grafana via contact-points.yml com
                # priority=1 e som siren.
                await send(message=msg, title=title, priority=0)
            except Exception as exc:
                logger.warning("pushover.bus_consumer.dispatch_failed", error=str(exc))
            finally:
                queue.task_done()
    except asyncio.CancelledError:
        logger.info("pushover.bus_consumer.cancelled")
    finally:
        await bus.unsubscribe(queue)


def subscribe_to_bus(bus: NotificationBus) -> asyncio.Task[None] | None:
    """Cria task background que consome o bus e envia push notifications.

    Retorna a Task (para cancelamento no shutdown) ou None se
    Pushover esta desabilitado (sem credenciais).
    """
    if not ENABLED:
        logger.info("pushover.bus_subscriber.disabled")
        return None
    return asyncio.create_task(_bus_consumer(bus))
