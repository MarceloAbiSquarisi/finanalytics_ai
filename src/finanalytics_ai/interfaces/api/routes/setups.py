"""
Rotas de setups intraday automaticos.

GET  /api/v1/setups/scan          -- escaneia tickers por setups ativos
POST /api/v1/setups/scan          -- idem com body JSON
GET  /api/v1/setups/disponíveis   -- lista setups suportados

Exemplos:
  GET /api/v1/setups/scan?tickers=PETR4,VALE3&setups=setup_91,pin_bar
  GET /api/v1/setups/scan?tickers=PETR4&timeframe=diario
  POST /api/v1/setups/scan  {"tickers":["PETR4"],"setups":["setup_91"]}
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
import structlog

from finanalytics_ai.application.services.intraday_setup_service import (
    AVAILABLE_SETUPS,
    IntradaySetupService,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/setups", tags=["Setups Intraday"])


class SetupScanRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=1, max_length=20)
    setups: list[str] | None = Field(
        default=None,
        description="None = todos. Ex: ['setup_91', 'pin_bar']",
    )
    timeframe: str = Field(
        default="diario",
        description="5min | 15min | 60min | diario",
    )
    notify: bool = Field(
        default=True,
        description="Publicar alertas no SSE stream",
    )


def _get_service(request: Request) -> IntradaySetupService:
    svc = getattr(request.app.state, "intraday_setup_service", None)
    if svc is None:
        raise HTTPException(503, "IntradaySetupService nao inicializado")
    return svc


@router.post("/scan", summary="Escaneia tickers buscando setups ativos")
async def scan_setups_post(
    body: SetupScanRequest,
    request: Request,
) -> dict[str, Any]:
    """
    Detecta setups de price action e tecnico nos tickers informados.

    Analisa a ultima barra disponivel e retorna alertas BUY/SELL
    para cada setup ativo. Alertas novos sao publicados no SSE stream.
    """
    svc = _get_service(request)
    try:
        result = await svc.scan(
            tickers=[t.upper().strip() for t in body.tickers],
            setups=body.setups,
            timeframe=body.timeframe,
            notify=body.notify,
        )
        return result.to_dict()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("setups.scan_error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc


@router.get("/scan", summary="Escaneia via query string")
async def scan_setups_get(
    request: Request,
    tickers: str = Query(..., description="Comma-separated: PETR4,VALE3"),
    setups: str | None = Query(None, description="Comma-separated: setup_91,pin_bar"),
    timeframe: str = Query("diario", description="5min|15min|60min|diario"),
    notify: bool = Query(True),
) -> dict[str, Any]:
    """Escaneia setups via GET para facilitar uso no frontend/browser."""
    body = SetupScanRequest(
        tickers=[t.strip().upper() for t in tickers.split(",") if t.strip()],
        setups=[s.strip() for s in setups.split(",") if s.strip()] if setups else None,
        timeframe=timeframe,
        notify=notify,
    )
    return await scan_setups_post(body, request)


@router.get("/disponiveis", summary="Lista setups suportados")
async def get_available_setups() -> dict[str, Any]:
    """Lista todos os setups disponíveis para deteccao."""
    return {
        "setups": [{"key": k, "nome": v} for k, v in AVAILABLE_SETUPS.items()],
        "timeframes": {
            "5min": "Intraday 5 minutos (requer ProfitDLL)",
            "15min": "Intraday 15 minutos (requer ProfitDLL)",
            "60min": "Intraday 60 minutos",
            "diario": "Grafico diario (dados Fintz)",
        },
        "sinais": {
            "BUY": "Sinal de compra — setup formado na ultima barra",
            "SELL": "Sinal de venda — setup formado na ultima barra",
        },
        "nota": (
            "Timeframes 5min e 15min requerem ProfitDLL ativo. "
            "Diario usa dados da base local (Fintz)."
        ),
    }
