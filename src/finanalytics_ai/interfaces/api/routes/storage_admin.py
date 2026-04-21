"""
Storage admin routes.

POST /api/v1/storage/collect/historical   — inicia coleta histórica B3
POST /api/v1/storage/collect/macro        — coleta séries macroeconômicas
POST /api/v1/storage/collect/{ticker}     — coleta ticker individual
GET  /api/v1/storage/stats                — uso do disco e tickers disponíveis
GET  /api/v1/storage/cleanup              — remove intraday antigo (> 90d)
"""

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Query, Request
import structlog

from finanalytics_ai.config import get_settings
from finanalytics_ai.infrastructure.storage.data_storage_service import get_storage

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/storage", tags=["Storage Admin"])


def _get_historical_collector(request: Request) -> Any:
    svc = getattr(request.app.state, "historical_collector", None)
    if svc is None:
        from finanalytics_ai.infrastructure.storage.historical_collector import HistoricalCollector

        settings = get_settings()
        svc = HistoricalCollector(
            brapi_token=settings.brapi_token,
            brapi_base_url=str(settings.brapi_base_url),
            storage=get_storage(settings.data_dir),
        )
        request.app.state.historical_collector = svc
    return svc


def _get_macro_collector(request: Request) -> Any:
    svc = getattr(request.app.state, "macro_collector", None)
    if svc is None:
        from finanalytics_ai.infrastructure.storage.macro_collector import MacroCollector

        settings = get_settings()
        svc = MacroCollector(storage=get_storage(settings.data_dir))
        request.app.state.macro_collector = svc
    return svc


@router.get("/stats")
async def storage_stats(request: Request) -> dict[str, Any]:
    """Retorna uso de disco e número de tickers disponíveis no armazenamento local."""
    settings = get_settings()
    storage = get_storage(settings.data_dir)
    return storage.stats()


@router.post("/collect/macro")
async def collect_macro(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    """Inicia coleta de séries macroeconômicas (BCB + Yahoo) em background."""
    collector = _get_macro_collector(request)
    background_tasks.add_task(collector.collect_all)
    return {"status": "started", "message": "Coleta macro iniciada em background"}


@router.post("/collect/historical")
async def collect_historical(
    request: Request,
    background_tasks: BackgroundTasks,
    force: Annotated[bool, Query(description="Recoleta mesmo tickers já presentes")] = False,
) -> dict[str, str]:
    """
    Inicia coleta histórica completa dos tickers B3 em background.
    Pode levar 15-30 minutos dependendo da quantidade de tickers.
    """
    collector = _get_historical_collector(request)
    background_tasks.add_task(collector.collect_all, force=force)
    return {
        "status": "started",
        "message": "Coleta histórica iniciada em background. Acompanhe via logs do container.",
    }


@router.post("/collect/{ticker}")
async def collect_ticker(
    ticker: str,
    request: Request,
    range_period: Annotated[str, Query()] = "5y",
    intraday: Annotated[bool, Query(description="Também coleta barras 1m intraday")] = False,
) -> dict[str, Any]:
    """Coleta e persiste histórico de um ticker específico (síncrono)."""
    collector = _get_historical_collector(request)
    rows = await collector.collect_ticker(ticker.upper(), range_period=range_period)
    result: dict[str, Any] = {"ticker": ticker.upper(), "rows_written": rows, "status": "ok"}

    if intraday:
        intraday_rows = await collector.collect_intraday(ticker.upper())
        result["intraday_rows"] = intraday_rows

    return result


@router.post("/cleanup")
async def cleanup_intraday(
    request: Request, keep_days: Annotated[int, Query(ge=7, le=365)] = 90
) -> dict[str, Any]:
    """Remove partições intraday mais antigas que keep_days."""
    settings = get_settings()
    storage = get_storage(settings.data_dir)
    deleted = storage.cleanup_old_intraday(keep_days=keep_days)
    return {"deleted_directories": deleted, "kept_days": keep_days}
