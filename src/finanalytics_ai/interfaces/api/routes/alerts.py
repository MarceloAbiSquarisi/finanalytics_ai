"""
Rotas de alertas — CRUD + SSE stream de notificações.

Endpoints:
  POST   /alerts/              — cria alerta
  GET    /alerts/              — lista alertas do usuário
  DELETE /alerts/{id}          — cancela alerta
  GET    /alerts/stream        — SSE: notificações em tempo real
  GET    /alerts/status        — status do bus de notificações
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import structlog

router = APIRouter()
logger = structlog.get_logger(__name__)

# ── Schemas de entrada/saída ──────────────────────────────────────────────────


class CreateAlertRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10)
    alert_type: str = Field(..., description="stop_loss|take_profit|price_target|pct_drop|pct_rise")
    threshold: float = Field(..., gt=0)
    reference_price: float = Field(default=0.0, ge=0)
    note: str = Field(default="", max_length=200)
    user_id: str = Field(default="user-demo")
    expires_at: datetime | None = None


class AlertResponse(BaseModel):
    alert_id: str
    ticker: str
    alert_type: str
    threshold: float
    reference_price: float
    status: str
    note: str
    user_id: str
    created_at: str
    triggered_at: str | None


# ── Dependency: AlertService ──────────────────────────────────────────────────


def _get_alert_service() -> Any:
    from finanalytics_ai.interfaces.api.app import get_alert_service

    svc = get_alert_service()
    if svc is None:
        raise HTTPException(503, detail="AlertService não disponível")
    return svc


# ── CRUD ─────────────────────────────────────────────────────────────────────


@router.post("/", response_model=AlertResponse, status_code=201)
async def create_alert(body: CreateAlertRequest) -> AlertResponse:
    """Cria um novo alerta de preço."""
    svc = _get_alert_service()
    alert = await svc.create_alert(
        user_id=body.user_id,
        ticker=body.ticker,
        alert_type=body.alert_type,
        threshold=body.threshold,
        reference_price=body.reference_price,
        note=body.note,
        expires_at=body.expires_at,
    )
    return _to_response(alert)


@router.get("/", response_model=list[AlertResponse])
async def list_alerts(user_id: str = Query(default="user-demo")) -> list[AlertResponse]:
    """Lista todos os alertas do usuário."""
    svc = _get_alert_service()
    alerts = await svc.list_alerts(user_id)
    return [_to_response(a) for a in alerts]


@router.delete("/{alert_id}")
async def cancel_alert(alert_id: str, user_id: str = Query(default="user-demo")) -> dict:
    """Cancela um alerta ativo."""
    svc = _get_alert_service()
    cancelled = await svc.cancel_alert(alert_id, user_id)
    if not cancelled:
        raise HTTPException(404, detail="Alerta não encontrado ou já inativo")
    return {"cancelled": True, "alert_id": alert_id}


# ── SSE Stream ────────────────────────────────────────────────────────────────


@router.get("/stream")
async def alerts_stream(
    user_id: str | None = Query(default=None, description="Filtrar por user_id"),
) -> StreamingResponse:
    """
    SSE — stream de notificações de alertas disparados em tempo real.

    Conecte com:
        const es = new EventSource('/api/v1/alerts/stream')
        es.onmessage = (e) => console.log(JSON.parse(e.data))

    Cada notificação inclui: alert_id, ticker, alert_type, message,
    current_price, threshold, triggered_at.
    """
    from finanalytics_ai.infrastructure.notifications import get_notification_bus

    bus = get_notification_bus()
    queue = await bus.subscribe()

    async def _generator():
        try:
            async for chunk in bus.stream(queue, user_id=user_id):
                yield chunk
        finally:
            await bus.unsubscribe(queue)

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/status")
async def alerts_status() -> dict:
    """Status do bus de notificações."""
    from finanalytics_ai.infrastructure.notifications import get_notification_bus

    bus = get_notification_bus()
    return {
        "subscribers": bus.subscriber_count,
        "service_available": get_alert_service_status(),
    }


def get_alert_service_status() -> bool:
    try:
        from finanalytics_ai.interfaces.api.app import get_alert_service

        return get_alert_service() is not None
    except Exception:
        return False


# ── Helper ────────────────────────────────────────────────────────────────────


def _to_response(alert: Any) -> AlertResponse:
    return AlertResponse(
        alert_id=alert.alert_id,
        ticker=alert.ticker,
        alert_type=alert.alert_type.value,
        threshold=float(alert.threshold),
        reference_price=float(alert.reference_price),
        status=alert.status.value,
        note=alert.note,
        user_id=alert.user_id,
        created_at=alert.created_at.isoformat(),
        triggered_at=alert.triggered_at.isoformat() if alert.triggered_at else None,
    )
