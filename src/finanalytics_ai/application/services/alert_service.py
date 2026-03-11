"""
AlertService — avalia alertas contra preços e dispara notificações.

Fluxo:
  1. Kafka recebe PRICE_UPDATE para ticker X
  2. app.py chama alert_service.evaluate_price(ticker, price)
  3. Busca alertas ACTIVE para o ticker no PostgreSQL
  4. Avalia cada alerta com Alert.evaluate(price)
  5. Alertas disparados → marca TRIGGERED no DB + broadcast no NotificationBus
  6. Clientes SSE em /alerts/stream recebem a notificação em tempo real

Design decisions:
  - Injeção de dependência manual: recebe session factory, não session direto
    porque o service é singleton mas cada avaliação precisa de sua própria
    transação (contexto de request/evento distinto)
  - evaluate_price é fire-and-forget no Kafka handler — não bloqueia o pipeline
  - Logging estruturado por alerta para rastreabilidade
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from finanalytics_ai.domain.entities.alert import Alert, AlertType
from finanalytics_ai.infrastructure.database.repositories.alert_repo import SQLAlertRepository
from finanalytics_ai.infrastructure.notifications import (
    AlertNotification,
    NotificationBus,
)

logger = structlog.get_logger(__name__)

# Tipo para session factory (ex: get_db do FastAPI)
SessionFactory = Callable[[], Coroutine[Any, Any, AsyncSession]]


class AlertService:
    """
    Serviço de avaliação e gestão de alertas.

    Injetado no lifespan da app como singleton.
    """

    def __init__(
        self,
        session_factory: Any,  # AsyncGenerator que yield AsyncSession
        notification_bus: NotificationBus,
    ) -> None:
        self._session_factory = session_factory
        self._bus = notification_bus

    async def evaluate_price(self, ticker: str, price: float) -> int:
        """
        Avalia todos os alertas ativos para um ticker contra o preço atual.
        Retorna número de alertas disparados.

        Chamado pelo Kafka handler a cada PRICE_UPDATE.
        """
        current = Decimal(str(price))
        triggered_count = 0

        async with self._session_factory() as session, session.begin():
            repo = SQLAlertRepository(session)
            alerts = await repo.find_active_by_ticker(ticker)

            if not alerts:
                return 0

            for alert in alerts:
                result = alert.evaluate(current)
                if result.triggered:
                    await repo.mark_triggered(alert.alert_id)
                    triggered_count += 1

                    notif = AlertNotification(
                        alert_id=alert.alert_id,
                        ticker=alert.ticker,
                        alert_type=alert.alert_type.value,
                        message=result.message,
                        current_price=float(current),
                        threshold=float(alert.threshold),
                        user_id=alert.user_id,
                        triggered_at=datetime.now(UTC).isoformat(),
                        context=result.context,
                    )
                    await self._bus.broadcast(notif)

                    logger.info(
                        "alert.triggered",
                        alert_id=alert.alert_id,
                        ticker=ticker,
                        alert_type=alert.alert_type,
                        price=price,
                        threshold=str(alert.threshold),
                    )

        return triggered_count

    async def create_alert(
        self,
        user_id: str,
        ticker: str,
        alert_type: str,
        threshold: float,
        reference_price: float = 0.0,
        note: str = "",
        expires_at: datetime | None = None,
    ) -> Alert:
        """Cria e persiste um novo alerta."""
        alert = Alert(
            user_id=user_id,
            ticker=ticker.upper(),
            alert_type=AlertType(alert_type),
            threshold=Decimal(str(threshold)),
            reference_price=Decimal(str(reference_price)),
            note=note,
            expires_at=expires_at,
        )
        async with self._session_factory() as session, session.begin():
            repo = SQLAlertRepository(session)
            await repo.save(alert)

        logger.info(
            "alert.created",
            alert_id=alert.alert_id,
            ticker=ticker,
            alert_type=alert_type,
            threshold=threshold,
        )
        return alert

    async def list_alerts(self, user_id: str) -> list[Alert]:
        async with self._session_factory() as session:
            repo = SQLAlertRepository(session)
            return await repo.find_by_user(user_id)

    async def cancel_alert(self, alert_id: str, user_id: str) -> bool:
        async with self._session_factory() as session, session.begin():
            repo = SQLAlertRepository(session)
            cancelled = await repo.cancel(alert_id, user_id)
        if cancelled:
            logger.info("alert.cancelled", alert_id=alert_id)
        return cancelled
