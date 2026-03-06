"""
Dispatcher de notificações de alerta.

Canais suportados:
  1. SSE broadcast — publica no bus interno; clientes /alerts/stream recebem
  2. Webhook — HTTP POST para URL configurada pelo usuário (opcional)

Design decision: asyncio.Queue por conexão SSE (fan-out manual).
Alternativa seria Redis pub/sub, mas para este caso de uso
(poucas conexões simultâneas) o fan-out em memória é suficiente
e evita dependência extra.

O NotificationBus é um singleton registrado no lifespan da app.
AlertService recebe via injeção de dependência.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, AsyncIterator

import structlog

from finanalytics_ai.domain.entities.alert import AlertTriggerResult

logger = structlog.get_logger(__name__)


@dataclass
class AlertNotification:
    alert_id:      str
    ticker:        str
    alert_type:    str
    message:       str
    current_price: float
    threshold:     float
    user_id:       str
    triggered_at:  str
    context:       dict[str, Any]

    def to_sse(self) -> str:
        data = {
            "alert_id":      self.alert_id,
            "ticker":        self.ticker,
            "alert_type":    self.alert_type,
            "message":       self.message,
            "current_price": self.current_price,
            "threshold":     self.threshold,
            "user_id":       self.user_id,
            "triggered_at":  self.triggered_at,
            **self.context,
        }
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


class NotificationBus:
    """
    Bus de notificações em memória para SSE fan-out.

    Cada cliente SSE registra uma Queue via subscribe().
    Quando um alerta dispara, broadcast() coloca a notificação
    em todas as queues registradas.

    Max 100 subscribers simultâneos — suficiente para uso single-tenant.
    """

    def __init__(self, max_queue_size: int = 200) -> None:
        self._subscribers: list[asyncio.Queue[AlertNotification]] = []
        self._max_queue_size = max_queue_size
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[AlertNotification]:
        """Registra um novo subscriber SSE. Retorna sua queue."""
        queue: asyncio.Queue[AlertNotification] = asyncio.Queue(
            maxsize=self._max_queue_size
        )
        async with self._lock:
            self._subscribers.append(queue)
        logger.debug("notification.subscriber.added", total=len(self._subscribers))
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[AlertNotification]) -> None:
        async with self._lock:
            try:
                self._subscribers.remove(queue)
            except ValueError:
                pass
        logger.debug("notification.subscriber.removed", total=len(self._subscribers))

    async def broadcast(self, notification: AlertNotification) -> None:
        """Envia notificação para todos os subscribers conectados."""
        async with self._lock:
            subscribers = list(self._subscribers)

        dropped = 0
        for q in subscribers:
            try:
                q.put_nowait(notification)
            except asyncio.QueueFull:
                dropped += 1   # cliente lento — descarta silenciosamente

        logger.info(
            "notification.broadcast",
            ticker=notification.ticker,
            alert_type=notification.alert_type,
            subscribers=len(subscribers),
            dropped=dropped,
        )

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def stream(
        self,
        queue: asyncio.Queue[AlertNotification],
        user_id: str | None = None,
    ) -> AsyncIterator[str]:
        """Iterador SSE — yield de eventos até a conexão fechar."""
        yield f"data: {json.dumps({'type':'connected','subscribers':self.subscriber_count})}\n\n"
        try:
            while True:
                try:
                    notif = await asyncio.wait_for(queue.get(), timeout=20.0)
                    # Filtro opcional por user_id
                    if user_id and notif.user_id != user_id:
                        continue
                    yield notif.to_sse()
                    queue.task_done()
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass


# ── Singleton global ──────────────────────────────────────────────────────────
_bus: NotificationBus | None = None


def get_notification_bus() -> NotificationBus:
    global _bus
    if _bus is None:
        _bus = NotificationBus()
    return _bus


# ── Webhook dispatcher (opcional) ────────────────────────────────────────────

async def dispatch_webhook(url: str, notification: AlertNotification) -> None:
    """
    Envia notificação via HTTP POST para um webhook externo.
    Fire-and-forget — não bloqueia o fluxo principal.
    """
    try:
        import httpx
        payload = {
            "alert_id":      notification.alert_id,
            "ticker":        notification.ticker,
            "alert_type":    notification.alert_type,
            "message":       notification.message,
            "current_price": notification.current_price,
            "threshold":     notification.threshold,
            "triggered_at":  notification.triggered_at,
        }
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, json=payload)
            logger.info(
                "webhook.dispatched",
                url=url,
                status=resp.status_code,
                alert_id=notification.alert_id,
            )
    except Exception as exc:
        logger.warning("webhook.failed", url=url, error=str(exc))
