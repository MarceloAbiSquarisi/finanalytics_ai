"""
Rotas de alertas de indicadores Fintz.

POST   /api/v1/alerts/indicator          -- cria alerta de indicador
GET    /api/v1/alerts/indicator          -- lista alertas do usuario
DELETE /api/v1/alerts/indicator/{id}     -- cancela alerta
GET    /api/v1/alerts/indicator/fields   -- indicadores suportados
POST   /api/v1/alerts/indicator/evaluate -- dispara avaliacao manual (admin/debug)

Os alertas disparados chegam pelo mesmo SSE stream de precos:
GET /alerts/stream
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
import structlog

from finanalytics_ai.application.services.indicator_alert_service import (
    SUPPORTED_INDICATORS,
    IndicatorAlertService,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/alerts/indicator", tags=["Alertas Indicadores"])


class CreateIndicatorAlertRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10, description="Ex: PETR4")
    indicator: str = Field(..., description="Ex: ROE, DividendYield, P_L")
    operator: str = Field(..., description="gt | lt | gte | lte")
    threshold: float = Field(..., description="Valor em percentual para % ou absoluto para ratios")
    note: str = Field(default="", max_length=200)
    user_id: str = Field(default="user-demo")


class IndicatorAlertResponse(BaseModel):
    alert_id: str
    ticker: str
    user_id: str
    indicator: str
    operator: str
    threshold: float
    status: str
    note: str
    last_triggered_at: str | None
    created_at: str | None


def _get_service(request: Request) -> IndicatorAlertService:
    svc = getattr(request.app.state, "indicator_alert_service", None)
    if svc is None:
        raise HTTPException(503, "IndicatorAlertService nao inicializado")
    return svc


def _to_response(alert: Any) -> IndicatorAlertResponse:
    return IndicatorAlertResponse(
        alert_id=alert.alert_id,
        ticker=alert.ticker,
        user_id=alert.user_id,
        indicator=alert.condition.indicator,
        operator=alert.condition.operator,
        threshold=alert.condition.threshold,
        status=alert.status,
        note=alert.note,
        last_triggered_at=(
            alert.last_triggered_at.isoformat() if alert.last_triggered_at else None
        ),
        created_at=(alert.created_at.isoformat() if alert.created_at else None),
    )


@router.post("", status_code=201, response_model=IndicatorAlertResponse)
async def create_indicator_alert(
    body: CreateIndicatorAlertRequest,
    request: Request,
) -> IndicatorAlertResponse:
    """
    Cria um alerta de indicador Fintz.

    Exemplos:
      - ROE > 15%:           indicator=ROE, operator=gt, threshold=15
      - DividendYield >= 6%: indicator=DividendYield, operator=gte, threshold=6
      - P_L < 10:            indicator=P_L, operator=lt, threshold=10
      - Divida/PL <= 1.5:    indicator=DividaLiquida_PatrimonioLiquido, operator=lte, threshold=1.5
    """
    svc = _get_service(request)
    try:
        alert = await svc.create(
            ticker=body.ticker,
            indicator=body.indicator,
            operator=body.operator,
            threshold=body.threshold,
            user_id=body.user_id,
            note=body.note,
        )
        return _to_response(alert)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("indicator_alert.create_error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc


@router.get("", response_model=list[IndicatorAlertResponse])
async def list_indicator_alerts(
    request: Request,
    user_id: str = Query(default="user-demo"),
) -> list[IndicatorAlertResponse]:
    """Lista alertas de indicadores do usuario."""
    svc = _get_service(request)
    alerts = await svc.list_by_user(user_id)
    return [_to_response(a) for a in alerts]


@router.delete("/{alert_id}", status_code=204)
async def cancel_indicator_alert(
    alert_id: str,
    request: Request,
    user_id: str = Query(default="user-demo"),
) -> None:
    """Cancela um alerta de indicador."""
    svc = _get_service(request)
    cancelled = await svc.cancel(alert_id, user_id)
    if not cancelled:
        raise HTTPException(404, f"Alerta {alert_id!r} nao encontrado")


@router.get("/fields", summary="Indicadores suportados para alertas")
async def get_indicator_fields() -> dict[str, Any]:
    """Lista os indicadores disponíveis para criacao de alertas."""
    return {
        "indicators": [{"key": k, "description": v} for k, v in SUPPORTED_INDICATORS.items()],
        "operators": {
            "gt": "maior que (>)",
            "lt": "menor que (<)",
            "gte": "maior ou igual (>=)",
            "lte": "menor ou igual (<=)",
        },
        "nota": (
            "Indicadores de % (ROE, DividendYield, Margens) usam escala percentual. "
            "Ex: threshold=15 significa 15%, nao 0.15."
        ),
    }


@router.post("/evaluate", summary="Dispara avaliacao manual de todos os alertas")
async def evaluate_now(request: Request) -> dict[str, Any]:
    """
    Avalia todos os alertas de indicadores ativos contra os dados mais recentes do Fintz.
    Util para debug e teste. Em producao, a avaliacao roda periodicamente.
    """
    svc = _get_service(request)
    try:
        triggered = await svc.evaluate_all()
        return {
            "status": "ok",
            "triggered": triggered,
            "evaluated_at": __import__("datetime")
            .datetime.now(__import__("datetime").timezone.utc)
            .isoformat(),
        }
    except Exception as exc:
        logger.exception("indicator_alert.evaluate_error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc
